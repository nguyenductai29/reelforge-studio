"""ReelForge Studio: small, self-hosted first slice."""
from contextlib import asynccontextmanager
import asyncio
import hashlib
import json
import math
import os
import re
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from sqlalchemy import case, func, inspect, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db import ROOT, config, engine, Session
from app.body_limit import MULTIPART_OVERHEAD_BYTES, RequestBodyLimitMiddleware
from app.models import Notification, PaymentOrderEvent, SupportMessage, SupportTicket, UserProfile, VerificationCheck
from app.models import User, LoginSession, Workspace, Membership, Project, Asset, Workflow, SystemSetting, WorkspaceSetting, Plan, Subscription, PaymentOrder, CreditAccount, CreditLedger, UsageEvent, AITool, WorkflowRun, WorkflowRunStep, WorkflowJob
from app import (auth_security, bank_qr, billing, config_checks, heartbeat, jobs, master_key, media_maintenance,
                 notifications, payment_config, payment_providers, payments, publications, readiness, reconciliation,
                 run_summary, secret_box, sources, storage, support, system_config, usage)
from app.payment_providers import onepay
from app.payment_providers import setup as payment_setup
from app.models import CreditReconciliation
# Re-exported: existing callers import video_provider_config_issue from app.main.
from app.providers.catalog import video_provider_config_issue  # noqa: F401
from app.publishers import channel_oauth, google_oauth
from app.publishers.youtube import UPLOAD_SCOPE as YOUTUBE_UPLOAD_SCOPE
from app import runtime_env
from app.runtime_env import start_process
from app.logs import log_event
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
    # Used by text steps whose own setting is left empty (app/workflow/nodes/text.py).
    "default_platform": "generic",
    "default_tone": "neutral",
    "default_duration": None,
    # Prefills the time of a scheduled post ("HH:MM", the viewer's time zone).
    "default_publish_time": None,
}


def setting(db, key):
    return json.loads(db.get(SystemSetting, key).value)


def workspace_settings(db, workspace_id):
    values = {}
    for key, default in WORKSPACE_DEFAULTS.items():
        row = db.get(WorkspaceSetting, (workspace_id, key))
        values[key] = json.loads(row.value) if row else default
    return values


# Storage paths, quotas and retention live in app/storage.py; this name stays for existing importers.
media_root = storage.media_root


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
    # Like every worker: the optional legacy runtime file, then settings from PostgreSQL (Admin → System
    # settings, app/system_config.py). Activation happens here, not at import, because workers and tests
    # import this module without serving the API.
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
    # Media storage per workspace (Phase 17); null uses WORKSPACE_MEDIA_QUOTA_BYTES. Left out, it is unchanged.
    storage_limit_bytes: int | None = Field(default=None, ge=64 * 1024 * 1024, le=100 * 1024 ** 4)


class CheckoutInput(BaseModel):
    plan_code: str = Field(pattern="^(standard|pro)$")
    # vietqr: payOS bank transfer (the default, as before); card: OnePAY.
    method: Literal["vietqr", "card"] = "vietqr"


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
    default_platform: Literal["generic", "youtube", "youtube_shorts", "tiktok", "facebook"] = "generic"
    default_tone: Literal["neutral", "casual", "professional", "cinematic", "storytelling", "documentary", "dramatic",
                          "funny"] = "neutral"
    default_duration: int | None = Field(default=None, ge=5, le=3600)
    default_publish_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ProfileInput(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


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
        # An expired or deleted asset keeps its row with 0 bytes (app/storage.py); it is not listed.
        assets = db.scalars(select(Asset).where(Asset.workspace_id == ws.id, Asset.bytes > 0)
                            .order_by(Asset.created_at.desc())).all()
        workflows = db.scalars(select(Workflow).where(Workflow.workspace_id == ws.id)).all()
        user = authorize(request, db)
        subscription = db.get(Subscription, ws.id)
        plan = db.get(Plan, subscription.plan_code) if subscription else None
        return {"workspace": {"id": ws.id, "name": ws.name, "plan": plan.code if plan else ws.plan, "subscription_status": effective_status(subscription) if subscription else "unavailable"}, "user": {"email": user.email}, "is_admin": user.is_admin, "projects": [public_project(p) for p in projects], "assets": [{"id": a.id, "filename": a.filename, "bytes": a.bytes, "content_type": a.content_type, "project_id": a.project_id, "run_id": a.run_id, "created_at": a.created_at.isoformat() if a.created_at else None} for a in assets], "workflows": [{"id": w.id, "name": w.name, "graph": workflow_graph(w.definition)} for w in workflows], "limits": {"projects": plan.project_limit if plan else None, "workflows": plan.workflow_limit if plan else None},
                "storage": storage.usage(db, ws.id)}


@app.get("/api/settings")
def get_settings(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        user = authorize(request, db)
        system = None
        if user.is_admin:
            system = {key: setting(db, key) for key in SYSTEM_DEFAULTS}
            # The folder in use: REELFORGE_STORAGE_ROOT wins over the stored setting (app/storage.py).
            system["storage_dir"] = str(storage.media_root(db))
            root_source = system_config.source("storage.root")
            system["storage_dir_source"] = root_source if root_source in ("admin", "environment") else "setting"
        profile = db.get(UserProfile, user.id)
        return {"workspace": workspace_settings(db, ws.id), "system": system,
                "profile": {"display_name": profile.display_name if profile else None}}


@app.put("/api/settings/workspace")
def update_workspace_settings(data: WorkspaceSettingsInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        membership = db.get(Membership, (authorize(request, db).id, ws.id))
        if membership.role != "owner":
            raise HTTPException(403, "Workspace owner required")
        for key, value in data.model_dump().items():
            row = db.get(WorkspaceSetting, (ws.id, key))
            if row is None:
                db.add(WorkspaceSetting(workspace_id=ws.id, key=key, value=json.dumps(value)))
            else:
                row.value = json.dumps(value)
        return {"workspace": data.model_dump()}


@app.put("/api/settings/profile")
def update_profile(data: ProfileInput, request: Request):
    """The signed-in user's display name; empty clears it."""
    same_origin(request)
    with Session.begin() as db:
        user = authorize(request, db)
        name = " ".join((data.display_name or "").split()) or None
        profile = db.get(UserProfile, user.id)
        if profile is None:
            profile = UserProfile(user_id=user.id)
            db.add(profile)
        profile.display_name, profile.updated_at = name, datetime.now(timezone.utc)
        return {"display_name": name}


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


def retention_data(policy: storage.RetentionPolicy) -> dict:
    return {"intermediate_days": policy.intermediate_days, "temp_days": policy.temp_days,
            "partial_days": policy.partial_days}


def intermediate_summary(db, workspace_id, project_id=None) -> dict:
    """Intermediate media a user may remove now: their run has a final render and no publication uses them."""
    found = storage.expirable_query(workspace_id=workspace_id, project_id=project_id).subquery()
    count, total = db.execute(select(func.count(), func.coalesce(func.sum(found.c.bytes), 0)).select_from(found)).one()
    return {"assets": int(count), "bytes": int(total)}


@app.get("/api/storage")
def storage_overview(request: Request):
    """How much media the workspace stores, by type, against its plan's quota, with the warning level."""
    with Session() as db:
        ws = workspace_for(request, db)
        return {**storage.usage(db, ws.id), "by_type": media_maintenance.usage_by_type(db, ws.id),
                "intermediate": intermediate_summary(db, ws.id),
                "retention": retention_data(storage.RetentionPolicy.from_environment())}


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
            "is_active": plan.is_active, "price_vnd": plan.price_vnd,
            "storage_limit_bytes": plan.storage_limit_bytes,
            "storage_quota_bytes": storage.effective_quota(plan.storage_limit_bytes)}


def order_data(order):
    """A payment order without anything secret: no checkout token, no provider payload."""
    data = {"id": order.id, "plan_code": order.plan_code, "provider": order.provider,
            "method": payment_providers.PROVIDER_METHOD.get(order.provider),
            "reference": str(order.order_code), "provider_reference": order.provider_reference,
            "amount_vnd": order.amount_vnd, "status": order.status,
            "created_at": order.created_at.isoformat(), "paid_at": order.paid_at.isoformat() if order.paid_at else None}
    if order.provider == "bank_qr":
        # Manual VietQR: the content the buyer must write, and when they said they paid.
        data["transfer_content"] = order.provider_reference
        data["transfer_reported_at"] = _iso_or_none(order.transfer_reported_at)
    return data


ORDER_PAGE = Query(default=10, ge=1, le=50)


@app.get("/api/billing")
def billing_overview(request: Request):
    with Session() as db:
        ws = workspace_for(request, db)
        subscription = db.get(Subscription, ws.id)
        orders = db.scalars(select(PaymentOrder).where(PaymentOrder.workspace_id == ws.id).order_by(PaymentOrder.created_at.desc()).limit(10)).all()
        total = db.scalar(select(func.count()).select_from(PaymentOrder).where(PaymentOrder.workspace_id == ws.id))
        return {"plans": [plan_data(p) for p in db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.code))],
                "subscription": {"plan_code": subscription.plan_code, "status": effective_status(subscription),
                                 "ends_at": subscription.ends_at.isoformat() if subscription.ends_at else None},
                "orders": [order_data(o) for o in orders], "orders_total": total,
                # Only the payment methods buyers may choose now; payos_ready is kept for older clients.
                "methods": payment_providers.available_methods(),
                "payos_ready": billing.configured() and billing.enabled() and payment_providers.vietqr_mode() == "payos"}


@app.get("/api/billing/orders")
def billing_orders(request: Request, limit: int = ORDER_PAGE, offset: int = Query(default=0, ge=0)):
    """The workspace's payment history, newest first, one page at a time."""
    with Session() as db:
        ws = workspace_for(request, db)
        where = PaymentOrder.workspace_id == ws.id
        rows = db.scalars(select(PaymentOrder).where(where).order_by(PaymentOrder.created_at.desc(), PaymentOrder.id)
                          .limit(limit).offset(offset)).all()
        total = db.scalar(select(func.count()).select_from(PaymentOrder).where(where))
        return {"items": [order_data(o) for o in rows], "total": total, "limit": limit, "offset": offset}


@app.post("/api/billing/checkout", status_code=201)
def billing_checkout(data: CheckoutInput, request: Request):
    same_origin(request)
    provider = payment_providers.for_method(data.method)
    # A disabled provider takes no new checkout; its existing orders still settle (webhooks, IPN, Check).
    if not provider.offered():
        raise HTTPException(503, "This payment method is not available")
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
        order_code = secrets.randbelow(8_000_000_000_000) + 1_000_000_000_000
        manual = bank_qr.settings() if provider.name == "bank_qr" else None
        order = PaymentOrder(id=ident(), workspace_id=ws.id, plan_code=plan.code,
                             provider=provider.name, order_code=order_code,
                             amount_vnd=plan.price_vnd, credits_award=plan.monthly_credits,
                             # A manual transfer is told apart by its content: the prefix and the order code.
                             provider_reference=bank_qr.transfer_content(manual["transfer_prefix"], order_code)
                             if manual else None,
                             status="pending", created_at=datetime.now(timezone.utc))
        db.add(order)
        db.flush()
        order_id, code, amount, plan_code = order.id, order.order_code, order.amount_vnd, plan.code
        content = order.provider_reference
    if manual is not None:
        # Manual VietQR: the QR is shown in ReelForge; an admin confirms the money arrived.
        return {"order_id": order_id, "checkout_url": None,
                "transfer": bank_qr.details(manual, amount_vnd=amount, content=content)}
    try:
        link = provider.checkout(order_code=code, amount_vnd=amount, plan_code=plan_code, origin=origin.rstrip("/"),
                                 client_ip=request.client.host if request.client else "")
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
            payments.apply_paid(db, verified.order_code, int(verified.amount), str(getattr(verified, "reference", "")),
                                provider="payos")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payment_setup.record_activity("payos", "webhook")
    return {"ok": True}


def refresh_order(order_id: str, workspace_id: str | None = None):
    """Ask the order's own provider, server to server, and settle what it confirms (idempotent)."""
    with Session() as db:
        query = select(PaymentOrder).where(PaymentOrder.id == order_id)
        if workspace_id is not None:
            query = query.where(PaymentOrder.workspace_id == workspace_id)
        order = db.scalar(query)
        if not order:
            raise HTTPException(404, "Order not found")
        if order.status in ("paid", "paid_unapplied") or order.provider == "bank_qr":
            # Nothing to ask for a manual transfer: a system admin confirms it.
            return order_data(order)
        provider = payment_providers.provider(order.provider)
        if provider is None or not provider.configured():
            raise HTTPException(503, "payOS is not configured" if order.provider == "payos" else
                                "This payment provider is not configured")
        code, amount, name = order.order_code, order.amount_vnd, order.provider
    try:
        evidence = provider.lookup(code, amount)
    except payment_providers.ProviderMismatch as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Unable to check payment with payOS" if name == "payos" else
                            "Unable to check payment with the provider") from exc
    with Session.begin() as db:
        try:
            payments.settle(db, name, evidence)
        except ValueError as exc:
            raise HTTPException(502, "Payment amount mismatch") from exc
        result = order_data(db.get(PaymentOrder, order_id))
    payment_setup.record_activity(name, "query")
    return result


def _manual_order(db, order_id: str, workspace_id: str, lock: bool = False) -> PaymentOrder:
    """A manual VietQR order of this studio; anything else answers 404 (no cross-studio access)."""
    query = select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.workspace_id == workspace_id,
                                       PaymentOrder.provider == "bank_qr")
    order = db.scalar(query.with_for_update() if lock else query)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


@app.get("/api/billing/orders/{order_id}/transfer")
def manual_transfer_details(order_id: str, request: Request):
    """The QR and bank details of a pending manual VietQR order, to show it again."""
    with Session() as db:
        ws = workspace_for(request, db)
        order = _manual_order(db, order_id, ws.id)
        values = bank_qr.settings()
        if bank_qr.problems(values):
            raise HTTPException(503, "This payment method is not available")
        return {"order": order_data(order),
                "transfer": bank_qr.details(values, amount_vnd=order.amount_vnd, content=order.provider_reference)}


@app.post("/api/billing/orders/{order_id}/transferred")
def report_manual_transfer(order_id: str, request: Request):
    """The buyer says the transfer is made. Nothing is paid until a system admin confirms the money arrived."""
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        user = authorize(request, db)
        if db.get(Membership, (user.id, ws.id)).role != "owner":
            raise HTTPException(403, "Workspace owner required")
        order = _manual_order(db, order_id, ws.id, lock=True)
        if order.status not in payments.WAITING_STATUSES:
            raise HTTPException(409, "Order is not awaiting payment")
        if order.status == "pending":
            # Not paid yet: an administrator still has to confirm that the money arrived.
            now = datetime.now(timezone.utc)
            order.status = "awaiting_confirmation"
            order.transfer_reported_at = order.transfer_reported_at or now
            db.add(PaymentOrderEvent(order_id=order.id, action="transfer_reported", user_id=user.id,
                                     amount_vnd=order.amount_vnd, created_at=now))
            notifications.transfer_reported(db, order, order.provider_reference or "")
        result = order_data(order)
    log_event(logger, "manual_transfer_reported", order_id=order_id)
    return result


@app.post("/api/billing/orders/{order_id}/refresh")
def refresh_payment_order(order_id: str, request: Request):
    same_origin(request)
    with Session() as db:
        ws = workspace_for(request, db)
    return refresh_order(order_id, ws.id)


logger = logging.getLogger("app.main")


@app.get("/api/billing/onepay/return")
def onepay_return(request: Request):
    """Where OnePAY sends the buyer back. The signed result is read, but a payment is applied only after
    QueryDR confirms it server to server (or when the signed IPN arrives); a browser visit alone never pays."""
    card = payment_providers.provider("onepay")
    params = dict(request.query_params)
    if not card.configured():
        return RedirectResponse("/billing?payment=failed", status_code=303)
    try:
        evidence = card.evidence(params)
    except (onepay.OnePayError, payment_providers.ProviderMismatch) as exc:
        log_event(logger, "payment_return_rejected", level=logging.WARNING, provider="onepay",
                  reason=getattr(exc, "code", "mismatch"))
        return RedirectResponse("/billing?payment=invalid", status_code=303)
    outcome = "returned"
    if evidence.status in ("failed", "cancelled"):
        with Session.begin() as db:
            payments.settle(db, "onepay", evidence)
        outcome = evidence.status
    elif evidence.status == "paid" and card.can_confirm():
        with Session() as db:
            order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == evidence.order_code))
            amount = order.amount_vnd if order else None
        try:
            confirmed = card.lookup(evidence.order_code, amount or 0) if amount else None
            if confirmed is not None:
                with Session.begin() as db:
                    payments.settle(db, "onepay", confirmed)
        except (onepay.OnePayError, payment_providers.ProviderMismatch, ValueError) as exc:
            # Left pending: the IPN or a later status check settles it.
            log_event(logger, "payment_confirmation_failed", level=logging.WARNING, provider="onepay",
                      reason=getattr(exc, "code", type(exc).__name__))
    return RedirectResponse(f"/billing?payment={outcome}", status_code=303)


@app.api_route("/api/webhooks/onepay", methods=["GET", "POST"])
async def onepay_ipn(request: Request):
    """OnePAY's server-to-server IPN. Settles only a correctly signed result of a OnePAY order, for its exact amount."""
    fail = lambda reason: PlainTextResponse(f"responsecode=0&desc={reason}")  # noqa: E731
    params = dict(request.query_params)
    if request.method == "POST":
        body = await request.body()
        if len(body) > 16384:
            return fail("confirm-fail")
        params.update(dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)))
    card = payment_providers.provider("onepay")
    if not card.configured():
        return fail("confirm-fail")
    try:
        evidence = card.evidence(params)
    except (onepay.OnePayError, payment_providers.ProviderMismatch):
        log_event(logger, "payment_callback_rejected", level=logging.WARNING, provider="onepay", reason="signature")
        return fail("confirm-fail")
    try:
        with Session.begin() as db:
            status = payments.settle(db, "onepay", evidence)
    except ValueError:
        log_event(logger, "payment_callback_rejected", level=logging.WARNING, provider="onepay", reason="amount")
        return fail("amount-mismatch")
    if status == "unknown" or (evidence.status == "paid" and status not in ("paid", "paid_unapplied")):
        return fail("order-not-found")
    payment_setup.record_activity("onepay", "ipn")
    return PlainTextResponse("responsecode=1&desc=confirm-success")


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


ADMIN_LIMIT = Query(default=20, ge=1, le=100)
ADMIN_OFFSET = Query(default=0, ge=0)


def count_of(db, query) -> int:
    return db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))


@app.get("/api/admin")
def admin_overview(request: Request):
    """Counts for the admin header, from COUNT queries; the collections are paginated endpoints of their own."""
    with Session() as db:
        admin_for(request, db)
        users = select(User.id)
        stuck = jobs.stuck_jobs(db, limit=100)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        levels = storage.level_counts(db)
        return {
            "counts": {
                "users": count_of(db, users),
                "active_users": count_of(db, users.where(User.is_active.is_(True))),
                "admins": count_of(db, users.where(User.is_admin.is_(True), User.is_active.is_(True))),
                "workspaces": count_of(db, select(Workspace.id)),
                "plans": count_of(db, select(Plan.code)),
                "pending_reconciliation": reconciliation.list_items(db, status="pending", limit=1, offset=0)["total"],
                "failed_jobs_24h": count_of(db, select(WorkflowJob.id).where(WorkflowJob.state == "failed",
                                                                             WorkflowJob.updated_at >= since)),
                "stuck_jobs": len(stuck["expired_leases"]) + len(stuck["overdue"]),
                "pending_payments": count_of(db, select(PaymentOrder.id).where(
                    PaymentOrder.status.in_(payments.WAITING_STATUSES))),
                # Studios at 90 % of their storage or more (the levels below come from one grouped query).
                "storage_alerts": sum(levels[name] for name in ("critical", "full")),
                "support_open": count_of(db, select(SupportTicket.id).where(
                    SupportTicket.status.in_(support.AWAITING_SUPPORT))),
                # Manual VietQR transfers the buyer reported, waiting for an admin.
                "transfers_to_confirm": count_of(db, select(PaymentOrder.id).where(
                    PaymentOrder.status == "awaiting_confirmation")),
            },
            "storage_levels": levels,
            "plans": [plan_data(p) for p in db.scalars(select(Plan).order_by(Plan.code))],
            "payment_providers": payment_providers.readiness(),
        }


def _iso_or_none(value):
    return _iso(value) if value else None


def _first_studios(db, user_ids):
    """Each user's first studio (by creation) with its subscription, for one page of users."""
    found = {}
    if not user_ids:
        return found
    rows = db.execute(select(Membership.user_id, Membership.role, Workspace, Subscription)
                      .join(Workspace, Workspace.id == Membership.workspace_id)
                      .outerjoin(Subscription, Subscription.workspace_id == Workspace.id)
                      .where(Membership.user_id.in_(user_ids)).order_by(Workspace.created_at, Workspace.id))
    for user_id, role, ws, subscription in rows:
        found.setdefault(user_id, (role, ws, subscription))
    return found


def admin_user(user, created_at, studio, display_name=None):
    role, ws, subscription = studio if studio else (None, None, None)
    return {"id": user.id, "email": user.email, "display_name": display_name, "is_admin": user.is_admin,
            "is_active": user.is_active, "created_at": _iso_or_none(created_at),
            "workspace": {"id": ws.id, "name": ws.name, "role": role} if ws else None,
            "plan_code": subscription.plan_code if subscription else None,
            "subscription_status": effective_status(subscription) if subscription else None}


@app.get("/api/admin/users")
def admin_users(request: Request, q: str | None = Query(default=None, max_length=255),
                role: Literal["admin", "member"] | None = None, status: Literal["active", "locked"] | None = None,
                limit: int = ADMIN_LIMIT, offset: int = ADMIN_OFFSET):
    """Users one page at a time; ``q`` searches email, case-insensitively, on the server."""
    with Session() as db:
        admin_for(request, db)
        joined = (select(Membership.user_id, func.min(Workspace.created_at).label("created_at"))
                  .join(Workspace, Workspace.id == Membership.workspace_id).group_by(Membership.user_id).subquery())
        query = select(User, joined.c.created_at, UserProfile.display_name) \
            .outerjoin(joined, joined.c.user_id == User.id).outerjoin(UserProfile, UserProfile.user_id == User.id)
        if q and q.strip():
            query = query.where(func.lower(User.email).like(like_pattern(q), escape="\\"))
        if role:
            query = query.where(User.is_admin.is_(role == "admin"))
        if status:
            query = query.where(User.is_active.is_(status == "active"))
        total = count_of(db, query)
        rows = db.execute(query.order_by(joined.c.created_at.desc().nulls_last(), User.email)
                          .limit(limit).offset(offset)).all()
        studios = _first_studios(db, [user.id for user, _, _ in rows])
        return {"items": [admin_user(user, created, studios.get(user.id), name) for user, created, name in rows],
                "total": total, "limit": limit, "offset": offset}


@app.get("/api/admin/users/{user_id}")
def admin_user_detail(user_id: str, request: Request):
    with Session() as db:
        admin_for(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        memberships = db.execute(select(Membership.role, Workspace, Subscription, CreditAccount.balance)
                                 .join(Workspace, Workspace.id == Membership.workspace_id)
                                 .outerjoin(Subscription, Subscription.workspace_id == Workspace.id)
                                 .outerjoin(CreditAccount, CreditAccount.workspace_id == Workspace.id)
                                 .where(Membership.user_id == user_id).order_by(Workspace.created_at)).all()
        created = min((ws.created_at for _, ws, _, _ in memberships if ws.created_at), default=None)
        profile = db.get(UserProfile, user_id)
        sessions = count_of(db, select(LoginSession.token_hash).where(LoginSession.user_id == user_id,
                                                                     LoginSession.expires_at > datetime.now(timezone.utc)))
        first = (memberships[0][0], memberships[0][1], memberships[0][2]) if memberships else None
        return {**admin_user(user, created, first, profile.display_name if profile else None),
                "active_sessions": sessions,
                "workspaces": [{"id": ws.id, "name": ws.name, "role": role,
                                "plan_code": sub.plan_code if sub else None,
                                "status": effective_status(sub) if sub else "unavailable", "credits": balance or 0}
                               for role, ws, sub, balance in memberships]}


def admin_workspace(ws, owner_email, subscription, balance):
    return {"id": ws.id, "name": ws.name, "owner_id": ws.owner_id, "owner_email": owner_email,
            "plan_code": subscription.plan_code if subscription else None,
            "status": effective_status(subscription) if subscription else "unavailable",
            "ends_at": _iso_or_none(subscription.ends_at) if subscription else None,
            "credits": balance or 0, "created_at": _iso_or_none(ws.created_at)}


@app.get("/api/admin/workspaces")
def admin_workspaces(request: Request, q: str | None = Query(default=None, max_length=255), plan: str | None = None,
                     status: Literal["active", "expired", "paused", "canceled"] | None = None,
                     limit: int = ADMIN_LIMIT, offset: int = ADMIN_OFFSET):
    """Studios one page at a time; ``q`` searches the studio name and the owner's email on the server."""
    with Session() as db:
        admin_for(request, db)
        now = datetime.now(timezone.utc)
        query = (select(Workspace, User.email, Subscription, CreditAccount.balance)
                 .join(User, User.id == Workspace.owner_id)
                 .outerjoin(Subscription, Subscription.workspace_id == Workspace.id)
                 .outerjoin(CreditAccount, CreditAccount.workspace_id == Workspace.id))
        if q and q.strip():
            pattern = like_pattern(q)
            query = query.where(or_(func.lower(Workspace.name).like(pattern, escape="\\"),
                                    func.lower(User.email).like(pattern, escape="\\")))
        if plan:
            query = query.where(Subscription.plan_code == plan)
        if status == "active":
            query = query.where(Subscription.status == "active", or_(Subscription.ends_at.is_(None), Subscription.ends_at > now))
        elif status == "expired":
            query = query.where(Subscription.status == "active", Subscription.ends_at <= now)
        elif status:
            query = query.where(Subscription.status == status)
        total = count_of(db, query)
        rows = db.execute(query.order_by(Workspace.created_at.desc(), Workspace.id).limit(limit).offset(offset)).all()
        stored = storage.usage_by_workspace(db, [row[0].id for row in rows])
        return {"items": [{**admin_workspace(*row), "storage": stored.get(row[0].id)} for row in rows],
                "total": total, "limit": limit, "offset": offset}


@app.get("/api/admin/workspaces/{workspace_id}")
def admin_workspace_detail(workspace_id: str, request: Request):
    with Session() as db:
        admin_for(request, db)
        row = db.execute(select(Workspace, User.email, Subscription, CreditAccount.balance)
                         .join(User, User.id == Workspace.owner_id)
                         .outerjoin(Subscription, Subscription.workspace_id == Workspace.id)
                         .outerjoin(CreditAccount, CreditAccount.workspace_id == Workspace.id)
                         .where(Workspace.id == workspace_id)).first()
        if not row:
            raise HTTPException(404, "Workspace not found")
        members = db.execute(select(User.email, Membership.role).join(Membership, Membership.user_id == User.id)
                             .where(Membership.workspace_id == workspace_id).order_by(User.email)).all()
        ledger = db.scalars(select(CreditLedger).where(CreditLedger.workspace_id == workspace_id)
                            .order_by(CreditLedger.created_at.desc()).limit(10)).all()
        orders = db.scalars(select(PaymentOrder).where(PaymentOrder.workspace_id == workspace_id)
                            .order_by(PaymentOrder.created_at.desc()).limit(5)).all()
        return {**admin_workspace(*row),
                "members": [{"email": email, "role": role} for email, role in members],
                "counts": {"projects": count_of(db, select(Project.id).where(Project.workspace_id == workspace_id)),
                           "workflows": count_of(db, select(Workflow.id).where(Workflow.workspace_id == workspace_id)),
                           "runs": count_of(db, select(WorkflowRun.id).where(WorkflowRun.workspace_id == workspace_id))},
                "storage_bytes": sum(media_maintenance.usage_by_type(db, workspace_id).values()),
                "storage": storage.usage(db, workspace_id),
                "ledger": [{"id": e.id, "delta": e.delta, "reason": e.reason, "created_at": _iso(e.created_at)}
                           for e in ledger],
                "orders": [order_data(order) for order in orders]}


@app.get("/api/admin/payments")
def admin_payments(request: Request, q: str | None = Query(default=None, max_length=255),
                   provider: Literal["payos", "bank_qr", "onepay"] | None = None,
                   status: Literal["pending", "awaiting_confirmation", "paid", "paid_unapplied", "failed", "cancelled",
                                   "expired", "rejected"] | None = None,
                   limit: int = ADMIN_LIMIT, offset: int = ADMIN_OFFSET):
    """Payment orders of every studio, newest first; ``q`` searches the owner's email, the studio name,
    the order reference and the provider reference. No checkout URL or provider payload is returned."""
    with Session() as db:
        admin_for(request, db)
        query = (select(PaymentOrder, Workspace.name, User.email)
                 .join(Workspace, Workspace.id == PaymentOrder.workspace_id)
                 .join(User, User.id == Workspace.owner_id))
        if q and q.strip():
            pattern = like_pattern(q)
            matches = [func.lower(User.email).like(pattern, escape="\\"),
                       func.lower(Workspace.name).like(pattern, escape="\\"),
                       func.lower(PaymentOrder.provider_reference).like(pattern, escape="\\")]
            if q.strip().isdigit() and len(q.strip()) <= 18:
                matches.append(PaymentOrder.order_code == int(q.strip()))
            query = query.where(or_(*matches))
        if provider:
            query = query.where(PaymentOrder.provider == provider)
        if status:
            query = query.where(PaymentOrder.status == status)
        total = count_of(db, query)
        rows = db.execute(query.order_by(PaymentOrder.created_at.desc(), PaymentOrder.id).limit(limit).offset(offset)).all()
        return {"items": [{**order_data(order), "workspace_id": order.workspace_id, "workspace_name": name,
                           "owner_email": email} for order, name, email in rows],
                "total": total, "limit": limit, "offset": offset}


class ManualConfirmInput(BaseModel):
    # The amount the admin saw arrive; it must equal the order's exact amount.
    amount_vnd: int = Field(gt=0)


class ManualRejectInput(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _admin_manual_order(db, order_id: str) -> PaymentOrder:
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(404, "Order not found")
    if order.provider != "bank_qr":
        raise HTTPException(409, "Only manual bank transfers are confirmed by an administrator")
    return order


@app.post("/api/admin/payments/{order_id}/confirm")
def confirm_manual_payment(order_id: str, data: ManualConfirmInput, request: Request):
    """A system admin confirms a manual VietQR transfer arrived. Settles through the shared path, exactly once."""
    same_origin(request)
    with Session.begin() as db:
        admin = admin_for(request, db)
        order = _admin_manual_order(db, order_id)
        if order.status in ("paid", "paid_unapplied"):
            raise HTTPException(409, "Order already settled")
        if data.amount_vnd != order.amount_vnd:
            raise HTTPException(422, {"code": "amount_mismatch", "field": "amount_vnd",
                                      "message": "The confirmed amount must equal the order amount"})
        status = payments.apply_paid(db, order.order_code, order.amount_vnd, order.provider_reference or "",
                                     provider="bank_qr")
        db.add(PaymentOrderEvent(order_id=order.id, action="confirmed", user_id=admin.id, amount_vnd=order.amount_vnd,
                                 created_at=datetime.now(timezone.utc)))
        result = order_data(order)
        admin_id = admin.id
    log_event(logger, "manual_payment_confirmed", admin_id=admin_id, order_id=order_id, amount=data.amount_vnd,
              status=status)
    return result


@app.post("/api/admin/payments/{order_id}/reject")
def reject_manual_payment(order_id: str, data: ManualRejectInput, request: Request):
    """The transfer was not found. The order fails; if the money turns up later it can still be confirmed."""
    same_origin(request)
    with Session.begin() as db:
        admin = admin_for(request, db)
        order = _admin_manual_order(db, order_id)
        if order.status not in payments.WAITING_STATUSES:
            raise HTTPException(409, "Order is not awaiting payment")
        order.status = "rejected"
        reason = (data.note or "").strip() or None
        db.add(PaymentOrderEvent(order_id=order.id, action="rejected", user_id=admin.id, amount_vnd=order.amount_vnd,
                                 note=reason, created_at=datetime.now(timezone.utc)))
        notifications.payment_rejected(db, order, reason)
        result = order_data(order)
        admin_id = admin.id
    log_event(logger, "manual_payment_rejected", admin_id=admin_id, order_id=order_id)
    return result


@app.get("/api/admin/payments/{order_id}/events")
def manual_payment_events(order_id: str, request: Request):
    """A manual order's history: reported, confirmed, rejected, with who and the amount."""
    with Session() as db:
        admin_for(request, db)
        rows = db.execute(select(PaymentOrderEvent, User.email).outerjoin(User, User.id == PaymentOrderEvent.user_id)
                          .where(PaymentOrderEvent.order_id == order_id)
                          .order_by(PaymentOrderEvent.created_at, PaymentOrderEvent.id)).all()
        return {"items": [{"action": event.action, "at": _iso(event.created_at), "by": email,
                           "amount_vnd": event.amount_vnd, "note": event.note} for event, email in rows]}


@app.post("/api/admin/payments/{order_id}/refresh")
def admin_refresh_payment(order_id: str, request: Request):
    """Ask the provider again, server to server; only what the provider confirms is applied. Admins cannot mark paid."""
    same_origin(request)
    with Session() as db:
        admin = admin_for(request, db)
        admin_id = admin.id
    result = refresh_order(order_id)
    log_event(logger, "payment_refreshed_by_admin", admin_id=admin_id, order_id=order_id, status=result["status"])
    return result


@app.get("/api/admin/payment-providers")
def admin_payment_providers(request: Request):
    """Whether each payment provider is configured; credentials are never returned."""
    with Session() as db:
        admin_for(request, db)
    return {"providers": payment_providers.readiness()}


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
        total = count_of(db, query)
        rows = db.scalars(query.order_by(WorkflowJob.updated_at.desc(), WorkflowJob.id).limit(limit).offset(offset))
        queue_name = case(*((WorkflowJob.logical_key.startswith(f"{name}:"), name) for name in ADMIN_JOB_QUEUES),
                          else_="other")
        counts = {}
        for key, job_state, count in db.execute(select(queue_name, WorkflowJob.state, func.count())
                                                .group_by(queue_name, WorkflowJob.state)):
            counts.setdefault(key, {})[job_state] = count
        return {"jobs": [public_job(job) for job in rows], "total": total, "limit": limit, "offset": offset,
                "counts": counts, "stuck": jobs.stuck_jobs(db)}


@app.get("/api/admin/storage")
def admin_storage(request: Request, limit: int = ADMIN_LIMIT, offset: int = ADMIN_OFFSET):
    """Studios by storage use (fullest first, one page), how many are at each warning level, and the disk."""
    with Session() as db:
        admin_for(request, db)
        usage_rows = storage.usage_by_workspace(db)
        names = dict(db.execute(select(Workspace.id, Workspace.name)).all())
        files = dict(db.execute(select(Asset.workspace_id, func.count(Asset.id)).where(Asset.bytes > 0)
                                .group_by(Asset.workspace_id)).all())
        # "bytes" repeats used_bytes for clients of the Phase 13 response.
        items = sorted(({"workspace_id": workspace_id, "name": names.get(workspace_id, ""),
                         "files": int(files.get(workspace_id, 0)), "bytes": item["used_bytes"], **item}
                        for workspace_id, item in usage_rows.items()),
                       key=lambda item: (-item["percent"], -item["used_bytes"], item["name"]))
        levels = {name: 0 for _, name in storage.LEVELS}
        for item in items:
            if item["level"] in levels:
                levels[item["level"]] += 1
        return {"workspaces": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset,
                "levels": levels, "disk": storage.disk_usage(db), "default_quota_bytes": storage.default_quota(),
                "retention": retention_data(storage.RetentionPolicy.from_environment())}


# --- Phase 18A/19: payment gateway configuration (system admins only) -------------------------------------
# Secrets are write-only: a saved value is never returned, logged or echoed in an error. Request bodies are
# parsed here rather than by FastAPI so a validation error can never quote a submitted value back.

class SecretUpdate(BaseModel):
    action: Literal["keep", "replace", "clear"] = "keep"
    value: str | None = None


class PayOSConfigInput(BaseModel):
    enabled: bool
    # Phase 20: make payOS the VietQR mode (automatic) in the same save.
    use_for_vietqr: bool = False
    client_id: SecretUpdate = SecretUpdate()
    api_key: SecretUpdate = SecretUpdate()
    checksum_key: SecretUpdate = SecretUpdate()


class OnePayConfigInput(BaseModel):
    enabled: bool
    mode: Literal["sandbox", "production", "custom"]
    merchant_id: SecretUpdate = SecretUpdate()
    access_code: SecretUpdate = SecretUpdate()
    hash_key: SecretUpdate = SecretUpdate()
    query_user: SecretUpdate = SecretUpdate()
    query_password: SecretUpdate = SecretUpdate()
    # Custom mode only; endpoints are not secret.
    payment_url: str | None = None
    query_url: str | None = None
    # Required to turn on (or switch to) the production card gateway.
    confirm_production: bool = False


class PaymentCheckInput(BaseModel):
    # Remote: one read-only OnePAY QueryDR about a reference that cannot exist; never a charge.
    remote: bool = False


class LegacyPaymentCheckInput(PaymentCheckInput):
    provider: Literal["payos", "onepay"]


class PaymentSwitchInput(BaseModel):
    confirm_production: bool = False


class BankQRConfigInput(BaseModel):
    enabled: bool
    bank_bin: str = ""
    bank_name: str = ""
    account_number: str = ""
    account_name: str = ""
    transfer_prefix: str = "RF"
    note: str = ""
    sla_message: str = ""
    # Make the manual bank QR the VietQR mode in the same save.
    use_for_vietqr: bool = True


class BankQRPreviewInput(BaseModel):
    bank_bin: str = ""
    account_number: str = ""
    account_name: str = ""
    transfer_prefix: str = "RF"


PAYMENT_CONFIG_INPUT = {"payos": PayOSConfigInput, "onepay": OnePayConfigInput, "bank_qr": BankQRConfigInput}


def _payment_provider(provider: str) -> str:
    if provider not in (*payment_config.PROVIDERS, "bank_qr"):
        raise HTTPException(404, "Unknown payment provider")
    return provider


def _settings_failure(exc: system_config.ConfigError) -> HTTPException:
    return HTTPException(422, {"code": exc.code, "field": exc.key, "message": "Settings not saved"})


def _save_bank_qr(db, admin_id: str, data: "BankQRConfigInput") -> list[str]:
    values = {name: getattr(data, name).strip() for name in bank_qr.FIELDS}
    values["transfer_prefix"] = values["transfer_prefix"].upper()
    values["account_name"] = values["account_name"].upper()
    if data.enabled and (missing := bank_qr.problems(values)):
        raise system_config.ConfigError("missing", f"payments.bank_qr.{missing[0]}")
    stored = {f"payments.bank_qr.{name}": value for name, value in values.items()}
    stored["payments.bank_qr.enabled"] = data.enabled
    if data.use_for_vietqr:
        stored["payments.vietqr_mode"] = "manual"
    return system_config.save(db, admin_id, values=stored, section="payments")


async def _payment_body(request: Request, model):
    """The JSON body as ``model``; a 422 names the field only, never the submitted value."""
    body = await request.body()
    if len(body) > 16384:
        raise HTTPException(413, "Request too large")
    try:
        return model.model_validate(json.loads(body or b"{}"))
    except (ValueError, ValidationError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else []
        location = next((str(part) for part in (errors[0]["loc"] if errors else ()) if isinstance(part, str)), None)
        raise HTTPException(422, {"code": "invalid_request", "field": location,
                                  "message": "Invalid payment configuration"}) from None


def _config_failure(exc) -> HTTPException:
    status = 409 if exc.code == "confirm_production" else 422
    field = getattr(exc, "field", None) or getattr(exc, "key", None)
    return HTTPException(status, {"code": exc.code, "field": field, "message": "Payment configuration not saved"})


def _payment_view(db, provider: str) -> dict:
    origin = setting(db, "frontend_origin").rstrip("/")
    return payment_setup.provider_view(db, provider, origin, payment_setup.activity(db))


@app.get("/api/admin/payment-config")
def admin_payment_config(request: Request):
    """Each gateway as resolved (admin, bootstrap, environment or missing); identifiers masked, secrets never."""
    with Session() as db:
        admin_for(request, db)
        providers = payment_setup.overview(db, setting(db, "frontend_origin"))
        return {"providers": providers, "any_available": any(item["available"] for item in providers),
                "vietqr_mode": payment_providers.vietqr_mode(),
                "banks": [{"bin": code, "name": name} for code, name in bank_qr.BANKS],
                "encryption": {"available": secret_box.available(), "variable": secret_box.KEY_VARIABLE}}


@app.put("/api/admin/payment-config/{provider}")
async def update_payment_config(provider: str, request: Request):
    """Save a gateway's configuration: encrypted at rest, effective at once, audited by field name."""
    same_origin(request)
    provider = _payment_provider(provider)
    with Session() as db:
        admin_for(request, db)  # before reading the body: a non-admin learns nothing about its validation
    data = await _payment_body(request, PAYMENT_CONFIG_INPUT[provider])
    if provider == "bank_qr":
        with Session.begin() as db:
            admin = admin_for(request, db)
            try:
                changed = _save_bank_qr(db, admin.id, data)
            except system_config.ConfigError as exc:
                raise _config_failure(exc) from None
        log_event(logger, "payment_config_saved", provider=provider, actions="updated", changed=",".join(changed))
        with Session() as db:
            return _payment_view(db, provider)
    updates = {spec.name: (getattr(data, spec.name).action, getattr(data, spec.name).value or "")
               for spec in payment_config.FIELDS[provider]}
    urls = {"payment_url": data.payment_url or "", "query_url": data.query_url or ""} if provider == "onepay" else None
    with Session.begin() as db:
        admin = admin_for(request, db)
        try:
            actions = payment_config.save(db, provider, admin.id, enabled=data.enabled,
                                          mode=getattr(data, "mode", None), updates=updates, urls=urls,
                                          confirm_production=getattr(data, "confirm_production", False))
            if getattr(data, "use_for_vietqr", False):
                system_config.save(db, admin.id, values={"payments.vietqr_mode": "payos"}, section="payments")
        except payment_config.ConfigError as exc:
            raise _config_failure(exc) from None
    changed = sorted(name for name, (action, _) in updates.items() if action != "keep")
    log_event(logger, "payment_config_saved", provider=provider, actions=",".join(actions) or "none",
              changed=",".join(changed))
    with Session() as db:
        return _payment_view(db, provider)


@app.post("/api/admin/payment-config/{provider}/disable")
def disable_payment_provider(provider: str, request: Request):
    """Stop new checkouts; historical orders are untouched and pending ones still settle."""
    same_origin(request)
    provider = _payment_provider(provider)
    with Session.begin() as db:
        admin = admin_for(request, db)
        if provider == "bank_qr":
            system_config.save(db, admin.id, values={"payments.bank_qr.enabled": False}, section="payments")
        else:
            payment_config.set_enabled(db, provider, admin.id, False)
    log_event(logger, "payment_config_saved", provider=provider, actions="disabled", changed="")
    with Session() as db:
        return _payment_view(db, provider)


@app.post("/api/admin/payment-config/{provider}/enable")
async def enable_payment_provider(provider: str, request: Request):
    same_origin(request)
    provider = _payment_provider(provider)
    with Session() as db:
        admin_for(request, db)
    data = await _payment_body(request, PaymentSwitchInput)
    with Session.begin() as db:
        admin = admin_for(request, db)
        try:
            if provider == "bank_qr":
                if not bank_qr.configured():
                    raise payment_config.ConfigError("not_configured")
                system_config.save(db, admin.id, values={"payments.bank_qr.enabled": True}, section="payments")
            else:
                payment_config.set_enabled(db, provider, admin.id, True, confirm_production=data.confirm_production)
        except payment_config.ConfigError as exc:
            raise _config_failure(exc) from None
    log_event(logger, "payment_config_saved", provider=provider, actions="enabled", changed="")
    with Session() as db:
        return _payment_view(db, provider)


def _run_payment_check(request: Request, provider: str, remote: bool) -> dict:
    with Session() as db:
        admin = admin_for(request, db)
    if provider == "bank_qr":
        missing = bank_qr.problems(bank_qr.settings())
        return {"provider": provider, "source": "admin",
                "local": {"status": "error", "code": "missing"} if missing else {"status": "ok"},
                "remote": {"status": "unsupported" if remote else "skipped"},
                "checked_at": datetime.now(timezone.utc).isoformat()}
    result = payment_setup.check(provider, remote=remote)
    if result["local"]["status"] == "ok" and result["remote"]["status"] in ("ok", "skipped"):
        payment_setup.record_activity(provider, "check")
    with Session.begin() as db:
        payment_config.audit(db, provider, "tested", admin.id, source=result["source"], remote=remote,
                             local=result["local"]["status"], remote_status=result["remote"]["status"])
    log_event(logger, "payment_config_checked", provider=provider, remote=remote,
              local=result["local"]["status"], remote_status=result["remote"]["status"])
    return {**result, "checked_at": datetime.now(timezone.utc).isoformat()}


@app.post("/api/admin/payment-config/{provider}/check")
async def check_payment_provider(provider: str, request: Request):
    """Validate the configuration in use. Never creates an order, charges, activates a plan or awards credits."""
    same_origin(request)
    provider = _payment_provider(provider)
    with Session() as db:
        admin_for(request, db)
    data = await _payment_body(request, PaymentCheckInput)
    return _run_payment_check(request, provider, data.remote)


@app.post("/api/admin/payment-config/bank_qr/preview")
async def preview_bank_qr(request: Request):
    """A sample QR (100,000 VND, content <prefix>TEST01) for the values on screen; nothing is saved or sent."""
    same_origin(request)
    with Session() as db:
        admin_for(request, db)
    data = await _payment_body(request, BankQRPreviewInput)
    values = {name: getattr(data, name).strip() for name in ("bank_bin", "account_number", "account_name",
                                                               "transfer_prefix")}
    values["transfer_prefix"] = values["transfer_prefix"].upper()
    if missing := bank_qr.problems(values):
        raise HTTPException(422, {"code": "missing", "field": f"payments.bank_qr.{missing[0]}",
                                  "message": "Incomplete bank details"})
    return bank_qr.preview(values)


@app.post("/api/admin/payment-config/check")
def admin_payment_check(data: LegacyPaymentCheckInput, request: Request):
    """Phase 18 route, kept for older clients: the same check with the provider in the body."""
    same_origin(request)
    return _run_payment_check(request, data.provider, data.remote)


# --- Phase 20: central system settings (system admins only) ------------------------------------------------
# The same write-only rules as payment gateways: secrets are encrypted at rest, never returned, never echoed.

ADMIN_SECTIONS = ("ai", "social", "storage", "runtime", "credits", "notifications")


class SystemConfigInput(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, SecretUpdate] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list, max_length=100)
    # Required to point the media root elsewhere while files exist; files are never moved.
    confirm_root_change: bool = False


class StorageCheckInput(BaseModel):
    root: str = Field(default="", max_length=1000)


def _stored_files(db) -> int:
    return count_of(db, select(Asset.id).where(Asset.bytes > 0))


AI_KEYS = {"openai": "ai.openai.api_key", "anthropic": "ai.anthropic.api_key", "gemini": "ai.gemini.api_key",
           "runway": "ai.runway.api_secret", "fal": "ai.fal.api_key", "runware": "ai.runware.api_key",
           "replicate": "ai.replicate.api_token"}


def _ai_status(db, used: dict) -> dict:
    """Per provider: switched on, a usable key, where the key comes from, models using it, the last test."""
    tests = {}
    for entry in system_config.history(db, "ai", limit=100):
        provider = entry["metadata"].get("provider")
        if entry["action"] == "tested" and provider and provider not in tests:
            tests[provider] = {"at": entry["at"], "by": entry["by"], "local": entry["metadata"].get("local"),
                               "remote": entry["metadata"].get("remote")}
    return {name: {"enabled": bool(system_config.get(f"ai.{name}.enabled")),
                   "configured": bool(system_config.get(key)), "source": system_config.source(key),
                   "models_in_use": int(used.get(name, 0)), "last_test": tests.get(name)}
            for name, key in AI_KEYS.items()}


def _system_overview(db) -> dict:
    origin = setting(db, "frontend_origin").rstrip("/")
    used = dict(db.execute(select(AITool.provider, func.count(AITool.id)).where(AITool.is_enabled.is_(True))
                           .group_by(AITool.provider)).all())
    return {
        "sections": {name: system_config.section_view(db, name) for name in ADMIN_SECTIONS},
        "history": {name: system_config.history(db, name) for name in ADMIN_SECTIONS},
        "redirects": {channel: system_config.redirect_uri(channel) for channel in system_config.REDIRECT_PATHS},
        "derived_redirects": {channel: f"{origin}{path}" for channel, path in system_config.REDIRECT_PATHS.items()},
        "models_in_use": {name: int(used.get(name, 0)) for name in system_config.AI_PROVIDERS},
        "ai_status": _ai_status(db, used),
        "legacy_in_use": system_config.legacy_in_use(),
        "environment": system_config.environment_report(),
        "runtime_file": {"path": runtime_env.LOADED["path"], "values": len(runtime_env.LOADED["names"])},
        "cache_seconds": system_config.CACHE_SECONDS,
        "master_key": {**master_key.status(), "encryption_available": secret_box.available()},
        "storage": {"root": str(storage.media_root(db)), "source": system_config.source("storage.root"),
                    "files": _stored_files(db), "disk": storage.disk_usage(db)},
        "migrated": system_config.ready(db),
    }


@app.get("/api/admin/system-config")
def admin_system_config(request: Request):
    """Every admin-managed setting, where its value comes from (admin, environment, default), never a secret."""
    with Session() as db:
        admin_for(request, db)
        return _system_overview(db)


@app.put("/api/admin/system-config/{section}")
async def update_system_config(section: str, request: Request):
    """Save one section; values apply at once here and within seconds in every worker, without a restart."""
    same_origin(request)
    if section not in ADMIN_SECTIONS:
        raise HTTPException(404, "Unknown settings section")
    with Session() as db:
        admin_for(request, db)  # before reading the body: a non-admin learns nothing about its validation
    data = await _payment_body(request, SystemConfigInput)
    secrets = {key: (update.action, update.value or "") for key, update in data.secrets.items()}
    with Session.begin() as db:
        admin = admin_for(request, db)
        if section == "storage" and ("storage.root" in data.values or "storage.root" in data.reset):
            current = storage.media_root(db)
            if "storage.root" in data.values:
                raw = data.values["storage.root"]
                check = config_checks.storage_root(raw if isinstance(raw, str) else "")
                if not check["ok"]:
                    raise HTTPException(422, {"code": "invalid_storage_root", "field": "storage.root",
                                              "problem": check["problem"], "message": "Storage root not usable"})
                moves = Path(check["path"]).resolve() != current.resolve()
            else:
                moves = True  # back to the environment or the stored folder: possibly elsewhere
            files = _stored_files(db)
            if moves and files and not data.confirm_root_change:
                raise HTTPException(409, {"code": "root_change_requires_confirmation", "field": "storage.root",
                                          "files": files, "message": "Existing files are not moved"})
        try:
            changed = system_config.save(db, admin.id, values=data.values, secrets=secrets, reset=data.reset,
                                         section=section)
        except system_config.ConfigError as exc:
            raise _settings_failure(exc) from None
    log_event(logger, "system_settings_saved", section=section, changed=",".join(changed))
    with Session() as db:
        return _system_overview(db)


@app.post("/api/admin/system-config/ai/{provider}/test")
def test_ai_provider_config(provider: str, request: Request):
    """Local check, then one free authenticated listing request; never generates anything or spends credits."""
    same_origin(request)
    if provider not in system_config.AI_PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    with Session() as db:
        admin = admin_for(request, db)
    result = config_checks.test_ai_provider(provider)
    with Session.begin() as db:
        system_config.audit(db, "ai", "tested", admin.id, provider=provider, local=result["local"]["status"],
                            remote=result["remote"]["status"])
    log_event(logger, "ai_provider_tested", provider=provider, local=result["local"]["status"],
              remote=result["remote"]["status"])
    return {**result, "checked_at": datetime.now(timezone.utc).isoformat()}


@app.post("/api/admin/system-config/storage/check")
def check_storage_root(data: StorageCheckInput, request: Request):
    """Whether a proposed media root is usable (absolute, existing, writable, not a link); nothing is saved."""
    same_origin(request)
    with Session() as db:
        admin_for(request, db)
        current = storage.media_root(db)
        files = _stored_files(db)
    result = config_checks.storage_root(data.root)
    moves = bool(result["ok"]) and Path(result["path"]).resolve() != current.resolve()
    return {**result, "current": str(current), "moves": moves, "files": files}


# --- Phase 18D: readiness and the manual live-verification checklist (system admins only) ----------------

class VerificationInput(BaseModel):
    verified: bool
    note: str | None = Field(default=None, max_length=1000)


@app.get("/api/admin/readiness")
def admin_readiness(request: Request):
    """Local, safe checks only: nothing paid, nothing published, no secret value."""
    with Session() as db:
        admin_for(request, db)
        return readiness.report(db, streams=_open_streams, poll_seconds=_sse_settings()[0])


@app.get("/api/admin/verification")
def admin_verification(request: Request):
    with Session() as db:
        admin_for(request, db)
        rows = {row.key: row for row in db.scalars(select(VerificationCheck))}
        emails = dict(db.execute(select(User.id, User.email).where(
            User.id.in_([row.verified_by_user_id for row in rows.values() if row.verified_by_user_id]))).all())
        return {"items": [{"key": key, "group": group, "paid": paid, "how": how,
                           "verified": bool(rows.get(key) and rows[key].verified_at),
                           "verified_at": _iso_or_none(rows[key].verified_at) if key in rows else None,
                           "verified_by": emails.get(rows[key].verified_by_user_id) if key in rows else None,
                           "note": rows[key].note if key in rows else None}
                          for key, group, paid, how in readiness.CHECKLIST]}


@app.put("/api/admin/verification/{key}")
def update_verification(key: str, data: VerificationInput, request: Request):
    """Record (or clear) a manual check; it is never marked verified by the server itself."""
    same_origin(request)
    if key not in readiness.CHECKLIST_KEYS:
        raise HTTPException(404, "Unknown check")
    with Session.begin() as db:
        admin = admin_for(request, db)
        now = datetime.now(timezone.utc)
        row = db.get(VerificationCheck, key) or VerificationCheck(key=key, updated_at=now)
        row.verified_at, row.verified_by_user_id = (now, admin.id) if data.verified else (None, None)
        row.note = (data.note or "").strip() or None
        row.updated_at = now
        db.add(row)
        return {"key": key, "verified": data.verified, "verified_at": _iso_or_none(row.verified_at),
                "verified_by": admin.email if data.verified else None, "note": row.note}


# --- Phase 18C: support, admin side ------------------------------------------------------------------------

class SupportAdminUpdate(BaseModel):
    status: Literal["open", "waiting_support", "waiting_user", "resolved", "closed"] | None = None
    priority: Literal["normal", "high"] | None = None


class SupportReplyInput(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    # An admin may set the status with the reply (for example "resolved").
    status: Literal["open", "waiting_support", "waiting_user", "resolved", "closed"] | None = None


def support_error(exc: support.SupportError):
    raise HTTPException(409 if exc.code == "ticket_closed" else 422, {"code": exc.code, "message": str(exc)}) from exc


def _ticket_detail(db, ticket, *, for_admin: bool) -> dict:
    creator = db.get(User, ticket.created_by_user_id)
    workspace = db.get(Workspace, ticket.workspace_id)
    rows = db.execute(select(SupportMessage, User.email).join(User, User.id == SupportMessage.author_user_id)
                      .where(SupportMessage.ticket_id == ticket.id)
                      .order_by(SupportMessage.created_at, SupportMessage.id)).all()
    # Users see "support" for admin messages, never the admin's own account.
    messages = [support.message_data(message, email if for_admin or message.author_type == "user" else None)
                for message, email in rows]
    return {**support.ticket_data(ticket, creator_email=creator.email if creator else None,
                                  workspace_name=workspace.name if workspace else None, messages=len(messages)),
            "thread": messages}


@app.get("/api/admin/support")
def admin_support(request: Request, q: str | None = Query(default=None, max_length=255),
                  status: Literal["open", "waiting_support", "waiting_user", "resolved", "closed"] | None = None,
                  category: str | None = None, priority: Literal["normal", "high"] | None = None,
                  limit: int = ADMIN_LIMIT, offset: int = ADMIN_OFFSET):
    """Support requests one page at a time; ``q`` matches the ticket ID, subject, user email or studio."""
    with Session() as db:
        admin_for(request, db)
        query = (select(SupportTicket, User.email, Workspace.name)
                 .join(User, User.id == SupportTicket.created_by_user_id)
                 .join(Workspace, Workspace.id == SupportTicket.workspace_id))
        if q and q.strip():
            pattern = like_pattern(q)
            query = query.where(or_(func.lower(SupportTicket.subject).like(pattern, escape="\\"),
                                    func.lower(User.email).like(pattern, escape="\\"),
                                    func.lower(Workspace.name).like(pattern, escape="\\"),
                                    SupportTicket.id.like(pattern[1:], escape="\\")))
        if status:
            query = query.where(SupportTicket.status == status)
        if category:
            query = query.where(SupportTicket.category == category)
        if priority:
            query = query.where(SupportTicket.priority == priority)
        total = count_of(db, query)
        rows = db.execute(query.order_by(SupportTicket.updated_at.desc(), SupportTicket.id)
                          .limit(limit).offset(offset)).all()
        return {"items": [support.ticket_data(ticket, creator_email=email, workspace_name=name)
                          for ticket, email, name in rows], "total": total, "limit": limit, "offset": offset}


def _admin_ticket(db, ticket_id: str) -> SupportTicket:
    ticket = db.scalar(select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update())
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


@app.get("/api/admin/support/{ticket_id}")
def admin_support_ticket(ticket_id: str, request: Request):
    with Session() as db:
        admin_for(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if ticket is None:
            raise HTTPException(404, "Ticket not found")
        return _ticket_detail(db, ticket, for_admin=True)


@app.post("/api/admin/support/{ticket_id}/messages", status_code=201)
def admin_support_reply(ticket_id: str, data: SupportReplyInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        admin = admin_for(request, db)
        ticket = _admin_ticket(db, ticket_id)
        try:
            support.add_message(db, ticket, admin, data.body, as_admin=True)
            if data.status:
                support.set_status(db, ticket, data.status, by_admin=True)
        except support.SupportError as exc:
            support_error(exc)
        db.flush()
        return _ticket_detail(db, ticket, for_admin=True)


@app.patch("/api/admin/support/{ticket_id}")
def admin_support_update(ticket_id: str, data: SupportAdminUpdate, request: Request):
    same_origin(request)
    with Session.begin() as db:
        admin_for(request, db)
        ticket = _admin_ticket(db, ticket_id)
        try:
            if data.priority:
                support.set_priority(ticket, data.priority)
            if data.status:
                support.set_status(db, ticket, data.status, by_admin=True)
        except support.SupportError as exc:
            support_error(exc)
        db.flush()
        return _ticket_detail(db, ticket, for_admin=True)


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
        values = data.model_dump()
        if "storage_limit_bytes" not in data.model_fields_set:
            values.pop("storage_limit_bytes")  # an older client: keep the plan's storage limit
        for field, value in values.items():
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
                      start: datetime | None = None, end: datetime | None = None,
                      limit: int = Query(default=100, ge=1, le=300), offset: int = Query(default=0, ge=0)):
    """Publications of every channel, newest first, one page at a time; ``start``/``end`` (UTC) select a
    calendar range by the scheduled time, else the publishing time, else the creation time."""
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
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(query.order_by(when.desc(), Publication.id).limit(limit).offset(offset))
        return {"publications": [public_publication(row) for row in rows], "total": total, "limit": limit,
                "offset": offset}


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


SCRIPT_NODES = {"ai_writer": "script", "recap_script": "script", "rewrite": "text", "translate": "text",
                "summarize": "summary"}


@app.get("/api/scripts")
def list_scripts(request: Request, project_id: str | None = None,
                 limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0)):
    """Text that finished runs wrote (AI Writer, Recap Script, Rewrite, Translate, Summarize), newest first."""
    with Session() as db:
        ws = workspace_for(request, db)
        query = (select(WorkflowRunStep, WorkflowRun)
                 .join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id)
                 .where(WorkflowRun.workspace_id == ws.id, WorkflowRunStep.status == "completed",
                        WorkflowRunStep.node_type.in_(tuple(SCRIPT_NODES))))
        if project_id:
            query = query.where(WorkflowRun.project_id == project_id)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = []
        for step, run in db.execute(query.order_by(WorkflowRun.created_at.desc(), WorkflowRunStep.position)
                                    .limit(limit).offset(offset)):
            output = json.loads(step.output) if step.output else {}
            text = output.get(SCRIPT_NODES[step.node_type]) or output.get("text") if isinstance(output, dict) else None
            text = text if isinstance(text, str) else ""
            items.append({"step_id": step.id, "run_id": run.id, "project_id": run.project_id,
                          "workflow_id": run.workflow_id, "node_type": step.node_type, "node_id": step.node_id,
                          "text": text[:6000], "words": len(text.split()), "truncated": len(text) > 6000,
                          "created_at": run.created_at.isoformat()})
        return {"items": items, "total": total, "limit": limit, "offset": offset}


def like_pattern(text: str) -> str:
    """A case-insensitive ``LIKE`` pattern for ``text`` with its wildcards escaped (used with ``escape='\\'``)."""
    escaped = text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class AssetPatch(BaseModel):
    project_id: str | None = None


@app.patch("/api/assets/{asset_id}")
def update_asset(asset_id: str, data: AssetPatch, request: Request):
    """Attach an uploaded file to one of the workspace's projects (or detach it). Generated media keeps its run's project."""
    same_origin(request)
    with Session.begin() as db:
        ws = workspace_for(request, db)
        asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == ws.id))
        if not asset:
            raise HTTPException(404, "Asset not found")
        if asset.run_id is not None:
            raise HTTPException(409, "Generated media belongs to its run's project")
        if data.project_id is not None and not db.scalar(
                select(Project.id).where(Project.id == data.project_id, Project.workspace_id == ws.id)):
            raise HTTPException(404, "Project not found")
        asset.project_id = data.project_id
        return {"id": asset.id, "project_id": asset.project_id}


@app.post("/api/assets", status_code=201)
def upload_asset(request: Request, file: UploadFile = File(...)):
    same_origin(request)
    content_type = upload_content_type(file.content_type or "", file.filename or "")
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Unsupported media type")
    with Session.begin() as db:
        ws = workspace_for(request, db)
        active_plan(db, ws)
        # Serialize quota checks for simultaneous uploads (and workers) in the same workspace.
        storage.lock_workspace(db, ws.id)
        stored_bytes = storage.stored_bytes(db, ws.id)
        quota = storage.quota_bytes(db, ws.id)
        if stored_bytes >= quota:
            raise HTTPException(413, {"code": "storage_full", "message": "Workspace media quota reached"})
        asset_id = ident()
        filename = Path(file.filename or "upload").name[:255]
        target = storage.asset_path(db, ws.id, asset_id)
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
            asset = Asset(id=asset_id, workspace_id=ws.id, filename=filename, bytes=size, content_type=content_type,
                          kind=storage.SOURCE)
            db.add(asset)
            notifications.storage_crossed(db, ws.id, stored_bytes, stored_bytes + size, quota)
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
        if asset.bytes == 0 and asset.expired_at is not None:
            raise HTTPException(410, {"code": "media_expired", "reason": asset.expired_reason,
                                      "message": "This media file was removed"})
        path = storage.asset_path(db, ws.id, asset.id)
        if not path.is_file():
            raise HTTPException(404, "Media file missing")
        return FileResponse(path, media_type=asset.content_type, filename=asset.filename)


def workspace_owner(request: Request, db):
    ws = workspace_for(request, db)
    membership = db.get(Membership, (authorize(request, db).id, ws.id))
    if not membership or membership.role != "owner":
        raise HTTPException(403, "Workspace owner required")
    return ws


def delete_media(request: Request, asset_ids: list[str]) -> dict:
    """Remove media a user chose. Each row stays with 0 bytes (lineage, run history); its file goes after commit.
    Media an unfinished publication still needs is skipped."""
    same_origin(request)
    now = datetime.now(timezone.utc)
    deleted, skipped, freed = [], [], 0
    with Session.begin() as db:
        ws = workspace_owner(request, db)
        root = storage.media_root(db)
        storage.lock_workspace(db, ws.id)
        rows = {row.id: row for row in db.scalars(select(Asset).where(Asset.workspace_id == ws.id,
                                                                       Asset.id.in_(asset_ids)).with_for_update())}
        for asset_id in dict.fromkeys(asset_ids):
            asset = rows.get(asset_id)
            if asset is None or asset.expired_at is not None:
                skipped.append({"id": asset_id, "reason": "not_found"})
            elif blocker := storage.deletion_blocker(db, asset):
                skipped.append({"id": asset_id, "reason": blocker})
            elif media_maintenance.check_asset_file(root, ws.id, asset.id) == "unsafe":
                skipped.append({"id": asset_id, "reason": "unsafe_path"})
            else:
                freed += storage.expire(db, asset, reason="deleted", now=now)
                deleted.append(asset_id)
    for asset_id in deleted:
        media_maintenance.remove_asset_file(root, ws.id, asset_id)
    log_event(logger, "media_deleted", workspace_id=ws.id, assets=len(deleted), freed_bytes=freed,
              skipped=len(skipped))
    return {"deleted": deleted, "skipped": skipped, "freed_bytes": freed}


class MediaDeleteInput(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=100)


class StorageCleanupInput(BaseModel):
    project_id: str | None = None
    apply: bool = False


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str, request: Request):
    result = delete_media(request, [asset_id])
    if not result["deleted"]:
        reason = result["skipped"][0]["reason"]
        if reason == "not_found":
            raise HTTPException(404, "Asset not found")
        raise HTTPException(409, {"code": reason, "message": "A publication still needs this media"})
    return result


@app.post("/api/assets/delete")
def delete_assets(data: MediaDeleteInput, request: Request):
    return delete_media(request, data.asset_ids)


# --- Phase 18B: notifications ----------------------------------------------------------------------------

def _sse_settings() -> tuple[float, float]:
    """(seconds between checks, seconds before the server ends a stream so the client reconnects)."""
    def number(name, default, low, high):
        try:
            return min(high, max(low, float(system_config.env(name).strip() or default)))
        except ValueError:
            return default
    return number("REELFORGE_SSE_POLL_SECONDS", 3, 0.1, 30), number("REELFORGE_SSE_MAX_SECONDS", 300, 1, 3600)


_open_streams = 0
SSE_HEARTBEAT_SECONDS = 15


@app.get("/api/notifications")
def list_notifications(request: Request, limit: int = Query(default=20, ge=1, le=100),
                       offset: int = Query(default=0, ge=0), unread: bool = False):
    with Session() as db:
        user = authorize(request, db)
        query = select(Notification).where(notifications.visible(user.id))
        if unread:
            query = query.where(Notification.read_at.is_(None))
        total = count_of(db, query)
        rows = db.scalars(query.order_by(Notification.id.desc()).limit(limit).offset(offset)).all()
        return {"items": [notifications.public(row) for row in rows], "total": total, "limit": limit,
                "offset": offset, "unread": notifications.unread_count(db, user.id)}


@app.get("/api/notifications/unread-count")
def notifications_unread(request: Request):
    with Session() as db:
        return {"unread": notifications.unread_count(db, authorize(request, db).id)}


@app.post("/api/notifications/read-all")
def notifications_read_all(request: Request):
    same_origin(request)
    with Session.begin() as db:
        user = authorize(request, db)
        updated = notifications.mark_read(db, user.id)
        return {"updated": updated, "unread": notifications.unread_count(db, user.id)}


@app.post("/api/notifications/{notification_id}/read")
def notification_read(notification_id: int, request: Request):
    same_origin(request)
    with Session.begin() as db:
        user = authorize(request, db)
        if not db.scalar(select(Notification.id).where(Notification.id == notification_id,
                                                       notifications.visible(user.id))):
            raise HTTPException(404, "Notification not found")
        notifications.mark_read(db, user.id, notification_id)
        return {"id": notification_id, "unread": notifications.unread_count(db, user.id)}


def _stream_start(request: Request) -> tuple[str, int]:
    with Session() as db:
        user = authorize(request, db)
        return user.id, notifications.latest_id(db, user.id)


def _stream_poll(request: Request, last_id: int) -> tuple[list[dict], int]:
    # The session is checked on every pass: signing out (or being locked) ends the stream.
    with Session() as db:
        user = authorize(request, db)
        return ([notifications.public(row) for row in notifications.newer_than(db, user.id, last_id)],
                notifications.unread_count(db, user.id))


@app.get("/api/notifications/stream")
async def notification_stream(request: Request):
    """Server-Sent Events: ``notification`` events (with an ``id`` to resume from) and ``unread`` counts.

    Authenticated by the session cookie like every other request. A ``Last-Event-ID``
    header (sent by the browser when it reconnects) replays what was missed; a new
    stream starts from now. A ``: ping`` comment keeps idle proxies from closing it,
    and the server ends each stream after ``REELFORGE_SSE_MAX_SECONDS`` so clients
    reconnect (``retry: 5000``) through restarts and proxy timeouts.
    """
    user_id, latest = await asyncio.to_thread(_stream_start, request)
    resume = request.headers.get("last-event-id", "").strip()
    start = int(resume) if resume.isdigit() else latest
    poll, lifetime = _sse_settings()

    async def events():
        global _open_streams
        _open_streams += 1
        last_id, unread, began = start, None, time.monotonic()
        quiet_since = began
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    rows, count = await asyncio.to_thread(_stream_poll, request, last_id)
                except HTTPException:
                    yield 'event: end\ndata: {"reason": "signed_out"}\n\n'
                    break
                for row in rows:
                    last_id = row["id"]
                    yield f"id: {row['id']}\nevent: notification\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if count != unread or rows:
                    unread, quiet_since = count, time.monotonic()
                    yield f'event: unread\ndata: {{"unread": {count}}}\n\n'
                elif time.monotonic() - quiet_since >= SSE_HEARTBEAT_SECONDS:
                    quiet_since = time.monotonic()
                    yield ": ping\n\n"
                if time.monotonic() - began >= lifetime:
                    break
                await asyncio.sleep(poll)
        finally:
            _open_streams -= 1

    log_event(logger, "notification_stream_opened", user_id=user_id, resumed=bool(resume.isdigit()))
    # no-transform keeps proxies (and Next.js) from compressing, which would buffer the events.
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# --- Phase 18C: support, user side -----------------------------------------------------------------------

class SupportTicketInput(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    category: Literal["billing", "credits", "generation", "publishing", "account", "storage", "bug", "other"]
    description: str = Field(min_length=1, max_length=5000)
    run_id: str | None = Field(default=None, max_length=36)
    project_id: str | None = Field(default=None, max_length=36)
    payment_order_id: str | None = Field(default=None, max_length=36)
    publication_id: str | None = Field(default=None, max_length=36)


class SupportMessageInput(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


def _support_scope(request: Request, db):
    """The user, their workspace, and whether they own it (owners see every ticket of the studio)."""
    user = authorize(request, db)
    ws = workspace_for(request, db)
    membership = db.get(Membership, (user.id, ws.id))
    return user, ws, bool(membership and membership.role == "owner")


def _visible_tickets(user, ws, owner: bool):
    condition = SupportTicket.workspace_id == ws.id
    return condition if owner else condition & (SupportTicket.created_by_user_id == user.id)


def _user_ticket(db, request: Request, ticket_id: str, *, lock: bool = False):
    user, ws, owner = _support_scope(request, db)
    query = select(SupportTicket).where(SupportTicket.id == ticket_id, _visible_tickets(user, ws, owner))
    ticket = db.scalar(query.with_for_update() if lock else query)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return user, ticket


@app.get("/api/support/tickets")
def list_support_tickets(request: Request, limit: int = Query(default=20, ge=1, le=100),
                         offset: int = Query(default=0, ge=0)):
    with Session() as db:
        user, ws, owner = _support_scope(request, db)
        query = select(SupportTicket).where(_visible_tickets(user, ws, owner))
        total = count_of(db, query)
        rows = db.scalars(query.order_by(SupportTicket.updated_at.desc(), SupportTicket.id)
                          .limit(limit).offset(offset)).all()
        counts = dict(db.execute(select(SupportMessage.ticket_id, func.count(SupportMessage.id))
                                 .where(SupportMessage.ticket_id.in_([row.id for row in rows]))
                                 .group_by(SupportMessage.ticket_id)).all())
        return {"items": [support.ticket_data(row, messages=int(counts.get(row.id, 0))) for row in rows],
                "total": total, "limit": limit, "offset": offset}


@app.post("/api/support/tickets", status_code=201)
def create_support_ticket(data: SupportTicketInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        user, ws, _ = _support_scope(request, db)
        try:
            ticket = support.create_ticket(db, workspace_id=ws.id, user=user, subject=data.subject,
                                           category=data.category, body=data.description,
                                           context={key: getattr(data, key) for key in
                                                    ("run_id", "project_id", "payment_order_id", "publication_id")})
        except support.SupportError as exc:
            support_error(exc)
        db.flush()
        log_event(logger, "support_ticket_created", workspace_id=ws.id, ticket_id=ticket.id, category=data.category)
        return _ticket_detail(db, ticket, for_admin=False)


@app.get("/api/support/tickets/{ticket_id}")
def support_ticket(ticket_id: str, request: Request):
    with Session() as db:
        _, ticket = _user_ticket(db, request, ticket_id)
        return _ticket_detail(db, ticket, for_admin=False)


@app.post("/api/support/tickets/{ticket_id}/messages", status_code=201)
def reply_support_ticket(ticket_id: str, data: SupportMessageInput, request: Request):
    same_origin(request)
    with Session.begin() as db:
        user, ticket = _user_ticket(db, request, ticket_id, lock=True)
        try:
            support.add_message(db, ticket, user, data.body, as_admin=False)
        except support.SupportError as exc:
            support_error(exc)
        db.flush()
        return _ticket_detail(db, ticket, for_admin=False)


@app.post("/api/support/tickets/{ticket_id}/close")
def close_support_ticket(ticket_id: str, request: Request):
    same_origin(request)
    with Session.begin() as db:
        _, ticket = _user_ticket(db, request, ticket_id, lock=True)
        support.set_status(db, ticket, "closed", by_admin=False)
        db.flush()
        return _ticket_detail(db, ticket, for_admin=False)


@app.post("/api/storage/cleanup")
def storage_cleanup(data: StorageCleanupInput, request: Request):
    """Preview (default) or remove the workspace's intermediate media whose run already has its final render."""
    same_origin(request)
    now = datetime.now(timezone.utc)
    with Session.begin() as db:
        ws = workspace_owner(request, db) if data.apply else workspace_for(request, db)
        if data.project_id is not None and not db.scalar(
                select(Project.id).where(Project.id == data.project_id, Project.workspace_id == ws.id)):
            raise HTTPException(404, "Project not found")
        if not data.apply:
            return {**intermediate_summary(db, ws.id, data.project_id), "applied": False}
        root = storage.media_root(db)
        storage.lock_workspace(db, ws.id)
        # A file whose path the safety check refuses is left alone, with its row.
        found = [asset for asset in db.scalars(storage.expirable_query(workspace_id=ws.id, project_id=data.project_id)
                                               .with_for_update())
                 if media_maintenance.check_asset_file(root, ws.id, asset.id) != "unsafe"]
        freed = sum(storage.expire(db, asset, reason="cleanup", now=now) for asset in found)
        removed = [asset.id for asset in found]
    for asset_id in removed:
        media_maintenance.remove_asset_file(root, ws.id, asset_id)
    log_event(logger, "media_cleanup", workspace_id=ws.id, assets=len(removed), freed_bytes=freed)
    return {"assets": len(removed), "bytes": freed, "applied": True}
