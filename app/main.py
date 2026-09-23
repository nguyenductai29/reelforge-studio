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
from sqlalchemy.exc import IntegrityError

from app.db import ROOT, config, engine, Session
from app.models import User, LoginSession, Workspace, Membership, Project, Asset, Workflow, SystemSetting, WorkspaceSetting, Plan, Subscription, PaymentOrder
from app import billing


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
app = FastAPI(title="ReelForge Studio")
MAX_UPLOAD = 100 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/ogg"}


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


class SubscriptionInput(BaseModel):
    plan_code: str
    status: str = Field(pattern="^(active|paused|canceled)$")
    ends_at: datetime | None = None


class ActiveInput(BaseModel):
    is_active: bool


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
    return {"id": p.id, "title": p.title, "topic": p.topic, "status": p.status}


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
    with Session.begin() as db:
        user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
        if not user or not user.is_active or not check_password(data.password, user.password_hash):
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
        subscription = db.get(Subscription, ws.id)
        plan = db.get(Plan, subscription.plan_code) if subscription else None
        return {"workspace": {"id": ws.id, "name": ws.name, "plan": plan.code if plan else ws.plan, "subscription_status": subscription.status if subscription else "unavailable"}, "is_admin": user.is_admin, "projects": [public_project(p) for p in projects], "assets": [{"id": a.id, "filename": a.filename, "bytes": a.bytes, "content_type": a.content_type} for a in assets], "workflows": [{"id": w.id, "name": w.name, "graph": parse_graph(w.definition)} for w in workflows], "limits": {"projects": plan.project_limit if plan else None, "workflows": plan.workflow_limit if plan else None}}


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
                "subscription": {"plan_code": subscription.plan_code, "status": subscription.status,
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
        if not subscription or subscription.status != "active":
            raise HTTPException(403, "Subscription is inactive")
        if ("trial", "standard", "pro").index(data.plan_code) <= ("trial", "standard", "pro").index(subscription.plan_code):
            raise HTTPException(400, "Choose a higher plan")
        plan = db.get(Plan, data.plan_code)
        if not plan or not plan.is_active or not plan.price_vnd:
            raise HTTPException(400, "Plan price is not configured")
        origin = setting(db, "frontend_origin")
        order = PaymentOrder(id=ident(), workspace_id=ws.id, plan_code=plan.code,
                             provider="payos", order_code=secrets.randbelow(8_000_000_000_000) + 1_000_000_000_000,
                             amount_vnd=plan.price_vnd, status="pending", created_at=datetime.now(timezone.utc))
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
    with Session.begin() as db:
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == verified.order_code).with_for_update())
        if not order:
            return {"ok": True}  # payOS validation ping or unknown order.
        if order.status in ("paid", "paid_unapplied"):
            return {"ok": True}
        if order.status != "pending" or order.provider != "payos":
            return {"ok": True}
        if int(verified.amount) != order.amount_vnd or getattr(verified, "currency", "VND") != "VND":
            raise HTTPException(400, "Payment amount mismatch")
        order.provider_reference = str(getattr(verified, "reference", ""))[:128]
        order.paid_at = datetime.now(timezone.utc)
        subscription = db.scalar(select(Subscription).where(Subscription.workspace_id == order.workspace_id).with_for_update())
        if not subscription or subscription.starts_at.replace(tzinfo=timezone.utc) > order.created_at.replace(tzinfo=timezone.utc):
            order.status = "paid_unapplied"
            return {"ok": True}
        subscription.plan_code = order.plan_code
        subscription.status = "active"
        subscription.starts_at = order.paid_at
        subscription.ends_at = order.paid_at + timedelta(days=30)
        db.get(Workspace, order.workspace_id).plan = order.plan_code
        order.status = "paid"
    return {"ok": True}


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
                                "status": s.status if s else "unavailable",
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
        return {"id": workflow.id, "name": workflow.name, "graph": parse_graph(workflow.definition)}


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
        active_plan(db, ws)
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
