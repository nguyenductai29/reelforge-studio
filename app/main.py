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
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import func, inspect, select, update
from sqlalchemy.exc import IntegrityError

from app.db import ROOT, config, engine, Session
from app.body_limit import MULTIPART_OVERHEAD_BYTES, RequestBodyLimitMiddleware
from app.models import User, LoginSession, Workspace, Membership, Project, Asset, Workflow, SystemSetting, WorkspaceSetting, Plan, Subscription, PaymentOrder, CreditAccount, CreditLedger, UsageEvent, AITool, WorkflowRun, WorkflowRunStep, WorkflowJob
from app import auth_security, billing, payments, publications, usage
# Re-exported: existing callers import video_provider_config_issue from app.main.
from app.providers.catalog import video_provider_config_issue  # noqa: F401
from app.publishers import google_oauth
from app.runtime_env import start_process
from app.workflow import (ExecutionContext, RunOptions, RunRequestError, default_executor, default_registry,
                          parse_graph)
from app.workflow.config import ConfigError, check_tools
from app.workflow.nodes import ReviewNodeHandler
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
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/ogg"}


def media_signature_matches(content_type: str, path: Path) -> bool:
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
    task: str = Field(pattern="^(script|image|video|voice|music)$")
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
    asset_id: str
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)


class NewWorkflow(BaseModel):
    name: str = Field(min_length=1, max_length=150)


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
        return {"workspace": {"id": ws.id, "name": ws.name, "plan": plan.code if plan else ws.plan, "subscription_status": effective_status(subscription) if subscription else "unavailable"}, "user": {"email": user.email}, "is_admin": user.is_admin, "projects": [public_project(p) for p in projects], "assets": [{"id": a.id, "filename": a.filename, "bytes": a.bytes, "content_type": a.content_type, "project_id": a.project_id, "run_id": a.run_id, "created_at": a.created_at.isoformat() if a.created_at else None} for a in assets], "workflows": [{"id": w.id, "name": w.name, "graph": workflow_graph(w.definition)} for w in workflows], "limits": {"projects": plan.project_limit if plan else None, "workflows": plan.workflow_limit if plan else None}}


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


def public_publication(row):
    return {"id": row.id, "run_id": row.run_id, "asset_id": row.asset_id, "channel": row.channel,
            "title": row.title, "description": row.description, "state": row.state,
            "remote_id": row.remote_id, "last_error": row.last_error,
            "can_retry": publications.can_retry_publication(row),
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}


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
        existing = db.scalar(select(publications.Publication).where(
            publications.Publication.workspace_id == ws.id,
            publications.Publication.run_id == data.run_id,
            publications.Publication.channel == "youtube"))
        try:
            row = publications.queue_publication(db, workspace_id=ws.id, run_id=data.run_id,
                asset_id=data.asset_id, channel="youtube", title=data.title.strip(),
                description=data.description)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if existing:
            response.status_code = 200
        return public_publication(row)


@app.post("/api/youtube/publications/{publication_id}/retry", status_code=202)
def retry_youtube_publication(publication_id: str, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = youtube_owner(request, db)
        active_plan(db, ws)
        try:
            row = publications.retry_publication(db, workspace_id=ws.id, publication_id=publication_id)
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
                  "code": check.code, "field": check.field}
                 for node, check in checks]
        required = sum(check.credits for _, check in checks)
        return {"workflow_id": workflow.id,
                "runnable": all(step["status"] in {"ready", "configured"} for step in steps)
                and context.credit_balance >= required,
                "credits_required": required, "credits_available": context.credit_balance, "steps": steps}


def public_step_output(step):
    if not step.output:
        return None
    output = json.loads(step.output)
    if isinstance(output, dict):
        for key in ("submission", "error_count", "video_url"):
            output.pop(key, None)
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
        if run.status != "awaiting_review" or not reviews or not any(step.node_type == "video" and step.status == "completed" for step in steps):
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
        # Only the video request is frozen; text jobs (keyed "text:") are generated again.
        original_job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == original.id,
                                                           WorkflowJob.logical_key.like("video:%"))
                                 .order_by(WorkflowJob.created_at))
        return persist_run(db, ws, workflow, project, original.graph_snapshot,
                           retry_of_id=original.id, frozen_video=original_job.payload if original_job else None)


@app.post("/api/workflows", status_code=201)
def create_workflow(data: NewWorkflow, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        enforce_limit(db, ws, Workflow, "workflow_limit")
        workflow = Workflow(id=ident(), workspace_id=ws.id, name=data.name.strip(), definition=json.dumps(default_graph()))
        if not workflow.name:
            raise HTTPException(400, "Name required")
        db.add(workflow)
        return {"id": workflow.id, "name": workflow.name, "graph": workflow_graph(workflow.definition)}


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
        for node in graph.nodes:
            try:
                check_tools(default_registry.resolve(node.type).config_fields, node.config, tools)
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
    content_type = file.content_type or ""
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
    return {"id": asset_id, "filename": filename, "bytes": size}


@app.get("/api/assets/{asset_id}")
def download_asset(asset_id: str, request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == ws.id))
        if not asset:
            raise HTTPException(404, "Asset not found")
        return FileResponse(media_root(db) / ws.id / asset.id, media_type=asset.content_type, filename=asset.filename)
