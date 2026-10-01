"""One place that decides each payment provider's configuration (Phase 19).

Checkout, webhooks, IPNs, status queries, the admin setup view and readiness all
ask ``get(provider)`` and use what it resolves; no other module reads payment
credentials.

**Credentials**, in order:

1. the admin-managed configuration (``payment_provider_configs``), when a system
   admin has saved one. It is stored as one encrypted JSON object
   (``app/secret_box.py``);
2. the legacy source: payOS in the ``payos`` object of ``instance/bootstrap.json``,
   OnePAY in the ``ONEPAY_*`` environment variables;
3. none.

A saved configuration that cannot be decrypted (the encryption key is missing or
was changed) is reported as an error. It never silently falls back to the legacy
credentials, which may belong to another merchant account or to production.

**The switch.** ``enabled`` is the admin's switch when a row exists; without a row
it is on, so a deployment configured before Phase 19 keeps working. A provider is
*offered* for new checkouts only when it is enabled and its credentials are
complete and valid. Webhooks, IPNs, returns and status queries of existing
orders need only valid credentials: disabling a provider stops new checkouts and
never strands a pending order.

Everything is read per call (one primary-key row), so a saved change applies at
once in every API process, without a restart. Legacy sources are read as before.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import re
from urllib.parse import urlsplit

from sqlalchemy import inspect, select

from app import secret_box
from app.models import PaymentConfigAudit, PaymentProviderConfig, User
from app.payment_providers import onepay

PROVIDERS = ("payos", "onepay")
METHOD = {"payos": "vietqr", "onepay": "card"}
MODES = ("sandbox", "production", "custom")
SANDBOX_PAYMENT_URL = "https://mtf.onepay.vn/paygate/vpcpay.op"
SANDBOX_QUERY_URL = "https://mtf.onepay.vn/msp/api/v1/vpc/invoices/queries"
SANDBOX_HOSTS = ("mtf.onepay.vn",)
_HEX = re.compile(r"(?:[0-9A-Fa-f]{2})+\Z")
MAX_VALUE = 512


@dataclass(frozen=True)
class FieldSpec:
    name: str
    secret: bool  # never returned; the others are identifiers, returned masked
    required: bool
    legacy: str  # where the legacy source keeps it


FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "payos": (FieldSpec("client_id", False, True, "payos.client_id"),
              FieldSpec("api_key", True, True, "payos.api_key"),
              FieldSpec("checksum_key", True, True, "payos.checksum_key")),
    "onepay": (FieldSpec("merchant_id", False, True, "ONEPAY_MERCHANT_ID"),
               FieldSpec("access_code", True, True, "ONEPAY_ACCESS_CODE"),
               FieldSpec("hash_key", True, True, "ONEPAY_HASH_KEY"),
               FieldSpec("query_user", True, False, "ONEPAY_QUERY_USER"),
               FieldSpec("query_password", True, False, "ONEPAY_QUERY_PASSWORD")),
}
# OnePAY endpoints for the custom mode only; not secret.
URL_FIELDS = ("payment_url", "query_url")


class ConfigError(ValueError):
    """A save that cannot be applied: a stable ``code`` and the field it concerns; never a value."""

    def __init__(self, code: str, field_name: str | None = None):
        super().__init__(code)
        self.code = code
        self.field = field_name


@dataclass(frozen=True)
class Resolved:
    provider: str
    source: str  # admin, bootstrap, environment or missing
    enabled: bool
    mode: str | None
    values: dict = field(repr=False)
    error: str | None = None  # key_missing or cannot_decrypt, for a saved configuration
    legacy_source: str | None = None
    updated_at: datetime | None = None
    updated_by_user_id: str | None = None


_tables_ready = False


def ready(db) -> bool:
    """Whether migration 0018 is applied (older schemas simply have no admin-managed configuration)."""
    global _tables_ready
    if not _tables_ready:
        _tables_ready = inspect(db.connection()).has_table("payment_provider_configs")
    return _tables_ready


def _purpose(provider: str) -> str:
    return f"payment-config:{provider}"


def onepay_mode(payment_url: str) -> str:
    host = (urlsplit(payment_url).hostname or "").lower()
    if host in SANDBOX_HOSTS:
        return "sandbox"
    return "production" if host == "onepay.vn" or host.endswith(".onepay.vn") else "custom"


def legacy(provider: str) -> tuple[str | None, dict]:
    """The pre-Phase 19 source and its values: the bootstrap file for payOS, the environment for OnePAY."""
    if provider == "payos":
        from app.db import config  # read on use: the bootstrap file belongs to the configured database

        data = config.get("payos") or {}
        values = {key: str(data.get(key) or "").strip() for key in ("client_id", "api_key", "checksum_key")}
        return ("bootstrap" if any(values.values()) else None), values
    env = os.environ.get
    values = {spec.name: env(spec.legacy, "").strip() for spec in FIELDS["onepay"]}
    present = any(values.values())
    values["payment_url"] = env("ONEPAY_PAYMENT_URL", "").strip()
    values["query_url"] = env("ONEPAY_QUERY_URL", "").strip()
    return ("environment" if present else None), values


def _row(db, provider: str, lock: bool = False) -> PaymentProviderConfig | None:
    if not ready(db):
        return None
    if lock:
        return db.scalar(select(PaymentProviderConfig).where(PaymentProviderConfig.provider == provider)
                         .with_for_update())
    return db.get(PaymentProviderConfig, provider)


def get(provider: str, db=None) -> Resolved:
    """The configuration ``provider`` uses right now (see the module docstring)."""
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    if db is None:
        from app.db import Session

        with Session() as session:
            return get(provider, session)
    row = _row(db, provider)
    legacy_source, legacy_values = legacy(provider)
    enabled = row.enabled if row is not None else True
    meta = {"legacy_source": legacy_source, "updated_at": row.updated_at if row is not None else None,
            "updated_by_user_id": row.updated_by_user_id if row is not None else None}
    if row is not None and row.config_ciphertext:
        try:
            values, error = secret_box.decrypt_json(_purpose(provider), row.config_ciphertext), None
        except secret_box.SecretBoxError as exc:
            values, error = {}, exc.code
        return Resolved(provider, "admin", enabled, row.mode, values, error, **meta)
    if legacy_source:
        mode = onepay_mode(legacy_values["payment_url"] or onepay.PRODUCTION_PAYMENT_URL) if provider == "onepay" \
            else None
        return Resolved(provider, legacy_source, enabled, mode, legacy_values, **meta)
    return Resolved(provider, "missing", enabled, None, {}, **meta)


def _value(resolved: Resolved, name: str) -> str:
    return str(resolved.values.get(name) or "").strip()


def payos_credentials(resolved: Resolved) -> dict | None:
    """``client_id``, ``api_key`` and ``checksum_key`` when all are set, else None."""
    data = {spec.name: _value(resolved, spec.name) for spec in FIELDS["payos"]}
    return data if not resolved.error and all(data.values()) else None


def onepay_urls(resolved: Resolved) -> tuple[str, str]:
    """(payment URL, QueryDR URL): from the mode for an admin configuration, else the legacy variables."""
    if resolved.source == "admin":
        if resolved.mode == "sandbox":
            return SANDBOX_PAYMENT_URL, SANDBOX_QUERY_URL
        if resolved.mode == "custom":
            return _value(resolved, "payment_url"), _value(resolved, "query_url")
        return onepay.PRODUCTION_PAYMENT_URL, onepay.PRODUCTION_QUERY_URL
    return (_value(resolved, "payment_url") or onepay.PRODUCTION_PAYMENT_URL,
            _value(resolved, "query_url") or onepay.PRODUCTION_QUERY_URL)


def onepay_config(resolved: Resolved) -> onepay.OnePayConfig:
    """The OnePAY configuration to sign and verify with; ``OnePayError('not_configured')`` when incomplete."""
    if resolved.error:
        raise onepay.OnePayError("not_configured", "OnePAY configuration cannot be read")
    payment_url, query_url = onepay_urls(resolved)
    return onepay.OnePayConfig(merchant=_value(resolved, "merchant_id"), access_code=_value(resolved, "access_code"),
                               hash_key=_value(resolved, "hash_key"), payment_url=payment_url, query_url=query_url,
                               query_user=_value(resolved, "query_user"),
                               query_password=_value(resolved, "query_password"))


def usable(resolved: Resolved) -> bool:
    """Whether the credentials are complete and valid (all that callbacks and status queries need)."""
    if resolved.provider == "payos":
        return payos_credentials(resolved) is not None
    try:
        onepay_config(resolved)
    except onepay.OnePayError:
        return False
    return True


def offered(resolved: Resolved) -> bool:
    """Whether buyers may choose this provider for a new checkout."""
    return resolved.enabled and usable(resolved)


def _https(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.hostname)


def issues(resolved: Resolved) -> list[dict]:
    """Why a provider is unavailable or needs attention, as stable codes the admin UI explains."""
    found = []
    if resolved.error:
        return [{"level": "error", "code": resolved.error}]
    if resolved.source == "missing":
        found.append({"level": "error", "code": "not_configured"})
    for spec in FIELDS[resolved.provider]:
        if spec.required and not _value(resolved, spec.name) and resolved.source != "missing":
            found.append({"level": "error", "code": "missing", "field": spec.name})
    if resolved.provider == "onepay" and resolved.source != "missing":
        hash_key = _value(resolved, "hash_key")
        if hash_key and not _HEX.fullmatch(hash_key):
            found.append({"level": "error", "code": "invalid_hash_key", "field": "hash_key"})
        for name, url in zip(URL_FIELDS, onepay_urls(resolved)):
            if not _https(url):
                found.append({"level": "error", "code": "invalid_url", "field": name})
        user, password = _value(resolved, "query_user"), _value(resolved, "query_password")
        if bool(user) != bool(password):
            found.append({"level": "warning", "code": "query_incomplete"})
        elif not user:
            found.append({"level": "warning", "code": "query_missing"})
    if not resolved.enabled:
        found.append({"level": "warning", "code": "disabled"})
    return found


def mask(value: str) -> str:
    value = value.strip()
    return value[:2] + "…" if len(value) <= 8 else f"{value[:4]}…{value[-4:]}"


def fields_view(resolved: Resolved) -> dict:
    """Each field's state; identifiers masked, secrets only as configured or missing."""
    view = {}
    for spec in FIELDS[resolved.provider]:
        value = _value(resolved, spec.name)
        status = "configured" if value else "missing"
        if spec.name == "hash_key" and value and not _HEX.fullmatch(value):
            status = "invalid"
        entry = {"configured": bool(value), "status": status, "secret": spec.secret, "required": spec.required,
                 "legacy": spec.legacy}
        if value and not spec.secret:
            entry["masked"] = mask(value)
        view[spec.name] = entry
    return view


def history(db, provider: str, limit: int = 8) -> list[dict]:
    if not ready(db):
        return []
    rows = db.execute(select(PaymentConfigAudit, User.email).outerjoin(User, User.id == PaymentConfigAudit.admin_user_id)
                      .where(PaymentConfigAudit.provider == provider)
                      .order_by(PaymentConfigAudit.created_at.desc(), PaymentConfigAudit.id.desc()).limit(limit)).all()
    out = []
    for audit, email in rows:
        try:
            meta = json.loads(audit.metadata_json or "{}")
        except ValueError:
            meta = {}
        out.append({"action": audit.action, "at": audit.created_at.isoformat(), "by": email, "metadata": meta})
    return out


def audit(db, provider: str, action: str, admin_id: str | None, **metadata) -> None:
    """Record a change or a test. ``metadata`` holds field names and statuses, never a value."""
    if not ready(db):
        return
    db.add(PaymentConfigAudit(provider=provider, action=action, admin_user_id=admin_id,
                              created_at=datetime.now(timezone.utc),
                              metadata_json=json.dumps(metadata, sort_keys=True)))


def _live_production(provider: str, enabled: bool, mode: str | None) -> bool:
    return provider == "onepay" and enabled and mode == "production"


def save(db, provider: str, admin_id: str, *, enabled: bool, mode: str | None, updates: dict[str, tuple[str, str]],
         urls: dict[str, str] | None = None, confirm_production: bool = False) -> list[str]:
    """Apply a system admin's change; returns the audit actions recorded.

    ``updates`` maps a credential field to ``("keep", "")``, ``("replace", value)`` or
    ``("clear", "")``. Keeping a field keeps the saved admin value; it never imports the
    legacy one. Any credential change, a mode change or new custom URLs make the saved
    configuration complete and valid, or nothing is written (``ConfigError``).
    Without them, only the switch changes.
    """
    if provider not in PROVIDERS:
        raise ConfigError("unknown_provider")
    names = {spec.name for spec in FIELDS[provider]}
    for name, (action, value) in updates.items():
        if name not in names or action not in ("keep", "replace", "clear"):
            raise ConfigError("invalid_request", name)
        if action == "replace" and (not value.strip() or len(value) > MAX_VALUE):
            raise ConfigError("invalid_value", name)
    if provider == "onepay":
        if mode not in MODES:
            raise ConfigError("invalid_mode", "mode")
    else:
        mode, urls = None, None
    row = _row(db, provider, lock=True)
    current = get(provider, db)
    urls = {name: (urls or {}).get(name, "").strip() for name in URL_FIELDS} if mode == "custom" else {}
    saved_urls = {name: _value(current, name) for name in URL_FIELDS} if current.source == "admin" else {}
    edits = any(action != "keep" for action, _ in updates.values())
    if current.source == "admin":
        credential_change = edits or (provider == "onepay" and (
            mode != current.mode or (mode == "custom" and urls != saved_urls)))
    else:
        # Credentials still in the bootstrap file or environment: keeping every field changes only the switch;
        # anything else starts an admin-managed configuration, which must then be complete.
        credential_change = edits or (provider == "onepay" and mode != current.mode)
    now = datetime.now(timezone.utc)
    actions = []
    if credential_change:
        values = dict(current.values) if current.source == "admin" and not current.error else {}
        for name in URL_FIELDS:
            values.pop(name, None)
        for name, (action, value) in updates.items():
            if action == "replace":
                values[name] = value.strip()
            elif action == "clear":
                values.pop(name, None)
        values.update({name: value for name, value in urls.items() if value})
        candidate = Resolved(provider, "admin", enabled, mode, values)
        for found in issues(candidate):
            if found["level"] == "error":
                raise ConfigError(found["code"], found.get("field"))
        if provider == "onepay" and bool(values.get("query_user")) != bool(values.get("query_password")):
            raise ConfigError("query_incomplete", "query_password" if values.get("query_user") else "query_user")
        if not usable(candidate):
            raise ConfigError("invalid_configuration")
        if (_live_production(provider, enabled, mode) and not confirm_production
                and not _live_production(provider, current.enabled, current.mode)):
            raise ConfigError("confirm_production", "mode")
        try:
            ciphertext = secret_box.encrypt_json(_purpose(provider), values)
        except secret_box.SecretBoxError as exc:
            raise ConfigError(exc.code) from exc
        created = row is None or not row.config_ciphertext
        changed = sorted({name for name, (action, _) in updates.items() if action != "keep"}
                         | ({"mode"} if mode != current.mode else set())
                         | {name for name in URL_FIELDS if urls.get(name, "") != saved_urls.get(name, "")})
        if row is None:
            row = PaymentProviderConfig(provider=provider, created_at=now)
            db.add(row)
        was_enabled = current.enabled
        row.config_ciphertext, row.mode, row.enabled = ciphertext, mode, enabled
        row.updated_at, row.updated_by_user_id = now, admin_id
        audit(db, provider, "created" if created else "updated", admin_id, changed=changed, mode=mode)
        actions.append("created" if created else "updated")
        if was_enabled != enabled:
            audit(db, provider, "enabled" if enabled else "disabled", admin_id)
            actions.append("enabled" if enabled else "disabled")
        return actions
    if enabled == current.enabled:
        return actions
    return set_enabled(db, provider, admin_id, enabled, confirm_production=confirm_production)


def set_enabled(db, provider: str, admin_id: str, enabled: bool, *, confirm_production: bool = False) -> list[str]:
    """Turn new checkouts on or off. Turning on needs usable credentials; the credentials never change here."""
    if provider not in PROVIDERS:
        raise ConfigError("unknown_provider")
    row = _row(db, provider, lock=True)
    current = get(provider, db)
    if current.enabled == enabled:
        return []
    if enabled and not usable(current):
        raise ConfigError(current.error or "not_configured")
    if _live_production(provider, enabled, current.mode) and not confirm_production:
        raise ConfigError("confirm_production", "mode")
    now = datetime.now(timezone.utc)
    if row is None:
        row = PaymentProviderConfig(provider=provider, enabled=enabled, mode=None, config_ciphertext=None,
                                    created_at=now)
        db.add(row)
    row.enabled, row.updated_at, row.updated_by_user_id = enabled, now, admin_id
    action = "enabled" if enabled else "disabled"
    audit(db, provider, action, admin_id)
    return [action]


def any_offered(db=None) -> bool:
    return any(offered(get(provider, db)) for provider in PROVIDERS)
