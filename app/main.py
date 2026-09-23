"""ReelForge Studio: small, self-hosted first slice."""
import hashlib
import json
import math
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, select

from app.db import ROOT, Session, config, engine
from app.models import (Asset, LoginSession, Membership, Project,
                        SystemSetting, User, Workflow, Workspace,
                        WorkspaceSetting)

SYSTEM_DEFAULTS = {
    "frontend_origin": "http://localhost:3000",
    "secure_cookies": False,
    "storage_dir": "instance/media",
    "trial_project_limit": 2,
}
WORKSPACE_DEFAULTS = {
    "default_language": "vi",
    "video_orientation": "vertical",
    "approval_required": True,
}


def setting(db, key):
    return json.loads(db.get(SystemSetting, key).value)


def workspace_settings(db, workspace_id):
    return {key: json.loads(db.get(WorkspaceSetting, (workspace_id, key)).value) for key in WORKSPACE_DEFAULTS}


def media_root(db):
    path = Path(setting(db, "storage_dir"))
    return path if path.is_absolute() else ROOT / path


if not inspect(engine).has_table("system_settings"):
    raise RuntimeError("Database is not migrated. Run: python -m alembic upgrade head")

with Session.begin() as db:
    for key, default in SYSTEM_DEFAULTS.items():
        if db.get(SystemSetting, key) is None:
            # Carry any pre-existing JSON configuration into the database once.
            db.add(SystemSetting(key=key, value=json.dumps(config.get(key, default))))
    for (workspace_id,) in db.execute(select(Workspace.id)):
        for key, default in WORKSPACE_DEFAULTS.items():
            if db.get(WorkspaceSetting, (workspace_id, key)) is None:
                db.add(WorkspaceSetting(workspace_id=workspace_id, key=key, value=json.dumps(default)))
app = FastAPI(title="ReelForge Studio")
MAX_UPLOAD = 100 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/ogg"}


class Credentials(BaseModel):
    email: str
    password: str


class NewProject(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    topic: str = Field(default="", max_length=3000)


class NewWorkflow(BaseModel):
    name: str = Field(min_length=1, max_length=150)


class GraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    type: str
    x: float
    y: float


class GraphEdge(BaseModel):
    source: str
    target: str


class WorkflowGraph(BaseModel):
    nodes: list[GraphNode] = Field(min_length=1, max_length=30)
    edges: list[GraphEdge] = Field(max_length=60)


NODE_TYPES = {"idea", "script", "scenes", "image", "video", "assets", "voice", "music", "subtitle", "render", "review", "publish"}


def default_graph():
    types = ["idea", "script", "scenes", "image", "voice", "render", "review", "publish"]
    coords = [(40, 210), (300, 210), (560, 210), (820, 50), (820, 370), (1090, 210), (1350, 210), (1610, 210)]
    nodes = [{"id": f"n{i}", "type": kind, "x": x, "y": y} for i, (kind, (x, y)) in enumerate(zip(types, coords))]
    links = [(0, 1), (1, 2), (2, 3), (2, 4), (3, 5), (4, 5), (5, 6), (6, 7)]
    return {"nodes": nodes, "edges": [{"source": f"n{a}", "target": f"n{b}"} for a, b in links]}


def parse_graph(definition):
    value = json.loads(definition)
    if isinstance(value, list):
        # Compatibility for workflows created before visual editing existed.
        nodes = [{"id": f"n{i}", "type": "assets" if kind == "assets" else kind, "x": i * 260, "y": 180} for i, kind in enumerate(value)]
        return {"nodes": nodes, "edges": [{"source": f"n{i}", "target": f"n{i+1}"} for i in range(len(nodes)-1)]}
    return value


def validate_graph(graph: WorkflowGraph):
    ids = [node.id for node in graph.nodes]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Duplicate node ID")
    if any(node.type not in NODE_TYPES or not math.isfinite(node.x) or not math.isfinite(node.y) or abs(node.x) > 100000 or abs(node.y) > 100000 for node in graph.nodes):
        raise HTTPException(422, "Invalid node type or position")
    links = [(edge.source, edge.target) for edge in graph.edges]
    if len(links) != len(set(links)) or any(a not in ids or b not in ids or a == b for a, b in links):
        raise HTTPException(422, "Invalid or duplicate connection")
    pending = {key: 0 for key in ids}
    onward = {key: [] for key in ids}
    for a, b in links:
        pending[b] += 1
        onward[a].append(b)
    queue = [key for key, degree in pending.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for target in onward[current]:
            pending[target] -= 1
            if pending[target] == 0:
                queue.append(target)
    if visited != len(ids):
        raise HTTPException(422, "Workflow cannot contain a cycle")


class WorkspaceSettingsInput(BaseModel):
    default_language: str = Field(pattern="^(vi|en|ja)$")
    video_orientation: str = Field(pattern="^(vertical|horizontal|square)$")
    approval_required: bool


class SystemSettingsInput(BaseModel):
    frontend_origin: str
    secure_cookies: bool
    trial_project_limit: int = Field(ge=1, le=10000)


def ident():
    return str(uuid.uuid4())


def hashed_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return secrets.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def authorize(request: Request, db):
    raw = request.cookies.get("rf_session", "")
    token = hashlib.sha256(raw.encode()).hexdigest()
    row = db.get(LoginSession, token)
    if not raw or not row or row.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Please sign in")
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(401, "Please sign in")
    return user


def workspace_for(request: Request, db):
    user = authorize(request, db)
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
    if not membership:
        raise HTTPException(403, "No workspace")
    return db.get(Workspace, membership.workspace_id)


def same_origin(request: Request):
    origin = request.headers.get("origin")
    with Session() as db:
        frontend_origin = setting(db, "frontend_origin")
    allowed = {str(request.base_url).rstrip("/"), frontend_origin.rstrip("/")}
    if origin and origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Invalid origin")


def public_project(p):
    return {"id": p.id, "title": p.title, "topic": p.topic, "status": p.status}


@app.get("/")
def index():
    return {"app": "ReelForge Studio API", "ui": "Run the Next.js frontend on port 3000", "docs": "/docs"}


@app.get("/api/status")
def status():
    with Session() as db:
        return {"setup_required": db.scalar(select(func.count()).select_from(User)) == 0}


@app.post("/api/setup")
def setup(data: Credentials, request: Request, response: Response):
    same_origin(request)
    email = data.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(data.password) < 12:
        raise HTTPException(400, "Use a valid email and a password of at least 12 characters")
    with Session.begin() as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise HTTPException(409, "Setup already completed")
        user = User(id=ident(), email=email, password_hash=hashed_password(data.password), is_admin=True)
        ws = Workspace(id=ident(), name="My Studio", owner_id=user.id)
        db.add(user)
        db.flush()
        db.add(ws)
        db.flush()
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role="owner"))
        db.add_all(WorkspaceSetting(workspace_id=ws.id, key=key, value=json.dumps(value)) for key, value in WORKSPACE_DEFAULTS.items())
    return login(data, request, response)


@app.post("/api/login")
def login(data: Credentials, request: Request, response: Response):
    same_origin(request)
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
        if not user or not check_password(data.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(48)
        db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    with Session() as db:
        secure_cookies = setting(db, "secure_cookies")
    response.set_cookie("rf_session", token, httponly=True, samesite="strict", secure=secure_cookies, max_age=604800)
    return {"email": user.email}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    same_origin(request)
    with Session.begin() as db:
        raw = request.cookies.get("rf_session")
        session = db.get(LoginSession, hashlib.sha256(raw.encode()).hexdigest()) if raw else None
        if session:
            db.delete(session)
    response.delete_cookie("rf_session")
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        projects = db.scalars(select(Project).where(Project.workspace_id == ws.id).order_by(Project.created_at.desc())).all()
        assets = db.scalars(select(Asset).where(Asset.workspace_id == ws.id).order_by(Asset.created_at.desc())).all()
        workflows = db.scalars(select(Workflow).where(Workflow.workspace_id == ws.id)).all()
        user = authorize(request, db)
        trial_limit = setting(db, "trial_project_limit")
        return {"workspace": {"id": ws.id, "name": ws.name, "plan": ws.plan}, "is_admin": user.is_admin, "projects": [public_project(p) for p in projects], "assets": [{"id": a.id, "filename": a.filename, "bytes": a.bytes, "content_type": a.content_type} for a in assets], "workflows": [{"id": w.id, "name": w.name, "graph": parse_graph(w.definition)} for w in workflows], "limits": {"projects": trial_limit if ws.plan == "trial" else None}}


@app.get("/api/settings")
def get_settings(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        user = authorize(request, db)
        system = None
        if user.is_admin:
            system = {key: setting(db, key) for key in SYSTEM_DEFAULTS}
        return {"workspace": workspace_settings(db, ws.id), "system": system}


@app.put("/api/settings/workspace")
def update_workspace_settings(data: WorkspaceSettingsInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        membership = db.get(Membership, (authorize(request, db).id, ws.id))
        if membership.role != "owner":
            raise HTTPException(403, "Workspace owner required")
        for key, value in data.model_dump().items():
            db.get(WorkspaceSetting, (ws.id, key)).value = json.dumps(value)
        return {"workspace": data.model_dump()}


@app.put("/api/settings/system")
def update_system_settings(data: SystemSettingsInput, request: Request):
    same_origin(request)
    origin = data.frontend_origin.rstrip("/")
    if not re.fullmatch(r"https?://[^/\s]+", origin):
        raise HTTPException(400, "Use a valid frontend origin (scheme and host only)")
    if data.secure_cookies and not origin.startswith("https://"):
        raise HTTPException(400, "Secure cookies require HTTPS frontend origin")
    with Session.begin() as db:
        if not authorize(request, db).is_admin:
            raise HTTPException(403, "System admin required")
        for key, value in {**data.model_dump(), "frontend_origin": origin}.items():
            db.get(SystemSetting, key).value = json.dumps(value)
        return {"system": {key: setting(db, key) for key in SYSTEM_DEFAULTS}}


@app.post("/api/projects", status_code=201)
def create_project(data: NewProject, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        count = db.scalar(select(func.count()).select_from(Project).where(Project.workspace_id == ws.id))
        if ws.plan == "trial" and count >= setting(db, "trial_project_limit"):
            raise HTTPException(403, "Trial project limit reached")
        project = Project(id=ident(), workspace_id=ws.id, title=data.title.strip(), topic=data.topic.strip())
        if not project.title:
            raise HTTPException(400, "Title required")
        db.add(project)
        return public_project(project)


@app.post("/api/workflows", status_code=201)
def create_workflow(data: NewWorkflow, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        workflow = Workflow(id=ident(), workspace_id=ws.id, name=data.name.strip(), definition=json.dumps(default_graph()))
        if not workflow.name:
            raise HTTPException(400, "Name required")
        db.add(workflow)
        return {"id": workflow.id, "name": workflow.name, "graph": parse_graph(workflow.definition)}


@app.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, graph: WorkflowGraph, request: Request):
    same_origin(request)
    validate_graph(graph)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        membership = db.get(Membership, (authorize(request, db).id, ws.id))
        if membership.role != "owner":
            raise HTTPException(403, "Workspace owner required")
        workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws.id))
        if not workflow:
            raise HTTPException(404, "Workflow not found")
        workflow.definition = graph.model_dump_json()
        return {"id": workflow.id, "name": workflow.name, "graph": graph.model_dump()}


@app.post("/api/assets", status_code=201)
def upload_asset(request: Request, file: UploadFile = File(...)):
    same_origin(request)
    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Unsupported media type")
    with Session.begin() as db:
        ws = workspace_for(request, db)
        asset_id = ident()
        filename = Path(file.filename or "upload").name[:255]
        target = media_root(db) / ws.id / asset_id
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with target.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "File exceeds 100 MB")
                    out.write(chunk)
            asset = Asset(id=asset_id, workspace_id=ws.id, filename=filename, bytes=size, content_type=content_type)
            db.add(asset)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return {"id": asset_id, "filename": filename, "bytes": size}


@app.get("/api/assets/{asset_id}")
def download_asset(asset_id: str, request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == ws.id))
        if not asset:
            raise HTTPException(404, "Asset not found")
        return FileResponse(media_root(db) / ws.id / asset.id, media_type=asset.content_type, filename=asset.filename)
