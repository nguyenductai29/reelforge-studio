"""Readiness checks and the manual checklist for live verification (Phase 18D).

Every check here is local and safe: a database query, a write probe in the storage
root, the presence of FFmpeg and the subtitle font, worker heartbeats, and whether
credentials are configured. None reads out a secret, calls a paid API, creates a
checkout or publishes anything. Paid live tests stay explicit CLI commands
(``CHECKLIST`` names them) that an operator runs on purpose.

Statuses: ``ok``, ``warning`` (works but needs attention), ``error`` (will fail),
``missing`` (not configured and needed) and ``off`` (an optional feature not configured).
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid

from sqlalchemy import func, select, text

from app import heartbeat, render, storage
from app.models import AITool, SupportTicket, SystemSetting
from app.runtime_env import ROOT

MAINTENANCE_KEY = "storage_maintenance"
# key, group, paid or not, and the command or place that performs it (shown beside the checklist item).
CHECKLIST = (
    ("migration_upgraded", "platform", False, "python -m alembic upgrade head"),
    ("storage_on_hdd", "platform", False, "REELFORGE_STORAGE_ROOT=/srv/data/videos/reelforge"),
    ("ffmpeg_verified", "platform", False, "python -m app.render_worker --check"),
    ("master_key_file", "platform", False, "python -m app.master_key init; chmod 600; backed up off the server"),
    ("gemini_tts_live", "ai", True, "python -m app.smoke_test voice --live"),
    ("final_render_live", "ai", True, "Run a workflow with Render"),
    ("movie_recap_live", "ai", True, "Movie Recap template"),
    ("article_video_live", "ai", True, "Article → Video template"),
    ("product_video_live", "ai", True, "Product Video template"),
    ("youtube_upload", "publishing", False, "Publish a private video to YouTube"),
    ("tiktok_upload", "publishing", False, "Publish to TikTok (inbox draft)"),
    ("facebook_reel", "publishing", False, "Publish a Facebook Reel"),
    ("scheduled_publishing", "publishing", False, "Schedule a post a few minutes ahead"),
    ("payos_config_saved", "vietqr", False, "Admin → Payments → Payment gateways → VietQR: Save, then Test"),
    ("payos_payment", "vietqr", True, "One small VietQR payment"),
    ("payos_webhook_received", "vietqr", False, "The last webhook time appears under VietQR activity"),
    ("payos_credits_once", "vietqr", False, "The credit history shows the plan's credits once"),
    ("bank_qr_round_trip", "vietqr", True, "Manual VietQR: scan, transfer, report, admin confirms, credits once"),
    ("onepay_sandbox_configured", "card", False, "Card: Sandbox mode, saved"),
    ("onepay_sandbox_check", "card", False, "Card: Test configuration and the QueryDR check"),
    ("onepay_sandbox_payment", "card", False, "OnePAY test card on the sandbox page"),
    ("onepay_sandbox_cancel", "card", False, "Cancel on the OnePAY page; the order shows cancelled"),
    ("onepay_ipn_received", "card", False, "The last IPN time appears under Card activity"),
    ("onepay_querydr_verified", "card", False, "Check on an order confirms it through QueryDR"),
    ("onepay_production_configured", "card", False, "Card: Production mode saved after confirmation"),
    ("onepay_production_payment", "card", True, "One small real card payment"),
    ("onepay_credits_once", "card", False, "The credit history shows the plan's credits once"),
    ("notification_realtime", "operations", False, "Check the notification stream below"),
    ("support_round_trip", "operations", False, "User ticket → admin reply → user sees it"),
    ("cleanup_dry_run", "operations", False, "python -m app.media_maintenance --intermediates"),
    ("maintenance_timer", "operations", False, "systemctl list-timers reelforge-media-maintenance.timer"),
)
CHECKLIST_KEYS = tuple(item[0] for item in CHECKLIST)
AI_KEYS = (("gemini", ("GEMINI_API_KEY",), True), ("runway", ("RUNWAYML_API_SECRET", "RUNWAY_OUTPUT_HOSTS"), True),
           ("openai", ("OPENAI_API_KEY",), True), ("anthropic", ("ANTHROPIC_API_KEY",), False),
           ("fal", ("FAL_KEY",), False), ("runware", ("RUNWARE_API_KEY",), False),
           ("replicate", ("REPLICATE_API_TOKEN",), False))


def _check(key: str, status: str, detail: str | None = None, **values) -> dict:
    return {"key": key, "status": status, **({"detail": detail} if detail else {}), **values}


def _set(name: str) -> bool:
    from app import system_config

    return bool(system_config.env(name).strip())


def database(db) -> list[dict]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    checks = []
    try:
        db.execute(text("SELECT 1"))
        dialect = db.get_bind().dialect.name
        checks.append(_check("connection", "ok" if dialect == "postgresql" else "warning",
                             None if dialect == "postgresql" else "not_postgresql", dialect=dialect))
    except Exception:  # noqa: BLE001
        return [_check("connection", "error", "unreachable")]
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    current = db.scalar(text("SELECT version_num FROM alembic_version"))
    checks.append(_check("migration", "ok" if current == head else "error", None if current == head else "behind",
                         current=current, head=head))
    return checks


def storage_checks(db, now: datetime) -> list[dict]:
    root = storage.media_root(db)
    source = "environment" if _set(storage.STORAGE_ROOT_ENV) else "setting"
    checks = [_check("root", "ok" if source == "environment" else "warning",
                     None if source == "environment" else "default_or_setting", source=source, path=str(root))]
    probe = Path(root) / f".readiness-{uuid.uuid4()}"
    if not Path(root).is_dir():
        # Created by the first upload; a check never creates folders.
        checks.append(_check("writable", "warning", "not_created"))
    else:
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
            checks.append(_check("writable", "ok"))
        except OSError:
            checks.append(_check("writable", "error", "not_writable"))
    disk = storage.disk_usage(db)
    if disk is None:
        checks.append(_check("disk", "error", "unavailable"))
    else:
        free = disk["free_bytes"] / disk["total_bytes"] if disk["total_bytes"] else 0
        checks.append(_check("disk", "ok" if free >= 0.1 else "warning", None if free >= 0.1 else "low_space",
                             free_bytes=disk["free_bytes"], total_bytes=disk["total_bytes"]))
    row = db.get(SystemSetting, MAINTENANCE_KEY)
    last = json.loads(row.value).get("last_run") if row else None
    if not last:
        checks.append(_check("maintenance", "warning", "never_run"))
    else:
        age = now - datetime.fromisoformat(last)
        checks.append(_check("maintenance", "ok" if age <= timedelta(hours=36) else "warning",
                             None if age <= timedelta(hours=36) else "stale", last_run=last))
    return checks


def ffmpeg_checks() -> list[dict]:
    tools = render.tools_issue()
    checks = [_check("ffmpeg", "error" if tools else "ok", tools[0] if tools else None)]
    try:
        font = render.font_issue()
    except render.RenderError as exc:
        font = (exc.code, str(exc))
    checks.append(_check("subtitle_font", "warning" if font else "ok", font[0] if font else None))
    return checks


def worker_checks(db, now: datetime) -> list[dict]:
    status = {"ok": "ok", "stale": "warning", "error": "error", "missing": "missing"}
    return [_check(item["worker"], status.get(item["status"], "warning"),
                   None if item["status"] == "ok" else item["status"], last_seen_at=item.get("last_seen_at"))
            for item in heartbeat.worker_health(db, now)]


def ai_checks(db) -> list[dict]:
    used = dict(db.execute(select(AITool.provider, func.count(AITool.id)).where(AITool.is_enabled.is_(True))
                           .group_by(AITool.provider)).all())
    checks = []
    for provider, names, primary in AI_KEYS:
        configured = all(_set(name) for name in names)
        in_use = int(used.get(provider, 0))
        status = "ok" if configured else "error" if in_use else "missing" if primary else "off"
        checks.append(_check(provider, status, None if configured else "key_missing", variables=list(names),
                             enabled_models=in_use, source=_source(names[0])))
    return checks


def publishing_checks() -> list[dict]:
    from app.publishers import channel_oauth, google_oauth

    checks = []
    try:
        google_oauth.GoogleOAuthConfig.from_environment()
        checks.append(_check("youtube", "ok"))
    except google_oauth.OAuthError:
        checks.append(_check("youtube", "missing", "not_configured"))
    for channel in ("tiktok", "facebook"):
        checks.append(_check(channel, "ok" if channel_oauth.configured(channel) else "missing",
                             None if channel_oauth.configured(channel) else "not_configured"))
    return checks


def _source(env_name: str) -> str:
    from app import system_config

    setting = system_config.BY_ENV.get(env_name)
    return system_config.source(setting.key) if setting else "environment"


def security_checks() -> list[dict]:
    """The master key: from a file (chmod 600) is the target; the legacy variable still works, with a warning."""
    from app import master_key

    info = master_key.status()
    if info["problem"]:
        status, detail = "error", info["problem"]
    elif info["source"] == "legacy_env":
        status, detail = "warning", "legacy_env"
    elif info["permissions_ok"] is False:
        status, detail = "warning", "permissions"
    elif info["legacy_env_matches"] is False:
        status, detail = "warning", "legacy_differs"
    else:
        status, detail = "ok", None
    return [_check("master_key", status, detail, path=info["path"], key_source=info["source"])]


def payment_checks(db) -> list[dict]:
    """Each provider as resolved (admin-managed, else bootstrap/env), and the key that protects saved credentials."""
    from app import payment_config, secret_box

    checks = []
    for provider in payment_config.PROVIDERS:
        resolved = payment_config.get(provider, db)
        usable = payment_config.usable(resolved)
        if resolved.error:
            status, detail = "error", resolved.error
        elif usable:
            status, detail = ("ok", None) if resolved.enabled else ("warning", "disabled")
        else:
            status, detail = "off", "not_configured"
        values = {"source": resolved.source, "enabled": resolved.enabled}
        if provider == "onepay":
            values["mode"] = resolved.mode
        checks.append(_check(provider, status, detail, **values))
    from app import bank_qr, system_config
    from app.payment_providers import vietqr_mode

    # Manual VietQR (Phase 20): offered when it is the VietQR mode, enabled and complete.
    mode, ready_manual = vietqr_mode(), bank_qr.configured()
    manual_on = bool(system_config.get("payments.bank_qr.enabled"))
    status, detail = (("ok", None) if manual_on else ("warning", "disabled")) if ready_manual else ("off", "not_configured")
    checks.append(_check("bank_qr", status, detail, vietqr_mode=mode))
    saved = any(payment_config.get(provider, db).source == "admin" for provider in payment_config.PROVIDERS)
    if secret_box.available():
        checks.append(_check("encryption", "ok"))
    else:
        # Without the key, admin-managed gateways cannot be saved, and saved ones cannot be read.
        checks.append(_check("encryption", "error" if saved else "off", "key_missing"))
    return checks


def report(db, *, streams: int, poll_seconds: float) -> dict:
    """Every section in one pass; each check is cheap and local."""
    now = datetime.now(timezone.utc)
    open_tickets = db.scalar(select(func.count()).select_from(SupportTicket)
                             .where(SupportTicket.status.in_(("open", "waiting_support"))))
    sections = [
        ("database", database(db)),
        ("storage", storage_checks(db, now)),
        ("ffmpeg", ffmpeg_checks()),
        ("workers", worker_checks(db, now)),
        ("ai", ai_checks(db)),
        ("publishing", publishing_checks()),
        ("payments", payment_checks(db)),
        ("security", security_checks()),
        ("realtime", [_check("stream", "ok", open_streams=streams, poll_seconds=poll_seconds)]),
        ("support", [_check("tickets", "ok", awaiting_support=int(open_tickets or 0))]),
    ]
    return {"checked_at": now.isoformat(), "sections": [{"key": key, "checks": checks} for key, checks in sections]}


def record_maintenance(session_factory, *, expired: int, freed_bytes: int) -> None:
    """The cleanup job's last ``--apply --intermediates`` run, for the readiness view (best effort)."""
    try:
        with session_factory.begin() as db:
            value = json.dumps({"last_run": datetime.now(timezone.utc).isoformat(), "expired": expired,
                                "freed_bytes": freed_bytes})
            row = db.get(SystemSetting, MAINTENANCE_KEY)
            if row is None:
                db.add(SystemSetting(key=MAINTENANCE_KEY, value=value))
            else:
                row.value = value
    except Exception:  # noqa: BLE001 - the report must never fail the cleanup itself
        pass
