"""Transactional email (Phase 22): one sender, two providers (SMTP or Resend), a durable outbox.

**Configuration** lives in Admin → System settings → Email (``app/system_config.py``,
section ``email``): enabled, provider, sender name and address, reply-to, and the provider's
credentials: SMTP host, port, username, password (secret) and TLS mode, or a Resend API
key (secret). Secrets are encrypted with the master key and never shown again.

**Sending** goes through ``email_outbox``. ``enqueue`` adds a row in the caller's
transaction (a rolled-back registration sends nothing); its template parameters, which
can contain a one-time link, are encrypted and erased once the email is sent or given
up. ``kick()`` wakes a background thread in the API process right after the commit, so a
request never waits on SMTP, and the response time does not depend on whether an email
was sent (forgot-password must not reveal whether an account exists). The scheduler
worker retries due rows on every pass: back-off 1 min, 5 min, 30 min, 2 h, 6 h, then
``failed``. Rows are claimed with ``FOR UPDATE SKIP LOCKED`` on PostgreSQL.

When email is off or incomplete, nothing is queued (``enqueue`` returns ``None``): the
product works without email, and the UI says what needs it.

Provider errors are reduced to a code (``auth_failed``, ``connection_failed``,
``rejected``, ``timeout``, ``provider_error``); a password, key or token never reaches a
log line, an audit row or the outbox.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
import logging
import re
import smtplib
import socket
import ssl
import threading
import uuid

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import email_templates, secret_box, system_config
from app.logs import log_event
from app.models import EmailOutbox

logger = logging.getLogger(__name__)

PROVIDERS = ("smtp", "resend")
SMTP_SECURITY = ("starttls", "ssl", "none")
RESEND_URL = "https://api.resend.com/emails"
PURPOSE = "email-outbox"
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = (60, 300, 1800, 7200, 21600)
STUCK_SECONDS = 600
SEND_TIMEOUT = 20
EMAIL_PATTERN = re.compile(r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+")

# Tests and development hooks: a callable(Message) that replaces the provider, and an httpx transport for Resend.
SEND_OVERRIDE = None
RESEND_TRANSPORT = None
# The API delivers right after a commit in a background thread; tests turn this off and call deliver_pending().
AUTO_DELIVER = True


class EmailError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str
    from_name: str
    from_email: str
    reply_to: str | None = None


def config() -> dict:
    get = system_config.get
    return {
        "enabled": bool(get("email.enabled")),
        "provider": str(get("email.provider") or "smtp"),
        "from_name": str(get("email.from_name") or email_templates.BRAND),
        "from_email": str(get("email.from_email") or "").strip(),
        "reply_to": str(get("email.reply_to") or "").strip() or None,
        "smtp_host": str(get("email.smtp.host") or "").strip(),
        "smtp_port": int(get("email.smtp.port") or 587),
        "smtp_username": str(get("email.smtp.username") or "").strip(),
        "smtp_password": str(get("email.smtp.password") or ""),
        "smtp_security": str(get("email.smtp.security") or "starttls"),
        "resend_api_key": str(get("email.resend.api_key") or ""),
    }


def provider_problem(cfg: dict | None = None) -> str | None:
    """Why the provider cannot send (ignoring the on/off switch), or None."""
    cfg = cfg or config()
    if cfg["provider"] not in PROVIDERS:
        return "provider_unknown"
    if not EMAIL_PATTERN.fullmatch(cfg["from_email"]):
        return "from_missing"
    if cfg["provider"] == "smtp" and not cfg["smtp_host"]:
        return "smtp_host_missing"
    if cfg["provider"] == "resend" and not cfg["resend_api_key"]:
        return "resend_key_missing"
    return None


def problem(cfg: dict | None = None) -> str | None:
    """Why transactional email is not being sent, or None when it is."""
    cfg = cfg or config()
    if not cfg["enabled"]:
        return "disabled"
    return provider_problem(cfg) or (None if secret_box.available() else "key_missing")


def enabled() -> bool:
    return problem() is None


# --- providers -----------------------------------------------------------------------------------------------

def _mime(message: Message) -> EmailMessage:
    mime = EmailMessage()
    mime["Subject"] = message.subject
    mime["From"] = formataddr((message.from_name, message.from_email))
    mime["To"] = message.to
    if message.reply_to:
        mime["Reply-To"] = message.reply_to
    mime["Date"] = formatdate(localtime=False)
    mime["Message-ID"] = make_msgid(domain=message.from_email.rsplit("@", 1)[-1])
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    return mime


def _smtp_send(cfg: dict, message: Message) -> None:
    host, port, security = cfg["smtp_host"], cfg["smtp_port"], cfg["smtp_security"]
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=SEND_TIMEOUT, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=SEND_TIMEOUT)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if cfg["smtp_username"]:
                server.login(cfg["smtp_username"], cfg["smtp_password"])
            server.send_message(_mime(message))
    except smtplib.SMTPAuthenticationError:
        raise EmailError("auth_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise EmailError("rejected") from None
    except (socket.timeout, TimeoutError):
        raise EmailError("timeout") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError):
        raise EmailError("connection_failed") from None


def _resend_send(cfg: dict, message: Message) -> None:
    import httpx

    payload = {"from": formataddr((message.from_name, message.from_email)), "to": [message.to],
               "subject": message.subject, "html": message.html, "text": message.text}
    if message.reply_to:
        payload["reply_to"] = message.reply_to
    try:
        with httpx.Client(timeout=SEND_TIMEOUT, transport=RESEND_TRANSPORT) as client:
            response = client.post(RESEND_URL, json=payload,
                                   headers={"Authorization": f"Bearer {cfg['resend_api_key']}"})
    except httpx.TimeoutException:
        raise EmailError("timeout") from None
    except httpx.HTTPError:
        raise EmailError("connection_failed") from None
    if response.status_code in (401, 403):
        raise EmailError("auth_failed")
    if response.status_code in (400, 404, 409, 422):
        raise EmailError("rejected")
    if response.status_code >= 300:
        raise EmailError("provider_error")


def send(message: Message, cfg: dict | None = None) -> None:
    """Send one message now; raises ``EmailError`` with a code."""
    if SEND_OVERRIDE is not None:
        SEND_OVERRIDE(message)
        return
    cfg = cfg or config()
    trouble = provider_problem(cfg)
    if trouble:
        raise EmailError(trouble)
    (_smtp_send if cfg["provider"] == "smtp" else _resend_send)(cfg, message)


def build(template: str, locale: str | None, params: dict, to: str, cfg: dict | None = None) -> Message:
    cfg = cfg or config()
    subject, text, html_body = email_templates.render(template, locale, params)
    return Message(to=to, subject=subject, text=text, html=html_body, from_name=cfg["from_name"],
                   from_email=cfg["from_email"], reply_to=cfg["reply_to"])


def send_test(to: str, locale: str | None) -> dict:
    """Send the test template right away (Admin → System settings → Email → Send test email)."""
    cfg = config()
    trouble = provider_problem(cfg)
    if trouble:
        return {"ok": False, "error": trouble}
    try:
        send(build("test", locale, {"time": datetime.now(timezone.utc)}, to, cfg), cfg)
    except EmailError as exc:
        log_event(logger, "email_test_failed", level=logging.WARNING, provider=cfg["provider"], error=exc.code)
        return {"ok": False, "error": exc.code}
    log_event(logger, "email_test_sent", provider=cfg["provider"])
    return {"ok": True, "error": None}


# --- the outbox ------------------------------------------------------------------------------------------------

_ready = False


def ready(db) -> bool:
    global _ready
    if not _ready:
        _ready = inspect(db.connection()).has_table("email_outbox")
    return _ready


def _plain(params: dict) -> dict:
    """JSON-ready parameters: times become ISO strings (the templates format them)."""
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in params.items()}


def enqueue(db, *, to: str, template: str, locale: str | None, params: dict, user_id: str | None = None,
            dedupe: str | None = None) -> str | None:
    """Queue one email in the caller's transaction; ``None`` when email is off (nothing is queued)."""
    if template not in email_templates.TEMPLATES:
        raise ValueError(f"unknown email template: {template}")
    if not ready(db):
        return None
    trouble = problem()
    if trouble:
        log_event(logger, "email_skipped", template=template, reason=trouble)
        return None
    now = datetime.now(timezone.utc)
    # An email never breaks the change that caused it (a settled payment, a reset password): trouble here is
    # logged and the email skipped; the caller's transaction goes on.
    try:
        payload = secret_box.encrypt_json(PURPOSE, {"params": _plain(params)})
    except Exception:  # noqa: BLE001 - see above
        log_event(logger, "email_skipped", level=logging.WARNING, template=template, reason="encrypt_failed")
        return None
    row = EmailOutbox(id=str(uuid.uuid4()), user_id=user_id, to_address=to.strip()[:320], template=template,
                      locale=email_templates.locale_of(locale), payload_ciphertext=payload,
                      status="queued", attempts=0, dedupe_key=dedupe[:160] if dedupe else None,
                      created_at=now, next_attempt_at=now)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None  # the same dedupe key was queued before
    except SQLAlchemyError:
        log_event(logger, "email_skipped", level=logging.WARNING, template=template, reason="queue_failed")
        return None
    return row.id


_wake = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def _loop() -> None:
    while True:
        _wake.wait(timeout=60)
        _wake.clear()
        try:
            deliver_pending()
        except Exception:  # noqa: BLE001 - the next kick or the scheduler retries
            logger.warning("email delivery pass failed", exc_info=True)


def kick() -> None:
    """Deliver queued email soon, in the background (call after the transaction that queued it commits)."""
    global _thread
    if not AUTO_DELIVER:
        return
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="email-delivery", daemon=True)
            _thread.start()
    _wake.set()


def _claim(db, now: datetime, limit: int) -> list[tuple]:
    rows = list(db.scalars(select(EmailOutbox)
                           .where(EmailOutbox.status.in_(("queued", "sending")), EmailOutbox.next_attempt_at <= now)
                           .order_by(EmailOutbox.next_attempt_at, EmailOutbox.created_at).limit(limit)
                           .with_for_update(skip_locked=True)))
    claimed = []
    for row in rows:
        row.status, row.attempts = "sending", row.attempts + 1
        row.next_attempt_at = now + timedelta(seconds=STUCK_SECONDS)  # a crashed sender is retried later
        claimed.append((row.id, row.to_address, row.template, row.locale, row.payload_ciphertext, row.attempts))
    return claimed


def _finish(session_factory, row_id: str, *, error: str | None, attempts: int, permanent: bool = False) -> None:
    with session_factory.begin() as db:
        row = db.get(EmailOutbox, row_id)
        if row is None:
            return
        now = datetime.now(timezone.utc)
        if error is None:
            row.status, row.sent_at, row.payload_ciphertext, row.last_error = "sent", now, None, None
            return
        row.last_error = error[:300]
        if permanent or attempts >= MAX_ATTEMPTS:
            row.status, row.payload_ciphertext = "failed", None
        else:
            row.status = "queued"
            row.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1])


def deliver_pending(session_factory=None, *, limit: int = 20, now: datetime | None = None) -> int:
    """Send due emails; returns how many were sent. Safe to run in several processes at once."""
    if session_factory is None:
        from app.db import Session as session_factory
    with session_factory.begin() as db:
        if not ready(db):
            return 0
        claimed = _claim(db, now or datetime.now(timezone.utc), limit)
    if not claimed:
        return 0
    cfg = config()
    sent = 0
    for row_id, to, template, locale, ciphertext, attempts in claimed:
        try:
            params = secret_box.decrypt_json(PURPOSE, ciphertext or "")["params"]
        except (secret_box.SecretBoxError, KeyError):
            _finish(session_factory, row_id, error="cannot_decrypt", attempts=attempts, permanent=True)
            continue
        try:
            send(build(template, locale, params, to, cfg), cfg)
        except EmailError as exc:
            _finish(session_factory, row_id, error=exc.code, attempts=attempts,
                    permanent=exc.code in ("rejected", "from_missing", "provider_unknown"))
            log_event(logger, "email_failed", level=logging.WARNING, template=template, error=exc.code,
                      attempt=attempts)
            continue
        except Exception:  # noqa: BLE001 - one broken email must not stop the others
            logger.warning("email send crashed", exc_info=True)
            _finish(session_factory, row_id, error="internal_error", attempts=attempts)
            continue
        _finish(session_factory, row_id, error=None, attempts=attempts)
        log_event(logger, "email_sent", template=template, attempt=attempts)
        sent += 1
    return sent


def stats(db, *, since: datetime | None = None) -> dict:
    if not ready(db):
        return {"queued": 0, "sent": 0, "failed": 0, "last_failure": None, "last_error": None}
    counts = dict(db.execute(select(EmailOutbox.status, func.count()).group_by(EmailOutbox.status)).all())
    last = db.execute(select(EmailOutbox.created_at, EmailOutbox.last_error).where(EmailOutbox.status == "failed")
                      .order_by(EmailOutbox.created_at.desc()).limit(1)).first()
    recent_failed = 0
    if since is not None:
        recent_failed = db.scalar(select(func.count()).select_from(EmailOutbox)
                                  .where(EmailOutbox.status == "failed", EmailOutbox.created_at >= since)) or 0
    return {"queued": int(counts.get("queued", 0)) + int(counts.get("sending", 0)), "sent": int(counts.get("sent", 0)),
            "failed": int(counts.get("failed", 0)), "recent_failed": int(recent_failed),
            "last_failure": last[0].isoformat() if last else None, "last_error": last[1] if last else None}


def link(path: str) -> str:
    """An absolute link into the frontend (the public origin, or this machine's development override)."""
    origin = system_config.frontend_origin()
    return f"{origin}{path}" if origin else path


def email_owners(db, workspace_id: str, template: str, params: dict, *, dedupe: str) -> int:
    """Queue ``template`` to the active owners of a workspace (billing emails); returns how many were queued."""
    from app.models import Membership, User

    owners = db.scalars(select(User).join(Membership, Membership.user_id == User.id)
                        .where(Membership.workspace_id == workspace_id, Membership.role == "owner",
                               User.is_active.is_(True))).all()
    return sum(1 for owner in owners
               if enqueue(db, to=owner.email, template=template, locale=owner.locale, params=params, user_id=owner.id,
                          dedupe=f"{dedupe}:{owner.id}") is not None)
