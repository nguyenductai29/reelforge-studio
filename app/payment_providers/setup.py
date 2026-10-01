"""The system admin's payment gateway view (Phase 18A, managed since Phase 19), never a secret value.

``overview`` describes each provider as ``app.payment_config`` resolves it: where
the configuration comes from (admin, bootstrap, environment or missing), whether
it is enabled and offered, each field as configured/missing/invalid (identifiers
masked), OnePAY's mode and endpoints, why it is unavailable, the URLs to register
with the provider, recent activity and the change history.

``check`` validates the configuration in use. With ``remote``, OnePAY is asked one
QueryDR question about a reference that cannot exist: it reads, never charges and
never creates a checkout. payOS has no read-only call that proves its keys without
a real order, so its remote check is reported as unsupported.

``record_activity`` keeps the time of the last verified webhook, IPN, status query
and check per provider (system setting ``payment_activity``).
"""
from datetime import datetime, timezone
import json
import time

from app import payment_config
from app.payment_providers import onepay

ACTIVITY_KEY = "payment_activity"


def provider_view(db, provider: str, origin: str, activity: dict) -> dict:
    from app.models import User

    resolved = payment_config.get(provider, db)
    usable = payment_config.usable(resolved)
    editor = db.get(User, resolved.updated_by_user_id) if resolved.updated_by_user_id else None
    updated_by = editor.email if editor else None
    view = {"provider": provider, "method": payment_config.METHOD[provider], "enabled": resolved.enabled,
            "configured": usable, "available": resolved.enabled and usable, "source": resolved.source,
            "legacy_source": resolved.legacy_source, "mode": resolved.mode,
            "fields": payment_config.fields_view(resolved), "issues": payment_config.issues(resolved),
            "updated_at": resolved.updated_at.isoformat() if resolved.updated_at else None, "updated_by": updated_by,
            "activity": activity.get(provider, {}), "history": payment_config.history(db, provider)}
    if provider == "payos":
        view["endpoints"] = [{"key": "webhook", "url": f"{origin}/api/webhooks/payos"}]
    else:
        payment_url, query_url = payment_config.onepay_urls(resolved)
        view["urls"] = {"payment_url": payment_url, "query_url": query_url}
        view["query_configured"] = bool(resolved.values.get("query_user") and resolved.values.get("query_password"))
        view["endpoints"] = [{"key": "ipn", "url": f"{origin}/api/webhooks/onepay"},
                             {"key": "return", "url": f"{origin}/api/billing/onepay/return"}]
    return view


def overview(db, origin: str) -> list[dict]:
    origin, recent = origin.rstrip("/"), activity(db)
    return [provider_view(db, provider, origin, recent) for provider in payment_config.PROVIDERS]


def check(provider: str, *, remote: bool = False, client=None, db=None) -> dict:
    """Validate one provider's configuration in use; ``remote`` adds OnePAY's read-only QueryDR round trip."""
    resolved = payment_config.get(provider, db)
    if resolved.error:
        return {"provider": provider, "source": resolved.source, "local": {"status": "error", "code": resolved.error},
                "remote": {"status": "skipped"}}
    if provider == "payos":
        local = {"status": "ok"} if payment_config.usable(resolved) else {"status": "error", "code": "not_configured"}
        return {"provider": provider, "source": resolved.source, "local": local,
                "remote": {"status": "unsupported" if remote else "skipped"}}
    try:
        config = payment_config.onepay_config(resolved)
    except onepay.OnePayError as exc:
        return {"provider": provider, "source": resolved.source, "local": {"status": "error", "code": exc.code},
                "remote": {"status": "skipped"}}
    local = {"status": "ok"} if config.can_query else {"status": "warning", "code": "query_not_configured"}
    result = {"provider": provider, "source": resolved.source, "mode": resolved.mode, "local": local}
    if not remote:
        return {**result, "remote": {"status": "skipped"}}
    if not config.can_query:
        return {**result, "remote": {"status": "skipped", "code": "query_not_configured"}}
    from app.payment_providers import http_client

    try:
        # A reference no checkout ever used: OnePAY can only answer that it does not exist.
        with (client or http_client()) as session:
            onepay.query(config, f"RFCHECK{int(time.time())}", client=session)
    except onepay.OnePayError as exc:
        return {**result, "remote": {"status": "error", "code": exc.code}}
    return {**result, "remote": {"status": "ok"}}


def activity(db) -> dict:
    from app.models import SystemSetting

    row = db.get(SystemSetting, ACTIVITY_KEY)
    try:
        return json.loads(row.value) if row else {}
    except ValueError:
        return {}


def record_activity(provider: str, event: str, session_factory=None) -> None:
    """Note a verified ``webhook``, ``ipn``, ``query`` or ``check`` now; best effort, in its own transaction."""
    from app.models import SystemSetting

    if session_factory is None:
        from app.db import Session as session_factory
    try:
        with session_factory.begin() as db:
            row = db.get(SystemSetting, ACTIVITY_KEY)
            data = activity(db)
            data.setdefault(provider, {})[event] = datetime.now(timezone.utc).isoformat()
            if row is None:
                db.add(SystemSetting(key=ACTIVITY_KEY, value=json.dumps(data)))
            else:
                row.value = json.dumps(data)
    except Exception:  # noqa: BLE001 - a missing timestamp must never fail a payment
        pass
