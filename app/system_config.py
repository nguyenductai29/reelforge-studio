"""Central system configuration (Phase 20): one registry, one table, every process.

A system admin edits these settings in Admin → System settings; the API and every
worker read them from PostgreSQL. A change applies within ``CACHE_SECONDS`` in
other processes and at once in the process that saved it. Nothing needs SSH,
an environment file or a restart.

Each setting is plain (text, number, switch) or secret. Secrets are encrypted at
rest with the master key (``app/secret_box.py``, one HKDF-derived key per
setting), written only, and never returned, logged or echoed in an error.

**Resolution**, per setting: the admin's stored value → the legacy environment
variable named in the registry (so installations configured with
``.env.runtime`` keep working) → the default. A stored secret that cannot be
decrypted (the master key is missing or changed) resolves to empty and is
reported as an error; it never silently falls back to the environment.

**Call sites** read ``env("GEMINI_API_KEY")`` instead of
``os.environ.get("GEMINI_API_KEY")`` and keep their own parsing and range checks;
new code reads typed values with ``get(key)``. An AI provider the admin disabled
resolves to an empty key.

Reading the database is opt-in: ``activate()`` runs in ``start_process``, which the
API calls in its lifespan and every worker in ``main``. Library code and unit tests that never call it read
the environment exactly as before, and never open a database connection.

Payment gateways keep their own table (``payment_provider_configs``, Phase 19)
with the same master key and the same write-only semantics; the manual VietQR
bank details and the VietQR mode live here, in the ``payments`` section.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import threading
import time
from typing import Any

from sqlalchemy import inspect, select

from app import secret_box
from app.models import SystemConfig, SystemConfigAudit, User

CACHE_SECONDS = 15
SECRET_MAX = 4096


@dataclass(frozen=True)
class Setting:
    key: str
    section: str  # ai, social, storage, runtime, credits, notifications, payments, email, security, backups
    group: str  # the card it belongs to in the admin UI (a provider, a channel, a topic)
    kind: str  # secret, str, int, float, bool
    env: str | None = None  # the legacy environment variable it replaces
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    max_length: int = 500


AI_PROVIDERS = ("openai", "anthropic", "gemini", "runway", "fal", "runware", "replicate")
SETTINGS: tuple[Setting, ...] = (
    # AI providers: a switch and a key each; Runway also needs the hosts its results come from.
    *(Setting(f"ai.{name}.enabled", "ai", name, "bool", default=True) for name in AI_PROVIDERS),
    Setting("ai.openai.api_key", "ai", "openai", "secret", "OPENAI_API_KEY"),
    Setting("ai.anthropic.api_key", "ai", "anthropic", "secret", "ANTHROPIC_API_KEY"),
    Setting("ai.gemini.api_key", "ai", "gemini", "secret", "GEMINI_API_KEY"),
    Setting("ai.runway.api_secret", "ai", "runway", "secret", "RUNWAYML_API_SECRET"),
    Setting("ai.runway.output_hosts", "ai", "runway", "str", "RUNWAY_OUTPUT_HOSTS", "",
            pattern=r"[A-Za-z0-9.,\-\s]*"),
    Setting("ai.fal.api_key", "ai", "fal", "secret", "FAL_KEY"),
    Setting("ai.runware.api_key", "ai", "runware", "secret", "RUNWARE_API_KEY"),
    Setting("ai.replicate.api_token", "ai", "replicate", "secret", "REPLICATE_API_TOKEN"),
    # OAuth apps; redirect URLs are derived from frontend_origin unless overridden.
    Setting("social.youtube.client_id", "social", "youtube", "str", "GOOGLE_OAUTH_CLIENT_ID", "", max_length=300),
    Setting("social.youtube.client_secret", "social", "youtube", "secret", "GOOGLE_OAUTH_CLIENT_SECRET"),
    Setting("social.youtube.redirect_uri", "social", "youtube", "str", "GOOGLE_OAUTH_REDIRECT_URI", ""),
    Setting("social.tiktok.client_key", "social", "tiktok", "str", "TIKTOK_CLIENT_KEY", "", max_length=300),
    Setting("social.tiktok.client_secret", "social", "tiktok", "secret", "TIKTOK_CLIENT_SECRET"),
    Setting("social.tiktok.redirect_uri", "social", "tiktok", "str", "TIKTOK_REDIRECT_URI", ""),
    Setting("social.tiktok.approved_scopes", "social", "tiktok", "str", "TIKTOK_APPROVED_SCOPES",
            "user.info.basic,video.upload", pattern=r"[a-z0-9_.,\s]*"),
    Setting("social.facebook.app_id", "social", "facebook", "str", "FACEBOOK_APP_ID", "", pattern=r"[0-9]*"),
    Setting("social.facebook.app_secret", "social", "facebook", "secret", "FACEBOOK_APP_SECRET"),
    Setting("social.facebook.redirect_uri", "social", "facebook", "str", "FACEBOOK_REDIRECT_URI", ""),
    # Storage.
    Setting("storage.root", "storage", "root", "str", "REELFORGE_STORAGE_ROOT", "", max_length=1000),
    Setting("storage.quota_ceiling_bytes", "storage", "quota", "int", "WORKSPACE_MEDIA_QUOTA_BYTES", None,
            1, 1 << 50),
    Setting("storage.retention_intermediate_days", "storage", "retention", "int",
            "REELFORGE_RETENTION_INTERMEDIATE_DAYS", 30, 0, 3650),
    Setting("storage.retention_temp_days", "storage", "retention", "int", "REELFORGE_RETENTION_TEMP_DAYS", 3, 1, 3650),
    Setting("storage.retention_partial_days", "storage", "retention", "int", "REELFORGE_RETENTION_PARTIAL_DAYS", 1,
            1, 3650),
    Setting("storage.retention_orphan_days", "storage", "retention", "int", "REELFORGE_RETENTION_ORPHAN_DAYS", 3,
            1, 3650),
    # Rendering and job limits.
    Setting("runtime.ffmpeg_path", "runtime", "ffmpeg", "str", "RENDER_FFMPEG_PATH", "", max_length=1000),
    Setting("runtime.ffprobe_path", "runtime", "ffmpeg", "str", "RENDER_FFPROBE_PATH", "", max_length=1000),
    Setting("runtime.subtitle_font", "runtime", "ffmpeg", "str", "RENDER_SUBTITLE_FONT", "Noto Sans",
            pattern=r"[A-Za-z0-9 _-]{0,80}"),
    Setting("runtime.render_timeout_seconds", "runtime", "ffmpeg", "int", "RENDER_TIMEOUT_SECONDS", 1800, 60, 21600),
    Setting("runtime.still_seconds", "runtime", "ffmpeg", "float", "RENDER_STILL_SECONDS", 5, 1, 60),
    Setting("runtime.video_job_max_age_seconds", "runtime", "jobs", "int", "VIDEO_JOB_MAX_AGE_SECONDS", 21600,
            60, 86400),
    Setting("runtime.image_job_max_age_seconds", "runtime", "jobs", "int", "IMAGE_JOB_MAX_AGE_SECONDS", 3600,
            60, 86400),
    Setting("runtime.voice_job_max_age_seconds", "runtime", "jobs", "int", "VOICE_JOB_MAX_AGE_SECONDS", 1800,
            60, 86400),
    Setting("runtime.transcription_max_seconds", "runtime", "jobs", "int", "TRANSCRIPTION_MAX_SECONDS", 10800,
            60, 43200),
    # Internal ReelForge credits per operation (not provider money).
    Setting("credits.video_per_clip", "credits", "credits", "int", "VIDEO_CREDITS_PER_CLIP", 10, 1, 100000),
    Setting("credits.text_per_generation", "credits", "credits", "int", "TEXT_CREDITS_PER_GENERATION", 1, 1, 100000),
    Setting("credits.image_per_generation", "credits", "credits", "int", "IMAGE_CREDITS_PER_GENERATION", 2, 1,
            100000),
    Setting("credits.voice_per_generation", "credits", "credits", "int", "VOICE_CREDITS_PER_GENERATION", 1, 1,
            100000),
    Setting("credits.render_per_job", "credits", "credits", "int", "RENDER_CREDITS_PER_JOB", 0, 0, 100000),
    Setting("credits.transcription_per_job", "credits", "credits", "int", "TRANSCRIPTION_CREDITS_PER_JOB", 2, 1,
            100000),
    # One batch of movie frames described by a vision-capable text model (app/workflow/nodes/movie.py).
    Setting("credits.vision_per_batch", "credits", "credits", "int", "VISION_CREDITS_PER_BATCH", 1, 1, 100000),
    # Notifications.
    Setting("notifications.sse_poll_seconds", "notifications", "stream", "float", "REELFORGE_SSE_POLL_SECONDS", 3,
            0.1, 30),
    Setting("notifications.sse_max_seconds", "notifications", "stream", "float", "REELFORGE_SSE_MAX_SECONDS", 300,
            1, 3600),
    Setting("notifications.credits_low_threshold", "notifications", "credits", "int", "CREDITS_LOW_THRESHOLD", 20,
            0, 1000000),
    # Payments (edited in Admin → Payments): the VietQR mode and the manual bank QR details (not secret:
    # buyers see them on the QR).
    Setting("payments.vietqr_mode", "payments", "vietqr", "str", None, "payos", pattern=r"payos|manual"),
    Setting("payments.bank_qr.enabled", "payments", "bank_qr", "bool", None, False),
    Setting("payments.bank_qr.bank_bin", "payments", "bank_qr", "str", None, "", pattern=r"[0-9]{6}"),
    Setting("payments.bank_qr.bank_name", "payments", "bank_qr", "str", None, "", max_length=80),
    Setting("payments.bank_qr.account_number", "payments", "bank_qr", "str", None, "", pattern=r"[0-9A-Za-z]{4,19}"),
    Setting("payments.bank_qr.account_name", "payments", "bank_qr", "str", None, "", pattern=r"[A-Za-z0-9 .]{2,50}"),
    Setting("payments.bank_qr.transfer_prefix", "payments", "bank_qr", "str", None, "RF", pattern=r"[A-Z0-9]{1,8}"),
    Setting("payments.bank_qr.note", "payments", "bank_qr", "str", None, "", max_length=300),
    Setting("payments.bank_qr.sla_message", "payments", "bank_qr", "str", None, "", max_length=300),
    # Transactional email (Phase 22, app/mailer.py): off until an admin configures and enables it.
    Setting("email.enabled", "email", "sender", "bool", None, False),
    Setting("email.provider", "email", "sender", "str", None, "smtp", pattern=r"smtp|resend"),
    Setting("email.from_name", "email", "sender", "str", None, "ReelForge Studio", max_length=100),
    Setting("email.from_email", "email", "sender", "str", None, "", pattern=r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+",
            max_length=254),
    Setting("email.reply_to", "email", "sender", "str", None, "", pattern=r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+",
            max_length=254),
    Setting("email.smtp.host", "email", "smtp", "str", None, "", pattern=r"[A-Za-z0-9.\-]+", max_length=253),
    Setting("email.smtp.port", "email", "smtp", "int", None, 587, 1, 65535),
    Setting("email.smtp.security", "email", "smtp", "str", None, "starttls", pattern=r"starttls|ssl|none"),
    Setting("email.smtp.username", "email", "smtp", "str", None, "", max_length=254),
    Setting("email.smtp.password", "email", "smtp", "secret"),
    Setting("email.resend.api_key", "email", "resend", "secret"),
    # Client addresses behind the proxy (Phase 24, app/client_ip.py).
    Setting("security.trusted_proxies", "security", "proxy", "str", None, "127.0.0.0/8,::1/128",
            pattern=r"[0-9A-Fa-f.:/,\s]*", max_length=500),
    Setting("security.client_ip_header", "security", "proxy", "str", None, "CF-Connecting-IP",
            pattern=r"[A-Za-z0-9-]{0,64}"),
    # Database backups (Phase 25, app/backup.py).
    Setting("backups.directory", "backups", "backups", "str", None, "/srv/data/backups/reelforge", max_length=1000),
    Setting("backups.keep_daily", "backups", "retention", "int", None, 14, 1, 365),
    Setting("backups.keep_weekly", "backups", "retention", "int", None, 8, 0, 260),
    Setting("backups.keep_monthly", "backups", "retention", "int", None, 6, 0, 120),
    Setting("backups.max_age_hours", "backups", "backups", "int", None, 26, 1, 720),
    # Movie sources (app/movie_sources.py): temporary source movies, kept in the operator's Google Drive while a
    # workflow needs them, then deleted. Off until an admin turns it on with a working Drive.
    Setting("movie_sources.enabled", "movie_sources", "general", "bool", None, False),
    Setting("movie_sources.retention_days", "movie_sources", "retention", "int", None, 7, 1, 90),
    Setting("movie_sources.max_retention_days", "movie_sources", "retention", "int", None, 30, 1, 365),
    Setting("movie_sources.delete_after_success", "movie_sources", "retention", "bool", None, True),
    Setting("movie_sources.success_grace_hours", "movie_sources", "retention", "int", None, 24, 0, 720),
    Setting("movie_sources.max_source_bytes", "movie_sources", "limits", "int", None, 20 * 1024 ** 3,
            1024 ** 2, 1 << 41),
    Setting("movie_sources.max_duration_seconds", "movie_sources", "limits", "int", None, 4 * 3600, 60, 43200),
    Setting("movie_sources.local_import_root", "movie_sources", "paths", "str", None, "/srv/data/import/reelforge",
            max_length=1000),
    Setting("movie_sources.scratch_root", "movie_sources", "paths", "str", None, "", max_length=1000),
    Setting("movie_sources.delete_local_temp", "movie_sources", "paths", "bool", None, True),
    Setting("movie_sources.delete_scratch", "movie_sources", "paths", "bool", None, True),
    Setting("movie_sources.frame_interval_seconds", "movie_sources", "analysis", "int", None, 10, 2, 120),
    Setting("movie_sources.max_frames", "movie_sources", "analysis", "int", None, 300, 10, 500),
    Setting("movie_sources.drive.enabled", "movie_sources", "drive", "bool", None, False),
    Setting("movie_sources.drive.auth_mode", "movie_sources", "drive", "str", None, "oauth",
            pattern=r"oauth|service_account"),
    Setting("movie_sources.drive.root_folder_id", "movie_sources", "drive", "str", None, "",
            pattern=r"[A-Za-z0-9_-]{0,200}"),
    Setting("movie_sources.drive.client_id", "movie_sources", "drive", "str", None, "", max_length=300,
            pattern=r"[A-Za-z0-9._-]*"),
    Setting("movie_sources.drive.client_secret", "movie_sources", "drive", "secret"),
    Setting("movie_sources.drive.refresh_token", "movie_sources", "drive", "secret"),
    Setting("movie_sources.drive.service_account_json", "movie_sources", "drive", "secret"),
    Setting("movie_sources.drive.delete_mode", "movie_sources", "drive", "str", None, "trash",
            pattern=r"trash|delete"),
    Setting("movie_sources.drive.warning_bytes", "movie_sources", "drive", "int", None, 500 * 1024 ** 3,
            1024 ** 3, 1 << 50),
)
BY_KEY = {setting.key: setting for setting in SETTINGS}
BY_ENV = {setting.env: setting for setting in SETTINGS if setting.env}

# Every other environment variable the backend reads, and why it is still an environment variable
# (docs/SYSTEM_CONFIGURATION.md). Categories: bootstrap (needed before the database can be opened or
# decrypted; allowed to stay outside it), legacy (a fallback for installations configured before the
# admin UI), experimental (off unless an operator runs it) and dev (tests and command-line tools).
# Every variable in BY_ENV is "legacy" too: a fallback for its admin-managed setting.
# tests/test_phase21.py fails if the code reads a variable that is in neither table.
_ONEPAY = ("ONEPAY_MERCHANT_ID", "ONEPAY_ACCESS_CODE", "ONEPAY_HASH_KEY", "ONEPAY_QUERY_USER", "ONEPAY_QUERY_PASSWORD",
           "ONEPAY_PAYMENT_URL", "ONEPAY_QUERY_URL")
_SMOKE = tuple(f"REELFORGE_SMOKE_{task}_{part}" for task in ("TEXT", "VIDEO", "IMAGE", "VOICE")
               for part in ("PROVIDER", "MODEL"))
ENVIRONMENT: dict[str, tuple[str, str]] = {
    "REELFORGE_DATABASE_URL": ("bootstrap", "The database URL when instance/bootstrap.json has none"),
    "REELFORGE_MASTER_KEY_FILE": ("bootstrap", "Where the master key file is, when not /etc/reelforge/master.key"),
    "REELFORGE_LOG_FORMAT": ("bootstrap", "Log format, read once when a process starts, before the database"),
    "REELFORGE_LOG_LEVEL": ("bootstrap", "Log level, read once when a process starts, before the database"),
    "REELFORGE_ENV_FILE": ("legacy", "Names a legacy runtime file to load"),
    "REELFORGE_TOKEN_ENCRYPTION_KEY": ("legacy", "The master key before it moved into a file"),
    **{name: ("legacy", "OnePAY before Admin → Payments → Payment gateways (Phase 19)") for name in _ONEPAY},
    "DOLA_API_KEY": ("experimental", "The experimental Dola video gateway"),
    "DOLA_EXPERIMENTAL_ENABLED": ("experimental", "Turns the experimental Dola gateway on"),
    "DOLA_BASE_URL": ("experimental", "The Dola gateway's address"),
    "DOLA_MEDIA_BASE_URL": ("experimental", "The Dola gateway's media address"),
    "DOLA_MAX_JOB_AGE_SECONDS": ("experimental", "How long a Dola job may wait"),
    **{name: ("dev", "Live smoke-test choice (python -m app.smoke_test / app.provider_check)") for name in _SMOKE},
    "REELFORGE_LIVE_TESTS": ("dev", "Allows the paid live smoke tests in this shell"),
    "REELFORGE_TEST_DATABASE_URL": ("dev", "Runs the migration tests on an isolated PostgreSQL database"),
    "REELFORGE_GOOGLE_API_BASE": ("dev", "Points the Google Drive client at a local test server (loopback only)"),
}
SECTIONS = ("ai", "social", "storage", "runtime", "credits", "notifications", "payments", "email", "security",
            "backups", "movie_sources")
# Paths the frontend serves each OAuth callback on.
REDIRECT_PATHS = {"youtube": "/youtube/callback", "tiktok": "/channels/callback/tiktok",
                  "facebook": "/channels/callback/facebook"}


class ConfigError(ValueError):
    """A save that cannot be applied: a stable ``code`` and the setting it concerns; never a value."""

    def __init__(self, code: str, key: str | None = None):
        super().__init__(code)
        self.code = code
        self.key = key


_active = False
_lock = threading.Lock()
_cache: dict = {"at": None, "rows": {}, "plain": {}}
_tables_ready = False


def activate() -> None:
    """Read settings from the database in this process (the API and every worker)."""
    global _active
    _active = True
    invalidate()


def active() -> bool:
    return _active


def invalidate() -> None:
    with _lock:
        _cache.update(at=None, rows={}, plain={})


def ready(db) -> bool:
    """Whether migration 0019 is applied; older schemas simply have no stored settings."""
    global _tables_ready
    if not _tables_ready:
        _tables_ready = inspect(db.connection()).has_table("system_config")
    return _tables_ready


def _rows() -> dict[str, tuple[str | None, str | None]]:
    if not _active:
        return {}
    with _lock:
        if _cache["at"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["rows"]
    try:
        from app.db import Session

        with Session() as db:
            rows = {row.key: (row.value, row.ciphertext) for row in db.scalars(select(SystemConfig))} \
                if ready(db) else {}
    except Exception:  # noqa: BLE001 - a database hiccup keeps the last known settings
        with _lock:
            return _cache["rows"]
    with _lock:
        _cache.update(at=time.monotonic(), rows=rows, plain={})
    return rows


def _purpose(key: str) -> str:
    return f"system-config:{key}"


def stored(key: str) -> tuple[bool, Any, str | None]:
    """(present, value, error) of the admin's stored value; ``error`` is key_missing or cannot_decrypt."""
    setting = BY_KEY[key]
    row = _rows().get(key)
    if row is None:
        return False, None, None
    with _lock:
        if key in _cache["plain"]:
            return True, _cache["plain"][key], None
    value_json, ciphertext = row
    if setting.kind == "secret":
        try:
            value = secret_box.decrypt_json(_purpose(key), ciphertext or "")["value"]
        except (secret_box.SecretBoxError, KeyError) as exc:
            return True, None, getattr(exc, "code", "cannot_decrypt")
    else:
        try:
            value = json.loads(value_json) if value_json is not None else None
        except ValueError:
            return True, None, "invalid"
    with _lock:
        _cache["plain"][key] = value
    return True, value, None


def _enabled_provider(group: str) -> bool:
    present, value, error = stored(f"ai.{group}.enabled")
    return bool(value) if present and not error else True


def _as_env(setting: Setting, value: Any) -> str:
    if value is None:
        return ""
    if setting.kind == "bool":
        return "1" if value else ""
    if setting.kind == "float":
        return repr(float(value))
    return str(value)


def env(name: str, default: str = "") -> str:
    """What used to be ``os.environ.get(name, default)``: the admin's value first, then the environment."""
    setting = BY_ENV.get(name)
    if setting is None or not _active:
        return os.environ.get(name, default)
    if setting.section == "ai" and not _enabled_provider(setting.group):
        return ""  # the admin switched this provider off
    present, value, error = stored(setting.key)
    if present:
        return "" if error else _as_env(setting, value)
    return os.environ.get(name, default)


def _parse(setting: Setting, raw: str) -> Any:
    if setting.kind == "int":
        return int(raw)
    if setting.kind == "float":
        return float(raw)
    if setting.kind == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw


def source(key: str) -> str:
    """admin, environment, default or error (a stored value that cannot be read)."""
    setting = BY_KEY[key]
    present, _, error = stored(key) if _active else (False, None, None)
    if present:
        return "error" if error else "admin"
    if setting.env and os.environ.get(setting.env, "").strip():
        return "environment"
    return "default"


def get(key: str) -> Any:
    """The typed value in use (see the module docstring)."""
    setting = BY_KEY[key]
    if setting.section == "ai" and setting.key.endswith(("api_key", "api_secret", "api_token")) \
            and _active and not _enabled_provider(setting.group):
        return ""
    present, value, error = stored(key)
    if present:
        return ("" if setting.kind in ("secret", "str") else setting.default) if error else value
    if setting.env:
        raw = os.environ.get(setting.env, "").strip()
        if raw:
            try:
                return _parse(setting, raw)
            except ValueError:
                return setting.default
    return setting.default if setting.kind != "secret" else ""


def legacy_in_use() -> list[str]:
    """Settings whose value comes from a legacy environment variable right now (names only)."""
    return [setting.key for setting in SETTINGS if setting.env and source(setting.key) == "environment"]


def environment_report() -> list[dict]:
    """Which known variables this process has (names, categories and whether set; never a value)."""
    rows = [{"name": name, "category": category, "reason": reason, "set": bool(os.environ.get(name, "").strip())}
            for name, (category, reason) in ENVIRONMENT.items()]
    rows += [{"name": setting.env, "category": "legacy", "reason": f"Fallback for {setting.key}",
              "set": bool(os.environ.get(setting.env, "").strip())} for setting in SETTINGS if setting.env]
    return rows


def frontend_origin() -> str:
    """System Settings → frontend_origin (read from the database when active), or this machine's override."""
    if not _active:
        return ""
    try:
        from app.db import Session, local_settings
        from app.models import SystemSetting

        local = local_settings().get("frontend_origin")
        if local:
            return local
        with Session() as db:
            row = db.get(SystemSetting, "frontend_origin")
            return str(json.loads(row.value)).rstrip("/") if row else ""
    except Exception:  # noqa: BLE001
        return ""


def redirect_uri(channel: str) -> str:
    """The OAuth redirect in use: an explicit override (admin, then environment), else derived from the origin."""
    override = env({"youtube": "GOOGLE_OAUTH_REDIRECT_URI", "tiktok": "TIKTOK_REDIRECT_URI",
                    "facebook": "FACEBOOK_REDIRECT_URI"}[channel]).strip()
    if override:
        return override
    origin = frontend_origin()
    return f"{origin}{REDIRECT_PATHS[channel]}" if origin else ""


# --- saving ------------------------------------------------------------------------------------------------

def validate(setting: Setting, value: Any) -> Any:
    """The value to store, or ``ConfigError('invalid_value', key)``. Never echoes the value."""
    if setting.kind == "secret":
        if not isinstance(value, str) or not value.strip() or len(value) > SECRET_MAX:
            raise ConfigError("invalid_value", setting.key)
        return value.strip()
    if setting.kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError("invalid_value", setting.key)
        return value
    if setting.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError("invalid_value", setting.key)
        if setting.kind == "int" and (not float(value).is_integer()):
            raise ConfigError("invalid_value", setting.key)
        value = int(value) if setting.kind == "int" else float(value)
        if (setting.minimum is not None and value < setting.minimum) or \
                (setting.maximum is not None and value > setting.maximum):
            raise ConfigError("out_of_range", setting.key)
        return value
    if not isinstance(value, str) or len(value) > setting.max_length or "\x00" in value:
        raise ConfigError("invalid_value", setting.key)
    value = value.strip()
    if setting.pattern and value and not re.fullmatch(setting.pattern, value):
        raise ConfigError("invalid_value", setting.key)
    return value


def save(db, admin_id: str | None, *, values: dict | None = None, secrets: dict | None = None,
         reset: list | None = None, section: str | None = None) -> list[str]:
    """Store plain ``values``, secret updates ``{key: (keep|replace|clear, value)}`` and ``reset`` keys.

    Clearing a secret or resetting a value deletes the stored row, so the legacy
    environment variable or the default applies again. Everything is validated
    first; nothing is written unless all of it is valid. Returns the changed keys.
    """
    values, secrets, reset = values or {}, secrets or {}, list(reset or [])
    for key in [*values, *secrets, *reset]:
        setting = BY_KEY.get(key)
        if setting is None or (section and setting.section != section):
            raise ConfigError("unknown_setting", key)
    if not ready(db):
        raise ConfigError("not_migrated")
    plain = {}
    for key, value in values.items():
        if BY_KEY[key].kind == "secret":
            raise ConfigError("invalid_request", key)
        plain[key] = validate(BY_KEY[key], value)
    replaced, cleared = {}, []
    for key, (action, value) in secrets.items():
        if BY_KEY[key].kind != "secret" or action not in ("keep", "replace", "clear"):
            raise ConfigError("invalid_request", key)
        if action == "replace":
            replaced[key] = validate(BY_KEY[key], value)
        elif action == "clear":
            cleared.append(key)
    encrypted = {}
    for key, value in replaced.items():
        try:
            encrypted[key] = secret_box.encrypt_json(_purpose(key), {"value": value})
        except secret_box.SecretBoxError as exc:
            raise ConfigError(exc.code, key) from exc
    now = datetime.now(timezone.utc)
    changed = []
    for key in [*reset, *cleared]:
        row = db.get(SystemConfig, key)
        if row is not None:
            db.delete(row)
            changed.append(key)
    for key, value in plain.items():
        row = db.get(SystemConfig, key) or SystemConfig(key=key)
        dumped = json.dumps(value)
        if row.value != dumped or row.ciphertext is not None:
            row.value, row.ciphertext, row.updated_at, row.updated_by_user_id = dumped, None, now, admin_id
            db.add(row)
            changed.append(key)
    for key, ciphertext in encrypted.items():
        row = db.get(SystemConfig, key) or SystemConfig(key=key)
        row.value, row.ciphertext, row.updated_at, row.updated_by_user_id = None, ciphertext, now, admin_id
        db.add(row)
        changed.append(key)
    if changed:
        sections = sorted({BY_KEY[key].section for key in changed})
        for name in sections:
            audit(db, name, "updated", admin_id, changed=sorted(k for k in changed if BY_KEY[k].section == name))
    db.flush()
    invalidate()
    return sorted(set(changed))


def audit(db, section: str, action: str, admin_id: str | None, **metadata) -> None:
    """Record a change or a test; ``metadata`` holds setting names and statuses, never a value."""
    if not ready(db):
        return
    db.add(SystemConfigAudit(section=section, action=action, admin_user_id=admin_id,
                             created_at=datetime.now(timezone.utc), metadata_json=json.dumps(metadata, sort_keys=True)))


# --- the admin view ----------------------------------------------------------------------------------------

def setting_view(db, setting: Setting, editors: dict) -> dict:
    row = db.get(SystemConfig, setting.key) if ready(db) else None
    present, value, error = stored(setting.key) if _active else (row is not None, None, None)
    if not _active and row is not None:
        present = True
        if setting.kind == "secret":
            try:
                secret_box.decrypt_json(_purpose(setting.key), row.ciphertext or "")
            except (secret_box.SecretBoxError, KeyError) as exc:
                error = getattr(exc, "code", "cannot_decrypt")
        else:
            value = json.loads(row.value) if row.value is not None else None
    env_set = bool(setting.env and os.environ.get(setting.env, "").strip())
    view = {"key": setting.key, "section": setting.section, "group": setting.group, "kind": setting.kind,
            "env": setting.env, "source": ("error" if error else "admin") if present
            else "environment" if env_set else "default",
            "minimum": setting.minimum, "maximum": setting.maximum,
            "default": None if setting.kind == "secret" else setting.default,
            "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
            "updated_by": editors.get(row.updated_by_user_id) if row is not None else None}
    if error:
        view["error"] = error
    if setting.kind == "secret":
        view["configured"] = bool(present and not error) or (not present and env_set)
    else:
        view["value"] = get(setting.key) if _active else (value if present else setting.default)
    return view


def section_view(db, section: str) -> list[dict]:
    rows = db.scalars(select(SystemConfig)).all() if ready(db) else []
    ids = {row.updated_by_user_id for row in rows if row.updated_by_user_id}
    editors = dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}
    return [setting_view(db, setting, editors) for setting in SETTINGS if setting.section == section]


def history(db, section: str, limit: int = 10) -> list[dict]:
    if not ready(db):
        return []
    rows = db.execute(select(SystemConfigAudit, User.email).outerjoin(User, User.id == SystemConfigAudit.admin_user_id)
                      .where(SystemConfigAudit.section == section)
                      .order_by(SystemConfigAudit.created_at.desc(), SystemConfigAudit.id.desc()).limit(limit)).all()
    return [{"action": row.action, "at": row.created_at.isoformat(), "by": email,
             "metadata": json.loads(row.metadata_json or "{}")} for row, email in rows]
