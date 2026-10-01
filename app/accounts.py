"""Account lifecycle (Phase 22): one-time tokens, sessions, password rules and the second factor.

**Tokens** (``account_tokens``): email verification (24 h), password reset (60 min) and the
second sign-in step (5 min, five tries). A token is 256 random bits sent once; only its
SHA-256 is stored, it expires, it works once, and issuing a new one for the same purpose
retires the previous one.

**Sessions** (``login_sessions``): a 7-day random token in an HttpOnly cookie; the database
keeps its SHA-256, a public id (to list and revoke sessions without exposing anything
usable), the user agent and client address it came from, and when it was created and
last seen. Changing or resetting the password, or enabling 2FA, revokes the other
sessions.

**Second factor**: TOTP (``app/totp.py``) with the secret encrypted by the master key
(purpose bound to the user, ``app/secret_box.py``), enrolled only after one correct code,
plus ten single-use recovery codes stored as digests and shown once.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import uuid

from sqlalchemy import delete, func, inspect, select, update

from app import secret_box, totp
from app.models import (AccountToken, AuditEvent, LoginSession, Membership, Notification, PaymentOrder,
                        RecoveryCode, SupportTicket, User, UserProfile, Workspace)

TERMS_VERSION = "2026-10-02"
VERIFY_LIFETIME = timedelta(hours=24)
RESET_LIFETIME = timedelta(minutes=60)
CHALLENGE_LIFETIME = timedelta(minutes=5)
CHALLENGE_ATTEMPTS = 5
SESSION_LIFETIME = timedelta(days=7)
SEEN_INTERVAL = timedelta(minutes=5)
PASSWORD_MIN, PASSWORD_MAX = 12, 256


def now() -> datetime:
    return datetime.now(timezone.utc)


_ready = {"value": False}


def ready(db) -> bool:
    """Whether migration 0022 is applied. Until then (an upgrade in progress) sessions are created without the
    new details and nothing reads them; a positive answer is remembered."""
    if not _ready["value"]:
        _ready["value"] = "email_verified_at" in {column["name"] for column in
                                                  inspect(db.connection()).get_columns("users")}
    return _ready["value"]


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value else None


def digest(raw: str) -> str:
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()


def password_problem(password: str, email: str) -> str | None:
    """None when acceptable; otherwise too_short, too_long or same_as_email."""
    if len(password) < PASSWORD_MIN:
        return "too_short"
    if len(password) > PASSWORD_MAX:
        return "too_long"
    if password.strip().lower() == email.strip().lower():
        return "same_as_email"
    return None


# --- one-time tokens -------------------------------------------------------------------------------------------

def issue_token(db, user: User, purpose: str, *, lifetime: timedelta, email: str | None = None,
                ip: str | None = None) -> str:
    """A new raw token (returned once, never stored); older unused tokens of the same purpose stop working."""
    moment = now()
    db.execute(update(AccountToken).where(AccountToken.user_id == user.id, AccountToken.purpose == purpose,
                                          AccountToken.used_at.is_(None)).values(used_at=moment))
    raw = secrets.token_urlsafe(32)
    db.add(AccountToken(id=str(uuid.uuid4()), user_id=user.id, purpose=purpose, token_hash=digest(raw), email=email,
                        created_at=moment, expires_at=moment + lifetime, attempts=0, ip=(ip or None) and ip[:64]))
    return raw


def live_token(db, raw: str | None, purpose: str, *, lock: bool = False) -> AccountToken | None:
    if not raw or len(raw) > 200:
        return None
    query = select(AccountToken).where(AccountToken.token_hash == digest(raw), AccountToken.purpose == purpose)
    if lock:
        query = query.with_for_update()
    token = db.scalar(query)
    if token is None or token.used_at is not None or aware(token.expires_at) <= now():
        return None
    return token


# --- sessions -----------------------------------------------------------------------------------------------------

def create_session(db, user: User, *, user_agent: str | None, ip: str | None) -> str:
    raw = secrets.token_urlsafe(48)
    moment = now()
    if not ready(db):
        # Before migration 0022: the old columns only (the ORM would name the new ones).
        db.execute(LoginSession.__table__.insert().values(token_hash=digest(raw), user_id=user.id,
                                                          expires_at=moment + SESSION_LIFETIME))
        return raw
    db.add(LoginSession(token_hash=digest(raw), user_id=user.id, expires_at=moment + SESSION_LIFETIME,
                        id=str(uuid.uuid4()), created_at=moment, last_seen_at=moment,
                        user_agent=(user_agent or "")[:255] or None, ip=(ip or None) and ip[:64],
                        active_workspace_id=user.last_workspace_id))
    user.last_login_at = moment
    return raw


def session_for(db, raw: str | None) -> LoginSession | None:
    if not raw or len(raw) > 200:
        return None
    row = db.get(LoginSession, digest(raw))
    if row is None or aware(row.expires_at) <= now():
        return None
    return row


def touch(session_factory, row: LoginSession) -> None:
    """Record activity at most every few minutes, in its own transaction (reads never write)."""
    if not _ready["value"]:
        return
    if row.last_seen_at is not None and now() - aware(row.last_seen_at) < SEEN_INTERVAL:
        return
    try:
        with session_factory.begin() as db:
            db.execute(update(LoginSession).where(LoginSession.token_hash == row.token_hash).values(last_seen_at=now()))
    except Exception:  # noqa: BLE001 - activity is informational
        pass


def revoke_sessions(db, user_id: str, *, keep_hash: str | None = None) -> int:
    query = delete(LoginSession).where(LoginSession.user_id == user_id)
    if keep_hash:
        query = query.where(LoginSession.token_hash != keep_hash)
    return db.execute(query).rowcount or 0


def sessions(db, user_id: str, current_hash: str | None) -> list[dict]:
    rows = db.scalars(select(LoginSession).where(LoginSession.user_id == user_id, LoginSession.expires_at > now())
                      .order_by(LoginSession.last_seen_at.desc(), LoginSession.created_at.desc())).all()
    return [{"id": row.id, "user_agent": row.user_agent, "ip": row.ip, "created_at": iso(row.created_at),
             "last_seen_at": iso(row.last_seen_at), "expires_at": iso(row.expires_at),
             "current": row.token_hash == current_hash} for row in rows if row.id]


# --- the second factor ----------------------------------------------------------------------------------------------

def _purpose(user_id: str) -> str:
    return f"totp:{user_id}"


def _decrypt_secret(user: User, ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return secret_box.decrypt_json(_purpose(user.id), ciphertext)["secret"]
    except (secret_box.SecretBoxError, KeyError):
        return None


def two_factor_enabled(user: User) -> bool:
    return bool(user.totp_enabled_at and user.totp_ciphertext)


def start_totp(db, user: User) -> dict:
    """A new secret waiting for its first correct code; replaces an unfinished enrollment."""
    secret = totp.new_secret()
    user.totp_pending_ciphertext = secret_box.encrypt_json(_purpose(user.id), {"secret": secret})
    uri = totp.provisioning_uri(secret, user.email)
    return {"secret": secret, "uri": uri, "qr": totp.qr_data_uri(uri)}


def replace_recovery_codes(db, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.new_recovery_codes()
    moment = now()
    for code in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=totp.recovery_digest(user.id, code), created_at=moment))
    return codes


def enable_totp(db, user: User, code: str) -> list[str] | None:
    """Turn 2FA on when ``code`` matches the pending secret; returns the new recovery codes (shown once)."""
    secret = _decrypt_secret(user, user.totp_pending_ciphertext)
    if not secret:
        return None
    step = totp.verify(secret, code)
    if step is None:
        return None
    user.totp_ciphertext, user.totp_pending_ciphertext = user.totp_pending_ciphertext, None
    user.totp_enabled_at, user.totp_last_step = now(), step
    return replace_recovery_codes(db, user)


def disable_totp(db, user: User) -> None:
    user.totp_ciphertext = user.totp_pending_ciphertext = None
    user.totp_enabled_at = user.totp_last_step = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))


def recovery_remaining(db, user_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(RecoveryCode)
                         .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))) or 0)


def check_second_factor(db, user: User, code: str) -> str | None:
    """``totp`` or ``recovery`` when ``code`` is valid (a recovery code is used up); otherwise None."""
    if not two_factor_enabled(user):
        return None
    if totp.is_totp_code(code):
        # The row lock makes "never the same code twice" hold for concurrent sign-ins too: a second request with
        # the same code waits, then sees the step the first one stored.
        db.refresh(user, ["totp_ciphertext", "totp_last_step"], with_for_update=True)
        secret = _decrypt_secret(user, user.totp_ciphertext)
        step = totp.verify(secret, code, last_step=user.totp_last_step) if secret else None
        if step is None:
            return None
        user.totp_last_step = step
        return "totp"
    row = db.scalar(select(RecoveryCode).where(RecoveryCode.user_id == user.id,
                                               RecoveryCode.code_hash == totp.recovery_digest(user.id, code),
                                               RecoveryCode.used_at.is_(None)).with_for_update())
    if row is None:
        return None
    row.used_at = now()
    return "recovery"


# --- views ------------------------------------------------------------------------------------------------------------

def security_view(db, user: User, current_hash: str | None, *, email_delivery: bool) -> dict:
    return {
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "email_verified_at": iso(user.email_verified_at),
        "email_delivery": email_delivery,
        "password_changed_at": iso(user.password_changed_at),
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
        "two_factor": {"enabled": two_factor_enabled(user), "enabled_at": iso(user.totp_enabled_at),
                       "recovery_codes_remaining": recovery_remaining(db, user.id) if two_factor_enabled(user) else 0,
                       "pending": bool(user.totp_pending_ciphertext) and not two_factor_enabled(user)},
        "is_admin": user.is_admin,
        "sessions": sessions(db, user.id, current_hash),
        "terms": {"version": user.terms_version, "accepted_at": iso(user.terms_accepted_at),
                  "current_version": TERMS_VERSION},
    }


def export(db, user: User) -> dict:
    """The account's metadata as JSON (Settings → Security → Download my data). No media, no secrets."""
    profile = db.get(UserProfile, user.id)
    memberships = db.execute(select(Membership, Workspace).join(Workspace, Workspace.id == Membership.workspace_id)
                             .where(Membership.user_id == user.id)).all()
    owned = [workspace.id for membership, workspace in memberships if membership.role == "owner"]
    orders = db.scalars(select(PaymentOrder).where(PaymentOrder.workspace_id.in_(owned))
                        .order_by(PaymentOrder.created_at.desc())).all() if owned else []
    tickets = db.scalars(select(SupportTicket).where(SupportTicket.created_by_user_id == user.id)
                         .order_by(SupportTicket.created_at.desc())).all()
    events = db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == user.id)
                        .order_by(AuditEvent.created_at.desc()).limit(200)).all()
    notifications = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id))
    return {
        "exported_at": now().isoformat(),
        "account": {"id": user.id, "email": user.email, "created_at": iso(user.created_at),
                    "email_verified_at": iso(user.email_verified_at), "is_system_admin": user.is_admin,
                    "two_factor_enabled": two_factor_enabled(user), "last_login_at": iso(user.last_login_at),
                    "password_changed_at": iso(user.password_changed_at), "language": user.locale,
                    "terms_version": user.terms_version, "terms_accepted_at": iso(user.terms_accepted_at)},
        "profile": {"display_name": profile.display_name if profile else None},
        "workspaces": [{"id": workspace.id, "name": workspace.name, "role": membership.role,
                        "joined_at": iso(membership.created_at)} for membership, workspace in memberships],
        "sessions": sessions(db, user.id, None),
        "payment_orders": [{"workspace_id": order.workspace_id, "reference": str(order.order_code),
                            "plan": order.plan_code, "provider": order.provider, "amount_vnd": order.amount_vnd,
                            "status": order.status, "created_at": iso(order.created_at), "paid_at": iso(order.paid_at)}
                           for order in orders],
        "support_tickets": [{"id": ticket.id, "subject": ticket.subject, "category": ticket.category,
                             "status": ticket.status, "created_at": iso(ticket.created_at)} for ticket in tickets],
        "notifications": int(notifications or 0),
        "security_events": [{"at": iso(event.created_at), "action": event.action, "outcome": event.outcome,
                             "ip": event.ip, "details": json.loads(event.details_json or "{}")} for event in events],
    }
