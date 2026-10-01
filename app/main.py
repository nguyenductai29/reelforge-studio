"""ReelForge Studio: small, self-hosted first slice."""
from contextlib import asynccontextmanager
import hashlib
import json
import math
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import case, func, inspect, select, update
from sqlalchemy.exc import IntegrityError

from app.db import ROOT, config, engine, Session
from app.body_limit import MULTIPART_OVERHEAD_BYTES, RequestBodyLimitMiddleware
from app.models import User, LoginSession, Workspace, Membership, Project, Asset, Workflow, SystemSetting, WorkspaceSetting, Plan, Subscription, PaymentOrder, CreditAccount, CreditLedger, UsageEvent, AITool, WorkflowRun, WorkflowRunStep, WorkflowJob
from app import (auth_security, billing, heartbeat, jobs, media_maintenance, payments, publications, reconciliation,
                 run_summary, sources, usage)
from app.models import CreditReconciliation
# Re-exported: existing callers import video_provider_config_issue from app.main.
from app.providers.catalog import video_provider_config_issue  # noqa: F401
from app.publishers import channel_oauth, google_oauth
from app.publishers.youtube import UPLOAD_SCOPE as YOUTUBE_UPLOAD_SCOPE
from app.runtime_env import start_process
from app.workflow import (ExecutionContext, RunOptions, RunRequestError, default_executor, default_registry,
                          parse_graph)
from app.workflow.config import TOOL, ConfigError, check_assets, check_tools
from app.workflow.templates import TEMPLATES, describe_templates, template_graph
from app.workflow.nodes import ReviewNodeHandler
from app.workflow.results import produced_asset_ids
from app.subtitles import CONTENT_TYPES as SUBTITLE_TYPES
from app.workflow.ports import DATA_TYPES, describe_node_types, edge_problems, normalize_edges


SYSTEM_DEFAULTS = {
    "frontend_origin": "http://localhost:3000",
    "secure_cookies": False,
    "storage_dir": "instance/media",
    "trial_project_limit": 2,
    "registration_enabled": True,
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
@asynccontextmanager
async def lifespan(_app):
    # The API loads the same runtime environment file as the workers (app/runtime_env.py).
    start_process("api")
    yield


app = FastAPI(title="ReelForge Studio", lifespan=lifespan)
MAX_UPLOAD = 100 * 1024 * 1024


def upload_preflight(scope):
    """Authorize uploads before their request bodies are buffered or parsed."""
    request = Request(scope)
    same_origin(request)
    with Session() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)


app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=MAX_UPLOAD + MULTIPART_OVERHEAD_BYTES,
                   preflight=upload_preflight)
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/ogg",
                 *sources.DOCUMENT_TYPES}
# Browsers often send no type (or a generic one) for these; the extension decides.
DOCUMENT_EXTENSIONS = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
                       ".srt": "application/x-subrip", ".vtt": "text/vtt"}
LOOSE_DOCUMENT_TYPES = {"", "application/octet-stream", "text/plain", "text/markdown", "text/x-markdown",
                        "application/x-subrip", "text/srt", "text/vtt", "text/x-vtt"}


def upload_content_type(content_type: str, filename: str) -> str:
    """The stored type of an upload: a text document by its extension, anything else as sent."""
    extension = Path(filename or "").suffix.lower()
    if extension in DOCUMENT_EXTENSIONS and content_type in LOOSE_DOCUMENT_TYPES:
        return DOCUMENT_EXTENSIONS[extension]
    return content_type


def document_matches(content_type: str, path: Path) -> bool:
    """UTF-8 text without NUL bytes, at most 2 MB; subtitles must have cue timings (VTT its header)."""
    if path.stat().st_size > sources.MAX_DOCUMENT_BYTES:
        return False
    try:
        text = sources.decode_text(path.read_bytes())
    except sources.SourceError:
        return False
    if content_type == "text/vtt":
        return text.lstrip().startswith("WEBVTT") and "-->" in text
    if content_type == "application/x-subrip":
        return "-->" in text
    return bool(text.strip())


def media_signature_matches(content_type: str, path: Path) -> bool:
    if content_type in sources.DOCUMENT_TYPES:
        return document_matches(content_type, path)
    with path.open("rb") as source:
        header = source.read(16)
    signatures = {
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
        "video/mp4": len(header) >= 12 and header[4:8] == b"ftyp",
        "video/webm": header.startswith(b"\x1a\x45\xdf\xa3"),
        "audio/mpeg": header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xff and header[1] & 0xe0 == 0xe0),
        "audio/wav": header.startswith(b"RIFF") and header[8:12] == b"WAVE",
        "audio/ogg": header.startswith(b"OggS"),
    }
    return signatures.get(content_type, False)


def workspace_media_quota():
    try:
        quota = int(os.environ.get("WORKSPACE_MEDIA_QUOTA_BYTES", str(1024 * 1024 * 1024)))
    except ValueError as exc:
        raise RuntimeError("WORKSPACE_MEDIA_QUOTA_BYTES must be a positive integer") from exc
    if quota <= 0:
        raise RuntimeError("WORKSPACE_MEDIA_QUOTA_BYTES must be a positive integer")
    return quota


class Credentials(BaseModel):
    email: str
    password: str


class NewAccount(Credentials):
    workspace_name: str = Field(min_length=1, max_length=100)
    plan_code: str = "trial"


class RegisterInput(Credentials):
    workspace_name: str = Field(min_length=1, max_length=100)


class PlanInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    project_limit: int | None = Field(default=None, ge=1)
    workflow_limit: int | None = Field(default=None, ge=1)
    monthly_credits: int = Field(default=0, ge=0)
    is_active: bool = True
    price_vnd: int | None = Field(default=None, ge=2000, le=2_000_000_000)


class CheckoutInput(BaseModel):
    plan_code: str = Field(pattern="^(standard|pro)$")


class ReconciliationInput(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class CreditAdjustment(BaseModel):
    delta: int = Field(ge=-1000000, le=1000000)
    reason: str = Field(min_length=3, max_length=80)


class SubscriptionInput(BaseModel):
    plan_code: str
    status: str = Field(pattern="^(active|paused|canceled)$")
    ends_at: datetime | None = None


class ActiveInput(BaseModel):
    is_active: bool


class NewProject(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    topic: str = Field(default="", max_length=3000)


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, max_length=150)
    topic: str | None = Field(default=None, max_length=3000)


class AIToolInput(BaseModel):
    task: str = Field(pattern="^(script|image|video|voice|music|transcription)$")
    provider: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9._-]+$")
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._:/@-]+$")
    is_enabled: bool = True


class StartWorkflowRun(BaseModel):
    project_id: str
    prompt: str | None = Field(default=None, max_length=3000)
    tool_id: str | None = None


class YouTubeCallbackInput(BaseModel):
    state: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=4096)


class YouTubePublicationInput(BaseModel):
    run_id: str
    # Omitted: the run's final render, else its clip (publications.final_video).
    asset_id: str | None = None
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=60)
    privacy_status: Literal["private", "unlisted", "public"] = "private"


class YouTubeRetryInput(BaseModel):
    """Corrections for a failed upload that never sent media; omitted values stay as they were."""
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=60)
    privacy_status: Literal["private", "unlisted", "public"] | None = None


class NewWorkflow(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    # A starter template (app/workflow/templates.py); omitted: idea → video → review.
    template: str | None = Field(default=None, max_length=40)


class GraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    type: str
    x: float
    y: float
    # Display name chosen in the editor; execution only depends on type.
    label: str | None = Field(default=None, max_length=80)
    # Per-node settings, validated by the node type's handler (e.g. a text node's prompt or language).
    config: dict[str, Any] | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    # Port names, named as in React Flow. Edges saved before ports existed have none and
    # connect default ports (app/workflow/ports.py).
    sourceHandle: str | None = Field(default=None, max_length=40,
                                     validation_alias=AliasChoices("sourceHandle", "source_handle"))
    targetHandle: str | None = Field(default=None, max_length=40,
                                     validation_alias=AliasChoices("targetHandle", "target_handle"))


class WorkflowGraph(BaseModel):
    nodes: list[GraphNode] = Field(min_length=1, max_length=30)
    edges: list[GraphEdge] = Field(max_length=60)


# A saved graph may contain any type with a registered handler (app/workflow/registry.py).
NODE_TYPES = default_registry.node_types


def workflow_graph(definition: str) -> dict:
    """A stored definition with every edge naming the ports it connects; old edges get their default ports."""
    return normalize_edges(parse_graph(definition), default_registry)


def default_graph():
    types = ["idea", "video", "review"]
    coords = [(40, 210), (340, 210), (640, 210)]
    nodes = [{"id": f"n{i}", "type": kind, "x": x, "y": y} for i, (kind, (x, y)) in enumerate(zip(types, coords))]
    links = [(0, 1), (1, 2)]
    return {"nodes": nodes, "edges": [{"source": f"n{a}", "target": f"n{b}"} for a, b in links]}


def config_error(node_id: str, exc: ConfigError) -> HTTPException:
    """422 whose detail carries a stable code, e.g. {"code": "invalid_language", "field": "language", ...}."""
    return HTTPException(422, {**exc.as_dict(), "node_id": node_id,
                               "message": f"Invalid settings for step {node_id}: {exc.message}"})


def validate_graph(graph: WorkflowGraph, *, editing: bool = True):
    """Reject malformed graphs.

    Port names and node settings are checked only when editing. A stored
    snapshot is bound tolerantly, and a node whose settings became invalid is
    blocked when the run reaches it (app/workflow/executor.py).
    """
    ids = [node.id for node in graph.nodes]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Duplicate node ID")
    if any(node.type not in NODE_TYPES or not math.isfinite(node.x) or not math.isfinite(node.y) or abs(node.x) > 100000 or abs(node.y) > 100000 for node in graph.nodes):
        raise HTTPException(422, "Invalid node type or position")
    for node in graph.nodes if editing else ():
        try:
            default_registry.resolve(node.type).validate_config(node.config)
        except ConfigError as exc:
            raise config_error(node.id, exc) from exc
    links = [(edge.source, edge.target) for edge in graph.edges]
    # Two nodes may be joined more than once, through different ports.
    wiring = [(edge.source, edge.sourceHandle, edge.target, edge.targetHandle) for edge in graph.edges]
    if len(wiring) != len(set(wiring)) or any(a not in ids or b not in ids or a == b for a, b in links):
        raise HTTPException(422, "Invalid or duplicate connection")
    if editing and (problems := edge_problems(graph.model_dump(), default_registry)):
        raise HTTPException(422, f"Invalid connection: {problems[0]}")
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
    registration_enabled: bool


def ident():
    return str(uuid.uuid4())


def provision_workspace(db, user, name, plan_code):
    """Insert parents first so PostgreSQL foreign keys are valid at each flush."""
    db.add(user)
    db.flush()
    ws = Workspace(id=ident(), name=name, owner_id=user.id, plan=plan_code)
    db.add(ws)
    db.flush()
    db.add(CreditAccount(workspace_id=ws.id, balance=0))
    db.add(Subscription(workspace_id=ws.id, plan_code=plan_code, status="active", starts_at=datetime.now(timezone.utc)))
    db.add(Membership(user_id=user.id, workspace_id=ws.id, role="owner"))
    db.add_all(WorkspaceSetting(workspace_id=ws.id, key=key, value=json.dumps(value)) for key, value in WORKSPACE_DEFAULTS.items())
    return ws


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
    if not user or not user.is_active:
        raise HTTPException(401, "Please sign in")
    return user


def workspace_for(request: Request, db):
    user = authorize(request, db)
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
    if not membership:
        raise HTTPException(403, "No workspace")
    return db.get(Workspace, membership.workspace_id)


def admin_for(request: Request, db):
    user = authorize(request, db)
    if not user.is_admin:
        raise HTTPException(403, "System admin required")
    return user


def active_plan(db, ws):
    subscription = db.get(Subscription, ws.id)
    if not subscription or subscription.status != "active" or (subscription.ends_at and subscription.ends_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)):
        raise HTTPException(403, "Workspace subscription is inactive")
    plan = db.get(Plan, subscription.plan_code)
    if not plan or not plan.is_active:
        raise HTTPException(403, "Plan is unavailable")
    return plan


def effective_status(subscription):
    if subscription.status == "active" and subscription.ends_at and subscription.ends_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        return "expired"
    return subscription.status


def enforce_limit(db, ws, table, limit_name):
    limit = getattr(active_plan(db, ws), limit_name)
    if limit is not None and db.scalar(select(func.count()).select_from(table).where(table.workspace_id == ws.id)) >= limit:
        raise HTTPException(403, f"{limit_name.replace('_', ' ').capitalize()} reached")


def same_origin(request: Request):
    origin = request.headers.get("origin")
    with Session() as db:
        frontend_origin = setting(db, "frontend_origin")
    allowed = {str(request.base_url).rstrip("/"), frontend_origin.rstrip("/")}
    if origin and origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Invalid origin")


def public_project(p):
    return {"id": p.id, "title": p.title, "topic": p.topic, "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None}


@app.get("/")
def index():
    return {"app": "ReelForge Studio API", "ui": "Run the Next.js frontend on port 3000", "docs": "/docs"}


@app.get("/api/status")
def status():
    with Session() as db:
        return {"setup_required": db.scalar(select(func.count()).select_from(User)) == 0,
                "registration_enabled": setting(db, "registration_enabled")}


@app.post("/api/setup")
def setup(data: Credentials, request: Request, response: Response):
    same_origin(request)
    email = data.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(data.password) < 12:
        raise HTTPException(400, "Use a valid email and a password of at least 12 characters")
    with Session.begin() as db:
        auth_security.lock_initial_setup(db)
        if db.scalar(select(func.count()).select_from(User)):
            raise HTTPException(409, "Setup already completed")
        user = User(id=ident(), email=email, password_hash=hashed_password(data.password), is_admin=True)
        provision_workspace(db, user, "My Studio", "trial")
    return login(data, request, response)


@app.post("/api/register", status_code=201)
def register(data: RegisterInput, request: Request, response: Response):
    same_origin(request)
    email, name = data.email.strip().lower(), data.workspace_name.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(data.password) < 12 or not name:
        raise HTTPException(400, "Valid email, workspace name and password of at least 12 characters required")
    try:
        with Session.begin() as db:
            if not db.scalar(select(func.count()).select_from(User)):
                raise HTTPException(403, "Create the first studio as administrator")
            if not setting(db, "registration_enabled"):
                raise HTTPException(403, "Registration is closed")
            if db.scalar(select(User.id).where(User.email == email)):
                raise HTTPException(409, "Email already exists")
            plan = db.get(Plan, "trial")
            if not plan or not plan.is_active:
                raise HTTPException(403, "Trial plan unavailable")
            user = User(id=ident(), email=email, password_hash=hashed_password(data.password), is_admin=False, is_active=True)
            provision_workspace(db, user, name, "trial")
    except IntegrityError as exc:
        raise HTTPException(409, "Email already exists") from exc
    return login(data, request, response)


@app.post("/api/login")
def login(data: Credentials, request: Request, response: Response):
    same_origin(request)
    identifier = f"{data.email.strip().lower()}|{request.client.host if request.client else 'unknown'}"
    blocked = False
    invalid = False
    token = None
    user = None
    with Session.begin() as db:
        if not auth_security.check_login_allowed(db, identifier):
            blocked = True
        else:
            user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
            if not user or not user.is_active or not check_password(data.password, user.password_hash):
                auth_security.record_login_failure(db, identifier)
                invalid = True
            else:
                auth_security.clear_login_failures(db, identifier)
                token = secrets.token_urlsafe(48)
                db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    if blocked:
        raise HTTPException(429, "Too many login attempts; try again later")
    if invalid:
        raise HTTPException(401, "Invalid credentials")
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
        subscription = db.get(Subscription, ws.id)
        plan = db.get(Plan, subscription.plan_code) if subscription else None
        return {"workspace": {"id": ws.id, "name": ws.name, "plan": plan.code if plan else ws.plan, "subscription_status": effective_status(subscription) if subscription else "unavailable"}, "user": {"email": user.email}, "is_admin": user.is_admin, "projects": [public_project(p) for p in projects], "assets": [{"id": a.id, "filename": a.filename, "bytes": a.bytes, "content_type": a.content_type, "project_id": a.project_id, "run_id": a.run_id, "created_at": a.created_at.isoformat() if a.created_at else None} for a in assets], "workflows": [{"id": w.id, "name": w.name, "graph": workflow_graph(w.definition)} for w in workflows], "limits": {"projects": plan.project_limit if plan else None, "workflows": plan.workflow_limit if plan else None},
                "storage": {"used_bytes": sum(a.bytes or 0 for a in assets), "quota_bytes": workspace_media_quota()}}


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


# Friendly names of the tasks a workspace can choose a default model for, and the AI tool task each maps to.
DEFAULT_MODEL_TASKS = {"text": "script", "image": "image", "video": "video", "voice": "voice",
                       "transcription": "transcription"}
_TOOL_ID_PATTERN = r"^[A-Za-z0-9-]{1,64}$"


class DefaultModelsInput(BaseModel):
    text: str | None = Field(default=None, pattern=_TOOL_ID_PATTERN)
    image: str | None = Field(default=None, pattern=_TOOL_ID_PATTERN)
    video: str | None = Field(default=None, pattern=_TOOL_ID_PATTERN)
    voice: str | None = Field(default=None, pattern=_TOOL_ID_PATTERN)
    transcription: str | None = Field(default=None, pattern=_TOOL_ID_PATTERN)


def default_models(db, workspace_id):
    row = db.get(WorkspaceSetting, (workspace_id, "default_models"))
    stored = json.loads(row.value) if row else {}
    stored = stored if isinstance(stored, dict) else {}
    return {name: stored.get(task) if isinstance(stored.get(task), str) else None
            for name, task in DEFAULT_MODEL_TASKS.items()}


@app.get("/api/settings/default-models")
def get_default_models(request: Request):
    """The model each task uses when a step names none; a step's own choice always wins."""
    with Session() as db:
        ws = workspace_for(request, db)
        return {"default_models": default_models(db, ws.id)}


@app.put("/api/settings/default-models")
def update_default_models(data: DefaultModelsInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = ai_tool_owner(request, db)
        chosen = data.model_dump()
        stored = {}
        for name, tool_id in chosen.items():
            if tool_id is None:
                continue
            tool = db.scalar(select(AITool).where(AITool.id == tool_id, AITool.workspace_id == ws.id))
            if tool is None or tool.task != DEFAULT_MODEL_TASKS[name] or not tool.is_enabled:
                raise HTTPException(422, {"code": "invalid_default_model", "field": name,
                                          "message": f"{name} must be an enabled {name} model of this workspace"})
            stored[DEFAULT_MODEL_TASKS[name]] = tool_id
        row = db.get(WorkspaceSetting, (ws.id, "default_models"))
        if row is None:
            db.add(WorkspaceSetting(workspace_id=ws.id, key="default_models", value=json.dumps(stored)))
        else:
            row.value = json.dumps(stored)
        return {"default_models": chosen}


@app.get("/api/storage")
def storage_overview(request: Request):
    """How much media the workspace stores, by kind, against its quota."""
    with Session() as db:
        ws = workspace_for(request, db)
        by_type = media_maintenance.usage_by_type(db, ws.id)
        return {"used_bytes": sum(by_type.values()), "quota_bytes": workspace_media_quota(), "by_type": by_type}


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
        db.get(Plan, "trial").project_limit = data.trial_project_limit
        return {"system": {key: setting(db, key) for key in SYSTEM_DEFAULTS}}


def plan_data(plan):
    return {"code": plan.code, "name": plan.name, "project_limit": plan.project_limit,
            "workflow_limit": plan.workflow_limit, "monthly_credits": plan.monthly_credits,
            "is_active": plan.is_active, "price_vnd": plan.price_vnd}


def order_data(order):
    return {"id": order.id, "plan_code": order.plan_code, "provider": order.provider,
            "amount_vnd": order.amount_vnd, "status": order.status,
            "created_at": order.created_at.isoformat(), "paid_at": order.paid_at.isoformat() if order.paid_at else None}


@app.get("/api/billing")
def billing_overview(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        subscription = db.get(Subscription, ws.id)
        orders = db.scalars(select(PaymentOrder).where(PaymentOrder.workspace_id == ws.id).order_by(PaymentOrder.created_at.desc()).limit(30)).all()
        return {"plans": [plan_data(p) for p in db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.code))],
                "subscription": {"plan_code": subscription.plan_code, "status": effective_status(subscription),
                                 "ends_at": subscription.ends_at.isoformat() if subscription.ends_at else None},
                "orders": [order_data(o) for o in orders], "payos_ready": billing.configured()}


@app.post("/api/billing/checkout", status_code=201)
def billing_checkout(data: CheckoutInput, request: Request):
    same_origin(request)
    if not billing.configured():
        raise HTTPException(503, "payOS is not configured")
    with Session.begin() as db:
        ws = workspace_for(request, db)
        if db.get(Membership, (authorize(request, db).id, ws.id)).role != "owner":
            raise HTTPException(403, "Workspace owner required")
        subscription = db.get(Subscription, ws.id)
        if not subscription or subscription.status not in ("active", "expired"):
            raise HTTPException(403, "Subscription is inactive")
        if ("trial", "standard", "pro").index(data.plan_code) < ("trial", "standard", "pro").index(subscription.plan_code) or (data.plan_code == subscription.plan_code and subscription.plan_code == "trial"):
            raise HTTPException(400, "Choose a higher plan or renew the current paid plan")
        plan = db.get(Plan, data.plan_code)
        if not plan or not plan.is_active or not plan.price_vnd:
            raise HTTPException(400, "Plan price is not configured")
        origin = setting(db, "frontend_origin")
        order = PaymentOrder(id=ident(), workspace_id=ws.id, plan_code=plan.code,
                             provider="payos", order_code=secrets.randbelow(8_000_000_000_000) + 1_000_000_000_000,
                             amount_vnd=plan.price_vnd, credits_award=plan.monthly_credits,
                             status="pending", created_at=datetime.now(timezone.utc))
        db.add(order)
        db.flush()
        order_id, code, amount, plan_code = order.id, order.order_code, order.amount_vnd, plan.code
    try:
        link = billing.create_link(code, amount, plan_code, origin)
    except Exception as exc:
        with Session.begin() as db:
            row = db.get(PaymentOrder, order_id)
            if row and row.status == "pending":
                row.status = "failed"
        raise HTTPException(502, "Unable to create payment link") from exc
    with Session.begin() as db:
        row = db.get(PaymentOrder, order_id)
        row.checkout_url = link
    return {"order_id": order_id, "checkout_url": link}


@app.post("/api/webhooks/payos")
async def payos_webhook(request: Request):
    body = await request.body()
    if len(body) > 65536 or not billing.configured():
        raise HTTPException(400, "Invalid webhook")
    try:
        payload = json.loads(body)
        verified = billing.verify_webhook(body)
    except Exception as exc:
        raise HTTPException(400, "Invalid webhook") from exc
    if not payload.get("success") or payload.get("code") != "00":
        return {"ok": True}
    if getattr(verified, "currency", None) != "VND":
        raise HTTPException(400, "Payment currency mismatch")
    try:
        with Session.begin() as db:
            payments.apply_paid(db, verified.order_code, int(verified.amount), str(getattr(verified, "reference", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/billing/orders/{order_id}/refresh")
def refresh_payment_order(order_id: str, request: Request):
    same_origin(request)
    if not billing.configured():
        raise HTTPException(503, "payOS is not configured")
    with Session() as db:
        ws = workspace_for(request, db)
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.workspace_id == ws.id))
        if not order:
            raise HTTPException(404, "Order not found")
        if order.status in ("paid", "paid_unapplied"):
            return order_data(order)
        code, amount = order.order_code, order.amount_vnd
    try:
        provider_order = billing.get_payment(code)
    except Exception as exc:
        raise HTTPException(502, "Unable to check payment with payOS") from exc
    if int(provider_order.order_code) != code or int(provider_order.amount) != amount:
        raise HTTPException(502, "Provider order mismatch")
    with Session.begin() as db:
        row = db.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.workspace_id == ws.id).with_for_update())
        if provider_order.status == "PAID":
            if int(provider_order.amount_paid) < amount:
                raise HTTPException(502, "Payment amount mismatch")
            payments.apply_paid(db, code, amount, str(provider_order.id))
        elif provider_order.status in ("CANCELLED", "EXPIRED") and row.status == "pending":
            row.status = provider_order.status.lower()
        return order_data(row)


@app.get("/api/usage")
def usage_overview(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        account = db.get(CreditAccount, ws.id)
        ledger = db.scalars(select(CreditLedger).where(CreditLedger.workspace_id == ws.id).order_by(CreditLedger.created_at.desc()).limit(50)).all()
        events = db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == ws.id).order_by(UsageEvent.created_at.desc()).limit(50)).all()
        return {"balance": account.balance if account else 0,
                "ledger": [{"id": row.id, "delta": row.delta, "reason": row.reason, "created_at": row.created_at.isoformat()} for row in ledger],
                "events": [{"id": row.id, "tool": row.tool, "units": row.units, "credits": row.credits, "created_at": row.created_at.isoformat()} for row in events]}


@app.post("/api/admin/workspaces/{workspace_id}/credits")
def adjust_credits(workspace_id: str, data: CreditAdjustment, request: Request):
    same_origin(request)
    with Session.begin() as db:
        admin_for(request, db)
        if not db.get(Workspace, workspace_id):
            raise HTTPException(404, "Workspace not found")
        try:
            balance = usage.post_credit(db, workspace_id, data.delta, "admin: " + data.reason, "admin:" + ident())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"balance": balance}


@app.get("/api/admin/reconciliation")
def list_credit_reconciliation(request: Request, status: Literal["pending", "resolved"] = "pending",
                               limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    with Session() as db:
        admin_for(request, db)
        return reconciliation.list_items(db, status=status, limit=limit, offset=offset)


def resolve_credit_reconciliation(data, request, decision, *, step_id=None, job_id=None):
    same_origin(request)
    try:
        with Session.begin() as db:
            admin = admin_for(request, db)
            admin_id = admin.id
            item, changed = reconciliation.reconcile(db, step_id=step_id, job_id=job_id, decision=decision,
                                                      admin_user_id=admin_id, note=data.note)
    except reconciliation.ReconciliationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if changed:
        reconciliation.log_resolution(item, admin_id)
    return item


@app.post("/api/admin/reconciliation/jobs/{job_id}/confirm-charge")
def confirm_job_reconciliation_charge(job_id: str, data: ReconciliationInput, request: Request):
    return resolve_credit_reconciliation(data, request, "confirmed_charge", job_id=job_id)


@app.post("/api/admin/reconciliation/jobs/{job_id}/refund")
def refund_job_reconciliation(job_id: str, data: ReconciliationInput, request: Request):
    return resolve_credit_reconciliation(data, request, "refunded", job_id=job_id)


# The step routes resolve a step's only paid job; a step with several jobs needs the job routes.
@app.post("/api/admin/reconciliation/{step_id}/confirm-charge")
def confirm_reconciliation_charge(step_id: str, data: ReconciliationInput, request: Request):
    return resolve_credit_reconciliation(data, request, "confirmed_charge", step_id=step_id)


@app.post("/api/admin/reconciliation/{step_id}/refund")
def refund_reconciliation(step_id: str, data: ReconciliationInput, request: Request):
    return resolve_credit_reconciliation(data, request, "refunded", step_id=step_id)


@app.get("/api/admin")
def admin_overview(request: Request):
    with Session() as db:
        admin_for(request, db)
        users = db.scalars(select(User).order_by(User.email)).all()
        workspaces = db.scalars(select(Workspace).order_by(Workspace.created_at.desc())).all()
        plans = db.scalars(select(Plan).order_by(Plan.code)).all()
        return {"plans": [plan_data(p) for p in plans],
                "users": [{"id": u.id, "email": u.email, "is_active": u.is_active, "is_admin": u.is_admin} for u in users],
                "workspaces": [{"id": w.id, "name": w.name, "owner_id": w.owner_id,
                                "plan_code": s.plan_code if s else None,
                                "status": effective_status(s) if s else "unavailable",
                                "credits": db.get(CreditAccount, w.id).balance if db.get(CreditAccount, w.id) else 0,
                                "ends_at": s.ends_at.isoformat() if s and s.ends_at else None}
                               for w in workspaces for s in [db.get(Subscription, w.id)]]}


ADMIN_JOB_QUEUES = ("text", "image", "video", "voice", "render", "source", "publish")


def public_job(job):
    """Operator view of a queued job: identifiers, state and timing only; never its payload."""
    error = job.last_error or None
    if error:
        error = re.sub(r"https?://\S+", "[URL omitted]", error)[:200]
    return {"id": job.id, "queue": job.logical_key.split(":", 1)[0], "channel": job.logical_key.split(":")[1]
            if job.logical_key.startswith("publish:") else None, "state": job.state,
            "attempt_count": job.attempt_count, "worker_id": job.worker_id, "workspace_id": job.workspace_id,
            "run_id": job.run_id, "step_id": job.step_id, "last_error": error,
            "created_at": _iso(job.created_at), "updated_at": _iso(job.updated_at),
            "available_at": _iso(job.available_at), "lease_expires_at": _iso(job.lease_expires_at),
            "finished_at": _iso(job.finished_at)}


@app.get("/api/admin/workers")
def admin_workers(request: Request):
    """Each worker's last heartbeat: ok, stale (stopped or stuck), error, or missing (never started)."""
    with Session() as db:
        admin_for(request, db)
        return {"workers": heartbeat.worker_health(db), "stale_after_seconds": heartbeat.STALE_SECONDS}


@app.get("/api/admin/jobs")
def admin_jobs(request: Request, state: Literal["queued", "leased", "succeeded", "failed"] | None = None,
               queue: Literal["text", "image", "video", "voice", "render", "source", "publish"] | None = None,
               limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    """Recent jobs of every workspace with safe fields, counts per queue and state, and a stuck-work audit."""
    with Session() as db:
        admin_for(request, db)
        query = select(WorkflowJob)
        if state:
            query = query.where(WorkflowJob.state == state)
        if queue:
            query = query.where(WorkflowJob.logical_key.startswith(f"{queue}:"))
        rows = db.scalars(query.order_by(WorkflowJob.updated_at.desc(), WorkflowJob.id).limit(limit).offset(offset))
        queue_name = case(*((WorkflowJob.logical_key.startswith(f"{name}:"), name) for name in ADMIN_JOB_QUEUES),
                          else_="other")
        counts = {}
        for key, job_state, count in db.execute(select(queue_name, WorkflowJob.state, func.count())
                                                .group_by(queue_name, WorkflowJob.state)):
            counts.setdefault(key, {})[job_state] = count
        return {"jobs": [public_job(job) for job in rows], "counts": counts, "stuck": jobs.stuck_jobs(db)}


@app.get("/api/admin/storage")
def admin_storage(request: Request):
    with Session() as db:
        admin_for(request, db)
        return {"workspaces": media_maintenance.storage_usage(db), "quota_bytes": workspace_media_quota()}


@app.post("/api/admin/accounts", status_code=201)
def create_account(data: NewAccount, request: Request):
    same_origin(request)
    email, name = data.email.strip().lower(), data.workspace_name.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(data.password) < 12 or not name:
        raise HTTPException(400, "Valid email, workspace name and password of at least 12 characters required")
    with Session.begin() as db:
        admin_for(request, db)
        if db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, "Email already exists")
        plan = db.get(Plan, data.plan_code)
        if not plan or not plan.is_active:
            raise HTTPException(400, "Plan unavailable")
        user = User(id=ident(), email=email, password_hash=hashed_password(data.password), is_admin=False, is_active=True)
        ws = provision_workspace(db, user, name, plan.code)
        return {"user_id": user.id, "workspace_id": ws.id}


@app.put("/api/admin/users/{user_id}")
def set_user_active(user_id: str, data: ActiveInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        admin = admin_for(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        if user.id == admin.id and not data.is_active:
            raise HTTPException(400, "Cannot disable your own account")
        if user.is_admin and not data.is_active and db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True))) <= 1:
            raise HTTPException(400, "Cannot disable the last system admin")
        user.is_active = data.is_active
        if not data.is_active:
            for session in db.scalars(select(LoginSession).where(LoginSession.user_id == user_id)):
                db.delete(session)
        return {"id": user.id, "is_active": user.is_active}


@app.put("/api/admin/plans/{code}")
def update_plan(code: str, data: PlanInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        admin_for(request, db)
        plan = db.get(Plan, code)
        if not plan:
            raise HTTPException(404, "Plan not found")
        if code == "trial" and data.project_limit is None:
            raise HTTPException(400, "Trial requires a project limit")
        if code == "trial" and data.price_vnd is not None:
            raise HTTPException(400, "Trial cannot have a checkout price")
        if not data.is_active and db.scalar(select(func.count()).select_from(Subscription).where(Subscription.plan_code == code, Subscription.status == "active")):
            raise HTTPException(409, "Move active subscriptions before disabling this plan")
        for field, value in data.model_dump().items():
            setattr(plan, field, value)
        if code == "trial":
            db.get(SystemSetting, "trial_project_limit").value = json.dumps(data.project_limit)
        return plan_data(plan)


@app.put("/api/admin/workspaces/{workspace_id}/subscription")
def update_subscription(workspace_id: str, data: SubscriptionInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        admin_for(request, db)
        ws, plan = db.get(Workspace, workspace_id), db.get(Plan, data.plan_code)
        if not ws:
            raise HTTPException(404, "Workspace not found")
        if not plan or not plan.is_active:
            raise HTTPException(400, "Plan unavailable")
        subscription = db.get(Subscription, workspace_id)
        if not subscription:
            raise HTTPException(404, "Subscription not found")
        if data.ends_at and data.ends_at.tzinfo is None:
            raise HTTPException(400, "End date must include timezone")
        subscription.plan_code = plan.code
        subscription.status = data.status
        subscription.starts_at = datetime.now(timezone.utc)
        subscription.ends_at = data.ends_at
        ws.plan = plan.code  # Keep the legacy workspace column synchronized.
        return {"workspace_id": ws.id, "plan_code": plan.code, "status": data.status}


@app.post("/api/projects", status_code=201)
def create_project(data: NewProject, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        enforce_limit(db, ws, Project, "project_limit")
        project = Project(id=ident(), workspace_id=ws.id, title=data.title.strip(), topic=data.topic.strip())
        if not project.title:
            raise HTTPException(400, "Title required")
        db.add(project)
        return public_project(project)


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, data: ProjectPatch, request: Request):
    same_origin(request)
    if not data.model_fields_set or any(getattr(data, field) is None for field in data.model_fields_set):
        raise HTTPException(422, "Provide a title or topic")
    with Session.begin() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)
        project = db.scalar(select(Project).where(Project.id == project_id, Project.workspace_id == ws.id))
        if not project:
            raise HTTPException(404, "Project not found")
        if "title" in data.model_fields_set:
            project.title = data.title.strip()
            if not project.title:
                raise HTTPException(400, "Title required")
        if "topic" in data.model_fields_set:
            project.topic = data.topic.strip()
        return public_project(project)


def public_ai_tool(tool):
    return {"id": tool.id, "task": tool.task, "provider": tool.provider, "model": tool.model, "is_enabled": tool.is_enabled}


def ai_tool_owner(request, db):
    ws = workspace_for(request, db)
    active_plan(db, ws)
    member = db.get(Membership, (authorize(request, db).id, ws.id))
    if not member or member.role != "owner":
        raise HTTPException(403, "Workspace owner required")
    return ws


@app.get("/api/ai-tools")
def list_ai_tools(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        return [public_ai_tool(t) for t in db.scalars(select(AITool).where(AITool.workspace_id == ws.id).order_by(AITool.created_at, AITool.id))]


@app.post("/api/ai-tools", status_code=201)
def create_ai_tool(data: AIToolInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = ai_tool_owner(request, db)
        tool = AITool(id=ident(), workspace_id=ws.id, **data.model_dump())
        db.add(tool)
        return public_ai_tool(tool)


@app.put("/api/ai-tools/{tool_id}")
def update_ai_tool(tool_id: str, data: AIToolInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = ai_tool_owner(request, db)
        tool = db.scalar(select(AITool).where(AITool.id == tool_id, AITool.workspace_id == ws.id))
        if not tool:
            raise HTTPException(404, "AI tool not found")
        for key, value in data.model_dump().items():
            setattr(tool, key, value)
        return public_ai_tool(tool)


@app.delete("/api/ai-tools/{tool_id}", status_code=204)
def delete_ai_tool(tool_id: str, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = ai_tool_owner(request, db)
        tool = db.scalar(select(AITool).where(AITool.id == tool_id, AITool.workspace_id == ws.id))
        if not tool:
            raise HTTPException(404, "AI tool not found")
        db.delete(tool)
    return Response(status_code=204)


def youtube_owner(request: Request, db):
    ws = workspace_for(request, db)
    membership = db.get(Membership, (authorize(request, db).id, ws.id))
    if not membership or membership.role != "owner":
        raise HTTPException(403, "Workspace owner required")
    return ws


def youtube_oauth_error(exc: google_oauth.OAuthError):
    status_code = 503 if exc.code == "not_configured" else 403 if exc.code == "forbidden" else 502 if exc.code in {"google_unavailable", "google_token_error", "invalid_response"} else 400
    raise HTTPException(status_code, str(exc)) from exc


def google_http_client():
    return httpx.Client(timeout=10, follow_redirects=False)


@app.get("/api/youtube/connection")
def youtube_connection(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        connected = google_oauth.connection_status(db, workspace_id=ws.id)
        return {"connected": connected is not None, "expires_at": connected.expires_at.isoformat() if connected else None,
                "scope": connected.scope if connected else None}


@app.get("/api/youtube/authorization")
def youtube_authorization(request: Request):
    same_origin(request)
    try:
        config = google_oauth.GoogleOAuthConfig.from_environment()
        with Session.begin() as db:
            ws = youtube_owner(request, db)
            started = google_oauth.begin_authorization(db, config, workspace_id=ws.id,
                                                       user_id=authorize(request, db).id)
        return {"url": started.url, "expires_at": started.expires_at.isoformat()}
    except google_oauth.OAuthError as exc:
        youtube_oauth_error(exc)


@app.post("/api/youtube/callback")
def youtube_callback(data: YouTubeCallbackInput, request: Request):
    same_origin(request)
    try:
        config = google_oauth.GoogleOAuthConfig.from_environment()
        with Session() as db, google_http_client() as client:
            user = authorize(request, db)
            result = google_oauth.complete_authorization(db, config, state=data.state,
                code=data.code, current_user_id=user.id, client=client)
        return {"connected": True, "workspace_id": result.workspace_id,
                "expires_at": result.expires_at.isoformat()}
    except google_oauth.OAuthError as exc:
        youtube_oauth_error(exc)


@app.delete("/api/youtube/connection", status_code=204)
def youtube_disconnect(request: Request):
    same_origin(request)
    with Session() as db:
        ws = youtube_owner(request, db)
        google_oauth.disconnect(db, workspace_id=ws.id)
    return Response(status_code=204)


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def public_publication(row):
    succeeded = row.state == "succeeded" and bool(row.remote_id)
    youtube_url = f"https://www.youtube.com/watch?v={row.remote_id}" if succeeded and row.channel == "youtube" else None
    # A TikTok inbox draft has no public page until the creator posts it in the app.
    url = youtube_url or (f"https://www.facebook.com/reel/{row.remote_id}"
                          if succeeded and row.channel == "facebook" and row.remote_status == "published" else None)
    open_state = row.state in publications.CANCELLABLE_STATES and row.upload_session_ciphertext is None \
        and row.remote_id is None
    return {"id": row.id, "run_id": row.run_id, "asset_id": row.asset_id, "channel": row.channel,
            "title": row.title, "description": row.description, "tags": publications.publication_tags(row),
            "privacy_status": row.privacy_status, "state": row.state,
            "remote_id": row.remote_id, "remote_status": row.remote_status, "remote_privacy": row.remote_privacy,
            "youtube_url": youtube_url, "url": url,
            "last_error": row.last_error,
            "can_retry": publications.can_retry_publication(row),
            "can_cancel": open_state, "can_reschedule": open_state,
            "scheduled_for": _iso(row.scheduled_for), "published_at": _iso(row.published_at),
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}


def metadata_error(exc: publications.MetadataError):
    raise HTTPException(422, {"code": exc.code, "field": exc.field, "message": str(exc)}) from exc


@app.get("/api/youtube/publications")
def list_youtube_publications(request: Request, run_id: str | None = None):
    with Session() as db:
        ws = workspace_for(request, db)
        query = select(publications.Publication).where(publications.Publication.workspace_id == ws.id,
                    publications.Publication.channel == "youtube")
        if run_id:
            query = query.where(publications.Publication.run_id == run_id)
        rows = db.scalars(query.order_by(publications.Publication.created_at.desc()).limit(100))
        return [public_publication(row) for row in rows]


@app.post("/api/youtube/publications", status_code=201)
def create_youtube_publication(data: YouTubePublicationInput, request: Request, response: Response):
    same_origin(request)
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        active_plan(db, ws)
        if google_oauth.connection_status(db, workspace_id=ws.id) is None:
            raise HTTPException(409, "Connect YouTube before uploading")
        try:
            publications.validate_metadata(data.title, data.description, data.tags, data.privacy_status)
        except publications.MetadataError as exc:
            metadata_error(exc)
        existing = db.scalar(select(publications.Publication).where(
            publications.Publication.workspace_id == ws.id,
            publications.Publication.run_id == data.run_id,
            publications.Publication.channel == "youtube"))
        asset_id = data.asset_id
        if asset_id is None:
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == data.run_id, WorkflowRun.workspace_id == ws.id))
            video = publications.final_video(db, run) if run else None
            if video is None:
                raise HTTPException(409, "The run has no finished video to publish")
            asset_id = video.id
        try:
            row = publications.queue_publication(db, workspace_id=ws.id, run_id=data.run_id,
                asset_id=asset_id, channel="youtube", title=data.title.strip(),
                description=data.description, tags=data.tags, privacy_status=data.privacy_status)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if existing:
            response.status_code = 200
        return public_publication(row)


@app.post("/api/youtube/publications/{publication_id}/retry", status_code=202)
def retry_youtube_publication(publication_id: str, request: Request, data: YouTubeRetryInput | None = None):
    """Queue only a new upload job; the run's script, media and render are reused, never generated again."""
    same_origin(request)
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        active_plan(db, ws)
        changes = data.model_dump(exclude_none=True) if data else {}
        try:
            row = publications.retry_publication(db, workspace_id=ws.id, publication_id=publication_id,
                                                 metadata=changes or None)
        except publications.MetadataError as exc:
            metadata_error(exc)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return public_publication(row)


@app.get("/api/youtube/publications/{publication_id}")
def get_youtube_publication(publication_id: str, request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        row = db.scalar(select(publications.Publication).where(
            publications.Publication.id == publication_id,
            publications.Publication.workspace_id == ws.id,
            publications.Publication.channel == "youtube"))
        if not row:
            raise HTTPException(404, "Publication not found")
        return public_publication(row)


class ChannelCallbackInput(BaseModel):
    state: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=4096)


class FacebookPageInput(BaseModel):
    page_id: str = Field(pattern=r"^[0-9]{1,40}$")


class PublicationTarget(BaseModel):
    channel: Literal["youtube", "tiktok", "facebook"]
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=60)
    privacy_status: Literal["private", "unlisted", "public"] = "private"


class PublicationsInput(BaseModel):
    run_id: str
    # Omitted: the run's final render, else its clip (publications.final_video).
    asset_id: str | None = None
    # UTC; omitted (or already passed): upload now.
    scheduled_for: datetime | None = None
    targets: list[PublicationTarget] = Field(min_length=1, max_length=3)


class RetryInput(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=60)
    privacy_status: Literal["private", "unlisted", "public"] | None = None


class ScheduleInput(BaseModel):
    scheduled_for: datetime | None = None


CHANNEL_NAMES = ("youtube", *channel_oauth.CHANNELS)


def channel_oauth_error(exc: channel_oauth.ChannelOAuthError):
    status_code = {"not_configured": 503, "forbidden": 403, "provider_unavailable": 502, "token_error": 502,
                   "invalid_response": 502, "no_pages": 409, "unknown_page": 409, "not_connected": 409,
                   "unsupported_channel": 404}.get(exc.code, 400)
    raise HTTPException(status_code, {"code": exc.code, "message": str(exc)}) from exc


def youtube_channel_status(db, workspace_id):
    result = {"channel": "youtube", "account_name": None, "connected_at": None, "reason": None}
    try:
        google_oauth.GoogleOAuthConfig.from_environment()
    except google_oauth.OAuthError:
        return {**result, "status": "configuration_required"}
    connection = db.get(google_oauth.YouTubeConnection, workspace_id)
    if connection is None:
        return {**result, "status": "not_connected"}
    result["connected_at"] = _iso(connection.connected_at)
    if YOUTUBE_UPLOAD_SCOPE not in (connection.scope or "").split():
        return {**result, "status": "authorization_required", "reason": "missing_scope"}
    return {**result, "status": "connected"}


def channel_statuses(db, workspace_id):
    return [youtube_channel_status(db, workspace_id),
            *(channel_oauth.status(db, channel, workspace_id) for channel in channel_oauth.CHANNELS)]


@app.get("/api/channels")
def list_channels(request: Request):
    """Every publishing channel with ``connected``, ``not_connected``, ``configuration_required`` or
    ``authorization_required``; never a token."""
    with Session() as db:
        ws = workspace_for(request, db)
        return {"channels": channel_statuses(db, ws.id)}


@app.get("/api/channels/{channel}/authorization")
def channel_authorization(channel: str, request: Request):
    if channel == "youtube":
        return youtube_authorization(request)
    same_origin(request)
    if channel not in channel_oauth.CHANNELS:
        raise HTTPException(404, "Unknown channel")
    try:
        config = channel_oauth.ChannelConfig.from_environment(channel)
        with Session.begin() as db:
            ws = youtube_owner(request, db)
            started = channel_oauth.begin_authorization(db, config, workspace_id=ws.id,
                                                        user_id=authorize(request, db).id)
        return {"url": started.url, "expires_at": started.expires_at.isoformat()}
    except channel_oauth.ChannelOAuthError as exc:
        channel_oauth_error(exc)


@app.post("/api/channels/{channel}/callback")
def channel_callback(channel: str, data: ChannelCallbackInput, request: Request):
    if channel == "youtube":
        return youtube_callback(YouTubeCallbackInput(state=data.state, code=data.code), request)
    same_origin(request)
    if channel not in channel_oauth.CHANNELS:
        raise HTTPException(404, "Unknown channel")
    try:
        config = channel_oauth.ChannelConfig.from_environment(channel)
        with Session() as db, google_http_client() as client:
            user = authorize(request, db)
            return channel_oauth.complete_authorization(db, config, state=data.state, code=data.code,
                                                        current_user_id=user.id, client=client)
    except channel_oauth.ChannelOAuthError as exc:
        channel_oauth_error(exc)


@app.put("/api/channels/facebook/page")
def choose_facebook_page(data: FacebookPageInput, request: Request):
    """Publish Reels to another of the Pages listed at authorization; queued uploads for the old Page stop."""
    same_origin(request)
    try:
        config = channel_oauth.ChannelConfig.from_environment("facebook")
        with Session.begin() as db:
            ws = youtube_owner(request, db)
            channel_oauth.select_facebook_page(db, config, workspace_id=ws.id, page_id=data.page_id)
        with Session() as db:
            return channel_oauth.status(db, "facebook", ws.id)
    except channel_oauth.ChannelOAuthError as exc:
        channel_oauth_error(exc)


@app.delete("/api/channels/{channel}", status_code=204)
def disconnect_channel(channel: str, request: Request):
    if channel == "youtube":
        return youtube_disconnect(request)
    same_origin(request)
    if channel not in channel_oauth.CHANNELS:
        raise HTTPException(404, "Unknown channel")
    with Session() as db:
        ws = youtube_owner(request, db)
        channel_oauth.disconnect(db, channel, workspace_id=ws.id)
    return Response(status_code=204)


def owned_publication(db, ws, publication_id):
    row = db.scalar(select(publications.Publication).where(publications.Publication.id == publication_id,
                                                           publications.Publication.workspace_id == ws.id))
    if not row:
        raise HTTPException(404, "Publication not found")
    return row


@app.get("/api/publications")
def list_publications(request: Request, run_id: str | None = None,
                      channel: Literal["youtube", "tiktok", "facebook"] | None = None,
                      start: datetime | None = None, end: datetime | None = None):
    """Publications of every channel, newest first; ``start``/``end`` (UTC) select a calendar range by the
    scheduled time, else the publishing time, else the creation time."""
    with Session() as db:
        ws = workspace_for(request, db)
        Publication = publications.Publication
        when = func.coalesce(Publication.scheduled_for, Publication.published_at, Publication.created_at)
        query = select(Publication).where(Publication.workspace_id == ws.id)
        if run_id:
            query = query.where(Publication.run_id == run_id)
        if channel:
            query = query.where(Publication.channel == channel)
        if start:
            query = query.where(when >= start)
        if end:
            query = query.where(when < end)
        rows = db.scalars(query.order_by(when.desc(), Publication.id).limit(300))
        return {"publications": [public_publication(row) for row in rows]}


@app.post("/api/publications", status_code=201)
def create_publications(data: PublicationsInput, request: Request, response: Response):
    """Publish one approved run to several channels: one publication and one independent upload job each.

    Every target is validated first (422 names the channel and field), then each
    channel must be connected (409). ``scheduled_for`` stores them as scheduled;
    the scheduler worker queues each upload when the time comes.
    """
    same_origin(request)
    channels = [target.channel for target in data.targets]
    if len(set(channels)) != len(channels):
        raise HTTPException(422, {"code": "duplicate_channel", "message": "Each channel can be chosen once"})
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        active_plan(db, ws)
        for target in data.targets:
            try:
                publications.validate_channel_metadata(target.channel, target.title, target.description,
                                                       target.tags, target.privacy_status)
            except publications.MetadataError as exc:
                raise HTTPException(422, {"code": exc.code, "field": exc.field, "channel": target.channel,
                                          "message": str(exc)}) from exc
        try:
            publications.schedule_time(data.scheduled_for)
        except publications.MetadataError as exc:
            metadata_error(exc)
        for target in data.targets:
            try:
                publications.connection_generation(db, target.channel, ws.id)
            except ValueError as exc:
                raise HTTPException(409, {"code": "channel_not_connected", "channel": target.channel,
                                          "message": str(exc)}) from exc
        asset_id = data.asset_id
        if asset_id is None:
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == data.run_id, WorkflowRun.workspace_id == ws.id))
            video = publications.final_video(db, run) if run else None
            if video is None:
                raise HTTPException(409, "The run has no finished video to publish")
            asset_id = video.id
        existing = db.scalar(select(func.count()).select_from(publications.Publication).where(
            publications.Publication.workspace_id == ws.id, publications.Publication.run_id == data.run_id,
            publications.Publication.channel.in_(channels), publications.Publication.state != "cancelled"))
        rows = []
        for target in data.targets:
            try:
                rows.append(publications.queue_publication(
                    db, workspace_id=ws.id, run_id=data.run_id, asset_id=asset_id, channel=target.channel,
                    title=target.title.strip(), description=target.description, tags=target.tags,
                    privacy_status=target.privacy_status, scheduled_for=data.scheduled_for))
            except ValueError as exc:
                raise HTTPException(409, {"code": "publication_rejected", "channel": target.channel,
                                          "message": str(exc)}) from exc
        if existing == len(channels):
            response.status_code = 200
        return {"publications": [public_publication(row) for row in rows]}


@app.get("/api/publications/{publication_id}")
def get_publication(publication_id: str, request: Request):
    with Session() as db:
        return public_publication(owned_publication(db, workspace_for(request, db), publication_id))


@app.post("/api/publications/{publication_id}/retry", status_code=202)
def retry_any_publication(publication_id: str, request: Request, data: RetryInput | None = None):
    """Queue only a new upload job for a failed publication that never sent media (any channel)."""
    same_origin(request)
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        active_plan(db, ws)
        changes = data.model_dump(exclude_none=True) if data else {}
        try:
            row = publications.retry_publication(db, workspace_id=ws.id, publication_id=publication_id,
                                                 metadata=changes or None, channel=None)
        except publications.MetadataError as exc:
            metadata_error(exc)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return public_publication(row)


@app.post("/api/publications/{publication_id}/cancel")
def cancel_publication(publication_id: str, request: Request):
    """Cancel before the upload starts; afterwards 409 ``upload_started``."""
    same_origin(request)
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        try:
            row = publications.cancel_publication(db, workspace_id=ws.id, publication_id=publication_id)
        except LookupError as exc:
            raise HTTPException(404, "Publication not found") from exc
        except ValueError as exc:
            raise HTTPException(409, {"code": "upload_started", "message": "The upload has already started"}) from exc
        return public_publication(row)


@app.put("/api/publications/{publication_id}/schedule")
def reschedule_publication(publication_id: str, data: ScheduleInput, request: Request):
    """Move a publication to another UTC time before its upload starts; ``null`` means as soon as possible."""
    same_origin(request)
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        active_plan(db, ws)
        try:
            row = publications.reschedule_publication(db, workspace_id=ws.id, publication_id=publication_id,
                                                      scheduled_for=data.scheduled_for)
        except LookupError as exc:
            raise HTTPException(404, "Publication not found") from exc
        except publications.MetadataError as exc:
            metadata_error(exc)
        except ValueError as exc:
            raise HTTPException(409, {"code": "upload_started", "message": "The upload has already started"}) from exc
        return public_publication(row)


@app.get("/api/workflows/{workflow_id}/readiness")
def workflow_readiness(workflow_id: str, request: Request, tool_id: str | None = None):
    with Session() as db:
        ws = workspace_for(request, db)
        workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws.id))
        if not workflow:
            raise HTTPException(404, "Workflow not found")
        context = ExecutionContext(db, workspace=ws, graph=parse_graph(workflow.definition),
                                   options=RunOptions(tool_id=tool_id))
        checks = default_executor.readiness(context)
        steps = [{"node_id": node["id"], "task": node["type"], "status": check.status, "detail": check.detail,
                  "code": check.code, "field": check.field, "credits": check.credits,
                  "tool": resolved_tool(context, node)}
                 for node, check in checks]
        required = sum(check.credits for _, check in checks)
        return {"workflow_id": workflow.id,
                "runnable": all(step["status"] in {"ready", "configured"} for step in steps)
                and context.credit_balance >= required,
                "credits_required": required, "credits_available": context.credit_balance, "steps": steps}


def resolved_tool(context, node):
    """The AI model a step will use: its own setting, else the first enabled model for its task."""
    handler = default_registry.resolve(node["type"])
    field = next((field for field in handler.config_fields if field.type == TOOL), None)
    if field is None:
        return None
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    chosen = config.get(field.key) if isinstance(config.get(field.key), str) else None
    tool = context.find_tool(field.task, field.providers, chosen or (context.options.tool_id if field.task == "video" else None))
    return {"id": tool.id, "provider": tool.provider, "model": tool.model,
            "chosen": bool(chosen)} if tool else None


def public_step_output(step):
    if not step.output:
        return None
    output = json.loads(step.output)
    if isinstance(output, dict):
        for key in ("submission", "error_count", "video_url", "provider_job"):
            output.pop(key, None)
        # Multi-job steps (app/media_jobs.py) keep the same private facts per job.
        if isinstance(output.get("jobs"), dict):
            output["jobs"] = {job_id: {key: value for key, value in record.items()
                                       if key not in ("submission", "error_count", "provider_job")}
                              for job_id, record in output["jobs"].items() if isinstance(record, dict)}
    return output


def public_run(run, steps=None):
    result = {"id": run.id, "workflow_id": run.workflow_id, "project_id": run.project_id,
              "retry_of_id": run.retry_of_id, "status": run.status,
              "created_at": run.created_at.isoformat(),
              "finished_at": run.finished_at.isoformat() if run.finished_at else None}
    if steps is not None:
        result["steps"] = [{"node_id": step.node_id, "node_type": step.node_type,
                            "status": step.status, "detail": step.detail,
                            "output": public_step_output(step)}
                           for step in steps]
    return result


def persist_run(db, ws, workflow, project, snapshot, retry_of_id=None, prompt_override=None, tool_id=None, frozen_video=None):
    # The graph, steps, credit hold and queued jobs are committed together, even if nodes block.
    validate_graph(WorkflowGraph.model_validate(parse_graph(snapshot)), editing=False)
    # The snapshot records which port each edge feeds, so the run's data flow is inspectable later.
    graph = workflow_graph(snapshot)
    now = datetime.now(timezone.utc)
    run = WorkflowRun(id=ident(), workspace_id=ws.id, workflow_id=workflow.id, project_id=project.id,
                      retry_of_id=retry_of_id, graph_snapshot=json.dumps(graph), status="running", created_at=now)
    db.add(run)
    db.flush()
    context = ExecutionContext(db, workspace=ws, project=project, run=run, graph=graph, now=now,
                               options=RunOptions(prompt_override=prompt_override, tool_id=tool_id,
                                                  frozen_video=frozen_video))
    try:
        progress = default_executor.start_run(context)
    except RunRequestError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return public_run(run, progress.steps)


@app.get("/api/workflows/{workflow_id}/runs")
def list_workflow_runs(workflow_id: str, request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws.id))
        if not workflow:
            raise HTTPException(404, "Workflow not found")
        runs = db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id,
            WorkflowRun.workspace_id == ws.id).order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()).limit(30))
        return [public_run(run) for run in runs]


@app.get("/api/workflow-runs")
def list_recent_runs(request: Request):
    """Latest runs across every workflow, for activity and project status views."""
    with Session() as db:
        ws = workspace_for(request, db)
        runs = db.scalars(select(WorkflowRun).where(WorkflowRun.workspace_id == ws.id)
                          .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()).limit(100))
        return [public_run(run) for run in runs]


@app.post("/api/workflows/{workflow_id}/runs", status_code=201)
def start_workflow_run(workflow_id: str, data: StartWorkflowRun, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)
        workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws.id))
        project = db.scalar(select(Project).where(Project.id == data.project_id, Project.workspace_id == ws.id))
        if not workflow or not project:
            raise HTTPException(404, "Workflow or project not found")
        return persist_run(db, ws, workflow, project, workflow.definition,
                           prompt_override=data.prompt, tool_id=data.tool_id)


@app.get("/api/workflow-runs/{run_id}")
def get_workflow_run(run_id: str, request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws.id))
        if not run:
            raise HTTPException(404, "Workflow run not found")
        steps = db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id).order_by(WorkflowRunStep.position))
        return public_run(run, list(steps))


@app.get("/api/workflow-runs/{run_id}/summary")
def get_workflow_run_summary(run_id: str, request: Request):
    """Progress, results, the final video, credits and publishing state of one run (app/run_summary.py)."""
    with Session() as db:
        ws = workspace_for(request, db)
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws.id))
        if not run:
            raise HTTPException(404, "Workflow run not found")
        summary = run_summary.summarize(db, run)
        publication = db.scalar(select(publications.Publication).where(
            publications.Publication.workspace_id == ws.id, publications.Publication.run_id == run.id,
            publications.Publication.channel == "youtube"))
        summary["publishing"]["publication"] = public_publication(publication) if publication else None
        summary["publishing"]["youtube_connected"] = google_oauth.connection_status(db, workspace_id=ws.id) is not None
        rows = db.scalars(select(publications.Publication).where(
            publications.Publication.workspace_id == ws.id, publications.Publication.run_id == run.id)
            .order_by(publications.Publication.created_at))
        summary["publishing"]["publications"] = [public_publication(row) for row in rows]
        summary["publishing"]["channels"] = channel_statuses(db, ws.id)
        return summary


@app.post("/api/workflow-runs/{run_id}/approve")
def approve_workflow_run(run_id: str, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        membership = db.get(Membership, (authorize(request, db).id, ws.id))
        if not membership or membership.role != "owner":
            raise HTTPException(403, "Workspace owner required")
        # Locked like every executor pass over a run, so a worker cannot advance it concurrently.
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws.id)
                        .with_for_update())
        if not run:
            raise HTTPException(404, "Workflow run not found")
        steps = list(db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id).order_by(WorkflowRunStep.position)))
        reviews = [step for step in steps if step.node_type == "review" and step.status == "awaiting_review"]
        # Any finished step that produced media (videos, including one per scene, or images) can be approved.
        if run.status != "awaiting_review" or not reviews or not any(
                step.status == "completed" and produced_asset_ids(json.loads(step.output) if step.output else None)
                for step in steps):
            raise HTTPException(409, "No finished video awaits review")
        now = datetime.now(timezone.utc)
        reviewer = authorize(request, db)
        for step in reviews:
            ReviewNodeHandler.approve(step, reviewer_id=reviewer.id, now=now)
        # Steps after the review can now run; the run settles as completed or blocked.
        progress = default_executor.advance_run(ExecutionContext.for_run(db, run, now=now))
        project = db.get(Project, run.project_id)
        project.status = "approved"
        return public_run(run, progress.steps)


@app.post("/api/workflow-runs/{run_id}/retry", status_code=201)
def retry_workflow_run(run_id: str, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)
        original = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws.id).with_for_update())
        if not original:
            raise HTTPException(404, "Workflow run not found")
        if original.status not in {"blocked", "failed"}:
            raise HTTPException(409, "Only blocked or failed runs can be retried")
        unresolved = db.scalar(select(WorkflowRunStep.id).where(
            WorkflowRunStep.run_id == original.id, WorkflowRunStep.status == "needs_attention"))
        confirmed = db.scalar(select(CreditReconciliation.step_id).join(
            WorkflowRunStep, CreditReconciliation.step_id == WorkflowRunStep.id).where(
            WorkflowRunStep.run_id == original.id, CreditReconciliation.decision == "confirmed_charge"))
        if unresolved or confirmed:
            raise HTTPException(409, "Resolve uncertain paid steps before starting another generation")
        refunded = db.scalar(select(CreditReconciliation.step_id).join(
            WorkflowRunStep, CreditReconciliation.step_id == WorkflowRunStep.id).where(
            WorkflowRunStep.run_id == original.id, CreditReconciliation.decision == "refunded"))
        # Subtitle files are made locally for free, so only generated media blocks the retry.
        if refunded and db.scalar(select(Asset.id).where(Asset.run_id == original.id,
                                                          Asset.content_type.notin_(SUBTITLE_TYPES))):
            raise HTTPException(409, "A reconciled run with generated media cannot be retried; start a new run")
        approved_review = db.scalar(select(WorkflowRunStep.id).where(
            WorkflowRunStep.run_id == original.id,
            WorkflowRunStep.node_type == "review",
            WorkflowRunStep.status == "completed"))
        if approved_review is not None:
            raise HTTPException(409, "An approved clip cannot be retried; start a new run for a new video")
        workflow = db.scalar(select(Workflow).where(Workflow.id == original.workflow_id, Workflow.workspace_id == ws.id))
        project = db.scalar(select(Project).where(Project.id == original.project_id, Project.workspace_id == ws.id))
        if not workflow or not project:
            raise HTTPException(404, "Workflow or project not found")
        # Only a single-clip video request is frozen; text jobs (keyed "text:") and clips made
        # one per scene are generated again from the current settings (docs/MULTI_SCENE_VIDEO.md).
        video_jobs = db.scalars(select(WorkflowJob).where(WorkflowJob.run_id == original.id,
                                                          WorkflowJob.logical_key.like("video:%"))
                                .order_by(WorkflowJob.created_at))
        original_job = next((job for job in video_jobs if job.payload.get("mode") in (None, "single")), None)
        return persist_run(db, ws, workflow, project, original.graph_snapshot,
                           retry_of_id=original.id, frozen_video=original_job.payload if original_job else None)


@app.post("/api/workflows", status_code=201)
def create_workflow(data: NewWorkflow, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        if data.template is not None and data.template not in TEMPLATES:
            raise HTTPException(422, {"code": "unknown_template", "message": "Unknown workflow template"})
        enforce_limit(db, ws, Workflow, "workflow_limit")
        if data.template:
            # Templates name no AI tool: each step uses the first enabled model for its task at run time.
            graph = WorkflowGraph.model_validate(template_graph(data.template))
            validate_graph(graph)
            definition = normalize_edges(graph.model_dump(), default_registry)
        else:
            definition = default_graph()
        workflow = Workflow(id=ident(), workspace_id=ws.id, name=data.name.strip(), definition=json.dumps(definition))
        if not workflow.name:
            raise HTTPException(400, "Name required")
        db.add(workflow)
        return {"id": workflow.id, "name": workflow.name, "graph": workflow_graph(workflow.definition)}


@app.get("/api/workflow-templates")
def workflow_templates(request: Request):
    """The starter workflows a new workflow can be created from."""
    with Session() as db:
        workspace_for(request, db)
    return {"templates": describe_templates()}


@app.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, graph: WorkflowGraph, request: Request):
    same_origin(request)
    validate_graph(graph)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)
        membership = db.get(Membership, (authorize(request, db).id, ws.id))
        if membership.role != "owner":
            raise HTTPException(403, "Workspace owner required")
        workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws.id))
        if not workflow:
            raise HTTPException(404, "Workflow not found")
        # Model settings must name one of this workspace's AI tools; keys never leave the server.
        tools = {tool.id: tool for tool in db.scalars(select(AITool).where(AITool.workspace_id == ws.id))}
        # Source files must be this workspace's own uploads (Phase 10).
        asset_ids = {node.config.get(field.key) for node in graph.nodes if isinstance(node.config, dict)
                     for field in default_registry.resolve(node.type).config_fields if field.type == "asset"}
        asset_ids = {value for value in asset_ids if isinstance(value, str)}
        assets = {asset.id: asset for asset in db.scalars(select(Asset).where(Asset.workspace_id == ws.id,
                                                                              Asset.id.in_(asset_ids)))} \
            if asset_ids else {}
        for node in graph.nodes:
            try:
                fields = default_registry.resolve(node.type).config_fields
                check_tools(fields, node.config, tools)
                check_assets(fields, node.config, assets)
            except ConfigError as exc:
                raise config_error(node.id, exc) from exc
        stored = normalize_edges(graph.model_dump(), default_registry)
        workflow.definition = json.dumps(stored)
        return {"id": workflow.id, "name": workflow.name, "graph": stored}


@app.get("/api/workflow-node-types")
def workflow_node_types(request: Request):
    """Each node type's typed ports (the canvas's handles) and settings (the inspector's fields)."""
    with Session() as db:
        workspace_for(request, db)
    return {"data_types": list(DATA_TYPES), "node_types": describe_node_types(default_registry)}


@app.post("/api/assets", status_code=201)
def upload_asset(request: Request, file: UploadFile = File(...)):
    same_origin(request)
    content_type = upload_content_type(file.content_type or "", file.filename or "")
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Unsupported media type")
    with Session.begin() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)
        # Serialize quota checks for simultaneous uploads in the same workspace.
        db.execute(update(Workspace).where(Workspace.id == ws.id).values(name=Workspace.name))
        stored_bytes = db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.workspace_id == ws.id))
        quota = workspace_media_quota()
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
                    if stored_bytes + size > quota:
                        raise HTTPException(413, "Workspace media quota reached")
                    out.write(chunk)
            if not media_signature_matches(content_type, target):
                raise HTTPException(415, "File contents do not match media type")
            asset = Asset(id=asset_id, workspace_id=ws.id, filename=filename, bytes=size, content_type=content_type)
            db.add(asset)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return {"id": asset_id, "filename": filename, "bytes": size, "content_type": content_type}


@app.get("/api/assets/{asset_id}")
def download_asset(asset_id: str, request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == ws.id))
        if not asset:
            raise HTTPException(404, "Asset not found")
        return FileResponse(media_root(db) / ws.id / asset.id, media_type=asset.content_type, filename=asset.filename)
