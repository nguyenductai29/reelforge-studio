"""The general audit log (Phase 24): security and administration events in ``audit_events``.

``record(db, action, …)`` adds one event in the caller's transaction, so a rolled-back
change leaves no trace; ``record_now`` writes in its own transaction for events that must
survive the request failing (a refused sign-in, a denied action).

An event says who (``actor_user_id``), what (``action``, ``outcome``), on what
(``workspace_id``, ``target_type``/``target_id``), from where (the resolved client address
and the request ID) and when. ``details`` are short display values. Keys that name a
secret are dropped whatever their value, and so are long values: an audit row never holds
a password, token, code, key or secret.

Admin → Audit lists events newest first, with server-side filters and pagination.
"""
from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import func, inspect, or_, select

from app import request_context
from app.models import AuditEvent, User, Workspace

# Every action written, for the admin filter (prefix filters such as "auth." work too).
ACTIONS = (
    "auth.login", "auth.login_blocked", "auth.logout", "auth.two_factor",
    "account.password_changed", "account.password_reset_requested", "account.password_reset",
    "account.email_verified", "account.verification_sent", "account.two_factor_enabled",
    "account.two_factor_disabled", "account.recovery_codes_regenerated", "account.recovery_code_used",
    "account.session_revoked", "account.sessions_revoked", "account.registered", "account.export",
    "account.closure_requested", "account.terms_accepted",
    "workspace.member_invited", "workspace.invite_revoked", "workspace.invite_accepted",
    "workspace.member_role_changed", "workspace.member_removed", "workspace.member_left",
    "workspace.ownership_transferred", "workspace.renamed", "workspace.settings_changed", "workspace.switched",
    "workspace.created",
    "billing.checkout_created",
    "channel.connected", "channel.disconnected",
    "admin.user_created", "admin.user_updated", "admin.user_two_factor_reset", "admin.user_email_verified",
    "admin.user_sessions_revoked", "admin.plan_updated", "admin.credits_adjusted", "admin.subscription_changed",
    "admin.payment_confirmed", "admin.payment_rejected", "admin.payment_config_changed",
    "admin.system_config_changed", "admin.system_settings_changed", "admin.support_replied",
    "admin.support_updated", "admin.reconciliation", "admin.verification_updated", "admin.email_test",
    "admin.master_key_backup_confirmed", "admin.user_password_reset", "payment.callback_rejected",
    "movie_source.created", "movie_source.import_started", "movie_source.ready", "movie_source.retention_extended",
    "movie_source.delete_requested", "movie_source.deleted", "movie_source.import_failed", "movie_source.drive_failed",
)
OUTCOMES = ("success", "failure", "denied")
_SECRET_WORDS = ("password", "token", "secret", "otp", "totp", "cipher", "authorization", "cookie", "api_key",
                 "apikey", "recovery", "private", "credential")
_DETAIL_LIMIT = 16
_ready = False


def ready(db) -> bool:
    global _ready
    if not _ready:
        _ready = inspect(db.connection()).has_table("audit_events")
    return _ready


def safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in list((details or {}).items())[:_DETAIL_LIMIT]:
        name = str(key)[:40]
        if any(word in name.lower() for word in _SECRET_WORDS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[name] = value
        elif isinstance(value, str):
            safe[name] = value[:200]
        elif isinstance(value, (list, tuple)):
            safe[name] = [str(item)[:80] for item in value[:20]]
    return safe


def record(db, action: str, *, outcome: str = "success", actor_id: str | None = None,
           workspace_id: str | None = None, target_type: str | None = None, target_id: str | None = None,
           details: dict[str, Any] | None = None, ip: str | None = None) -> None:
    if not ready(db):
        return
    address = ip or request_context.client_ip.get()
    db.add(AuditEvent(
        created_at=datetime.now(timezone.utc), action=action[:48], outcome=outcome if outcome in OUTCOMES else "success",
        actor_user_id=actor_id, workspace_id=workspace_id, target_type=target_type[:24] if target_type else None,
        target_id=str(target_id)[:64] if target_id else None, ip=str(address)[:64] if address else None,
        request_id=request_context.request_id.get(), details_json=json.dumps(safe_details(details), ensure_ascii=False)))


def record_now(action: str, *, session_factory=None, **fields) -> None:
    """``record`` in its own transaction (best effort: auditing never fails the caller)."""
    try:
        if session_factory is None:
            from app.db import Session as session_factory
        with session_factory.begin() as db:
            record(db, action, **fields)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("audit event not recorded", exc_info=True)


def view(event: AuditEvent, actor_email: str | None, workspace_name: str | None) -> dict:
    try:
        details = json.loads(event.details_json or "{}")
    except ValueError:
        details = {}
    created = event.created_at if event.created_at.tzinfo else event.created_at.replace(tzinfo=timezone.utc)
    return {"id": event.id, "at": created.isoformat(), "action": event.action, "outcome": event.outcome,
            "actor": {"id": event.actor_user_id, "email": actor_email} if event.actor_user_id else None,
            "workspace": {"id": event.workspace_id, "name": workspace_name} if event.workspace_id else None,
            "target_type": event.target_type, "target_id": event.target_id, "ip": event.ip,
            "request_id": event.request_id, "details": details}


def query(db, *, action: str | None = None, outcome: str | None = None, actor: str | None = None,
          workspace_id: str | None = None, actor_id: str | None = None, since: datetime | None = None,
          until: datetime | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """Newest first. ``action`` matches exactly or as a prefix ending in "."; ``actor`` matches an email part."""
    if not ready(db):
        return [], 0
    conditions = []
    if action:
        conditions.append(AuditEvent.action.startswith(action) if action.endswith(".") else AuditEvent.action == action)
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    if actor_id:
        conditions.append(AuditEvent.actor_user_id == actor_id)
    if actor:
        ids = select(User.id).where(User.email.ilike(f"%{actor.strip().lower()}%"))
        conditions.append(or_(AuditEvent.actor_user_id.in_(ids), AuditEvent.target_id.in_(ids)))
    if workspace_id:
        conditions.append(AuditEvent.workspace_id == workspace_id)
    if since:
        conditions.append(AuditEvent.created_at >= since)
    if until:
        conditions.append(AuditEvent.created_at < until)
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
    rows = db.execute(select(AuditEvent, User.email, Workspace.name)
                      .outerjoin(User, User.id == AuditEvent.actor_user_id)
                      .outerjoin(Workspace, Workspace.id == AuditEvent.workspace_id)
                      .where(*conditions).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                      .limit(limit).offset(offset)).all()
    return [view(event, email, name) for event, email, name in rows], int(total)
