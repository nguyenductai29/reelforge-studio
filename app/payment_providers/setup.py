"""What a system admin needs to set up each payment provider (Phase 18A), never a secret value.

Credentials stay where they are: payOS in ``instance/bootstrap.json`` (``payos``
object), OnePAY in the runtime environment (``ONEPAY_*``). They are not copied into
the database and cannot be edited from the browser. For each field the admin
sees its name, where it is set and whether it is configured, missing or invalid.
Identifiers (the payOS client ID, the OnePAY merchant ID) are shown masked; keys,
access codes, hash keys and passwords only as configured or missing.

``check`` validates the configuration locally. With ``remote``, OnePAY is asked
one QueryDR question about a reference that cannot exist: it reads, never
charges and never creates a checkout. payOS has no read-only call that proves
its keys without a real order, so its remote check is reported as unsupported.

``record_activity`` keeps the time of the last verified webhook, IPN, status
query and check per provider (system setting ``payment_activity``).
"""
from datetime import datetime, timezone
import json
import os
import re
import time
from urllib.parse import urlsplit

from app.payment_providers import onepay

ACTIVITY_KEY = "payment_activity"
SANDBOX_HOSTS = ("mtf.onepay.vn",)
_HEX = re.compile(r"(?:[0-9A-Fa-f]{2})+\Z")


def _mask(value: str) -> str:
    value = value.strip()
    return value[:2] + "…" if len(value) <= 8 else f"{value[:4]}…{value[-4:]}"


def _field(name: str, value: str | None, *, source: str, show: bool = False, valid=None) -> dict:
    value = (value or "").strip()
    status = "missing" if not value else "invalid" if valid is not None and not valid(value) else "configured"
    return {"name": name, "source": source, "status": status, **({"value": _mask(value)} if show and value else {})}


def onepay_mode(payment_url: str) -> str:
    host = (urlsplit(payment_url).hostname or "").lower()
    if host in SANDBOX_HOSTS:
        return "sandbox"
    return "production" if host == "onepay.vn" or host.endswith(".onepay.vn") else "custom"


def payos_setup(origin: str, activity: dict) -> dict:
    from app.db import config  # read on use: the bootstrap file belongs to the configured database

    data = config.get("payos") or {}
    fields = [_field("payos.client_id", data.get("client_id"), source="bootstrap", show=True),
              _field("payos.api_key", data.get("api_key"), source="bootstrap"),
              _field("payos.checksum_key", data.get("checksum_key"), source="bootstrap")]
    return {"provider": "payos", "method": "vietqr", "configured": all(f["status"] == "configured" for f in fields),
            "fields": fields, "mode": None, "setup_file": "instance/bootstrap.json",
            "endpoints": [{"key": "webhook", "url": f"{origin}/api/webhooks/payos"}],
            "activity": activity.get("payos", {})}


def onepay_setup(origin: str, activity: dict) -> dict:
    env = os.environ.get
    payment_url = env("ONEPAY_PAYMENT_URL", "").strip() or onepay.PRODUCTION_PAYMENT_URL
    query_url = env("ONEPAY_QUERY_URL", "").strip() or onepay.PRODUCTION_QUERY_URL
    https = lambda value: urlsplit(value).scheme == "https" and bool(urlsplit(value).hostname)  # noqa: E731
    fields = [_field("ONEPAY_MERCHANT_ID", env("ONEPAY_MERCHANT_ID"), source="environment", show=True),
              _field("ONEPAY_ACCESS_CODE", env("ONEPAY_ACCESS_CODE"), source="environment"),
              _field("ONEPAY_HASH_KEY", env("ONEPAY_HASH_KEY"), source="environment", valid=_HEX.fullmatch),
              _field("ONEPAY_QUERY_USER", env("ONEPAY_QUERY_USER"), source="environment"),
              _field("ONEPAY_QUERY_PASSWORD", env("ONEPAY_QUERY_PASSWORD"), source="environment"),
              # Endpoints are not secret: shown in full, with the OnePAY default when unset.
              {"name": "ONEPAY_PAYMENT_URL", "source": "environment", "value": payment_url,
               "status": "configured" if https(payment_url) else "invalid", "default": not env("ONEPAY_PAYMENT_URL")},
              {"name": "ONEPAY_QUERY_URL", "source": "environment", "value": query_url,
               "status": "configured" if https(query_url) else "invalid", "default": not env("ONEPAY_QUERY_URL")}]
    return {"provider": "onepay", "method": "card", "configured": onepay.configured(), "fields": fields,
            "mode": onepay_mode(payment_url), "setup_file": None,
            "query_configured": all(f["status"] == "configured" for f in fields[3:5]),
            "endpoints": [{"key": "ipn", "url": f"{origin}/api/webhooks/onepay"},
                          {"key": "return", "url": f"{origin}/api/billing/onepay/return"}],
            "activity": activity.get("onepay", {})}


def overview(origin: str, activity: dict) -> list[dict]:
    origin = origin.rstrip("/")
    return [payos_setup(origin, activity), onepay_setup(origin, activity)]


def check(provider: str, *, remote: bool = False, client=None) -> dict:
    """Validate one provider's configuration; ``remote`` adds OnePAY's read-only QueryDR round trip."""
    from app import billing

    if provider == "payos":
        local = {"status": "ok"} if billing.configured() else {"status": "error", "code": "not_configured"}
        return {"provider": provider, "local": local,
                "remote": {"status": "unsupported" if remote else "skipped"}}
    if provider != "onepay":
        raise ValueError("unknown provider")
    try:
        config = onepay.OnePayConfig.from_environment()
    except onepay.OnePayError as exc:
        return {"provider": provider, "local": {"status": "error", "code": exc.code}, "remote": {"status": "skipped"}}
    local = {"status": "ok"} if config.can_query else {"status": "warning", "code": "query_not_configured"}
    if not remote:
        return {"provider": provider, "local": local, "remote": {"status": "skipped"}}
    if not config.can_query:
        return {"provider": provider, "local": local, "remote": {"status": "skipped", "code": "query_not_configured"}}
    from app.payment_providers import http_client

    try:
        # A reference no checkout ever used: OnePAY can only answer that it does not exist.
        with (client or http_client()) as session:
            onepay.query(config, f"RFCHECK{int(time.time())}", client=session)
    except onepay.OnePayError as exc:
        return {"provider": provider, "local": local, "remote": {"status": "error", "code": exc.code}}
    return {"provider": provider, "local": local, "remote": {"status": "ok"}}


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
