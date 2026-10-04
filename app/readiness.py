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
# The release gates an administrator records by hand in Admin → Verification (docs/RELEASE_V1_CHECKLIST.md maps
# every gate of the release checklist to one of these, or to deploy/release-preflight.sh). Each is key, group, paid
# or not, and the command or place that performs it (shown beside the item). Groups are listed in display order.
CHECKLIST = (
    ("release_ci_green", "release", False, "GitHub Actions on the deployed commit: every job green "
                                           "(SQLite, PostgreSQL, migrations, Node 20/22, Python 3.11/3.14, Playwright)"),
    ("release_deploy", "release", False, "./deploy.sh on the release commit ends with \"ReelForge deployment completed OK\""),
    ("release_preflight", "release", False, "deploy/release-preflight.sh on the server: no FAIL"),
    # The production domain change (migration 0026): every outside system that holds the public address.
    ("domain_cloudflare_route", "domain", False, "Cloudflare Tunnel: the public hostname → http://127.0.0.1:3001; "
                                                 "the old hostname removed or redirected"),
    ("domain_google_redirect", "domain", False, "Google Cloud → OAuth client: the YouTube redirect URI shown in "
                                                "Admin → System settings → Social OAuth"),
    ("domain_tiktok_redirect", "domain", False, "TikTok for Developers: the redirect URI shown in Admin → System "
                                                "settings → Social OAuth"),
    ("domain_facebook_redirect", "domain", False, "Meta for Developers → Facebook Login: the redirect URI shown in "
                                                  "Admin → System settings → Social OAuth"),
    ("domain_payos_webhook", "domain", False, "payOS: the webhook URL shown in Admin → Payments → Payment gateways "
                                              "→ VietQR"),
    ("domain_onepay_urls", "domain", False, "OnePAY: the IPN and return URLs shown in Admin → Payments → Payment "
                                            "gateways → Card"),
    ("migration_upgraded", "platform", False, "python -m alembic current shows the head; existing accounts still sign in"),
    ("storage_on_hdd", "platform", False, "Media root /srv/data/videos/reelforge; Admin → Operations shows its disk"),
    ("ffmpeg_verified", "platform", False, "python -m app.render_worker --check"),
    ("master_key_file", "platform", False, "python -m app.master_key status: a file, chmod 600; a copy off the server"),
    ("email_test_sent", "email", False, "System settings → Email: Send test email; it arrives"),
    ("email_dns", "email", False, "SPF, DKIM and DMARC pass in the received message's headers"),
    ("email_verification", "email", False, "Register a test account; the verification email arrives and verifies"),
    ("email_password_reset", "email", False, "Forgot password, email, reset; other sessions are signed out"),
    ("email_password_changed", "email", False, "The \"password changed\" email arrives after the reset"),
    ("email_invitation", "email", False, "A studio invitation email arrives; its link joins the studio"),
    ("email_support_reply", "email", False, "An admin reply to a ticket reaches the user by email"),
    ("security_two_factor", "security", False, "Every system admin has 2FA; sign in with a code, then with a "
                                               "recovery code"),
    ("security_sessions", "security", False, "Sign out another session from Settings, Security"),
    ("security_rate_limit", "security", False, "Wrong passwords end in 429 and the lockout email; an unknown account "
                                               "gets the same answer as a wrong password"),
    ("security_headers", "security", False, "curl -sI the public origin: HTTPS, HSTS, CSP, nosniff, Referrer-Policy; "
                                            "rf_session is HttpOnly, Secure, SameSite=Strict"),
    ("security_foreign_origin", "security", False, "A POST with a foreign Origin is refused (403)"),
    ("security_client_ip", "security", False, "Admin → Audit log shows your real public address for a sign-in, "
                                              "not 127.0.0.1"),
    ("security_spoofed_headers", "security", False, "A forged CF-Connecting-IP / X-Forwarded-For sent from outside "
                                                    "does not change the recorded address"),
    ("gemini_text_live", "ai", True, "A script step with Gemini"),
    ("gemini_tts_live", "ai", True, "python -m app.smoke_test voice --live"),
    ("transcription_live", "ai", True, "A Transcript step on a short clip with speech"),
    ("runway_image_live", "ai", True, "An Image step with Runway"),
    ("runway_video_live", "ai", True, "A Video step with Runway (one short clip)"),
    ("ai_credits_once", "ai", False, "Credit history: each successful step charged once; a failed provider call refunded"),
    ("final_render_live", "render", True, "Render with voice, subtitles and music: an MP4 under the media root"),
    ("article_video_live", "render", True, "Article → Video template (a slideshow)"),
    ("product_video_live", "render", True, "Product Video template (a slideshow)"),
    ("movie_recap_live", "render", True, "Movie Recap template"),
    ("movie_review_live", "render", True, "Movie Review template"),
    ("youtube_upload", "publishing", False, "Publish a private video to YouTube; note its video ID"),
    ("tiktok_upload", "publishing", False, "Publish to TikTok (inbox draft); note its publish ID"),
    ("facebook_reel", "publishing", False, "Publish a Facebook Reel; note its URL"),
    ("scheduled_publishing", "publishing", False, "Schedule a post a few minutes ahead"),
    ("bank_qr_round_trip", "vietqr", True, "Manual VietQR: transfer, report, admin confirms, credits once, receipt; "
                                           "confirming again changes nothing"),
    ("payos_config_saved", "vietqr", False, "Admin → Payments → Payment gateways → VietQR: Save, then Test"),
    ("payos_payment", "vietqr", True, "One small VietQR payment"),
    ("payos_webhook_received", "vietqr", False, "The last webhook time appears under VietQR activity"),
    ("payos_credits_once", "vietqr", False, "The credit history shows the plan's credits once"),
    ("onepay_sandbox_configured", "card", False, "Card: Sandbox mode, saved"),
    ("onepay_sandbox_check", "card", False, "Card: Test configuration and the QueryDR check"),
    ("onepay_sandbox_payment", "card", False, "OnePAY test card on the sandbox page"),
    ("onepay_sandbox_cancel", "card", False, "Cancel on the OnePAY page; the order shows cancelled"),
    ("onepay_ipn_received", "card", False, "The last IPN time appears under Card activity"),
    ("onepay_querydr_verified", "card", False, "Check on an order confirms it through QueryDR"),
    ("onepay_production_configured", "card", False, "Card: Production mode saved after confirmation"),
    ("onepay_production_payment", "card", True, "One small real card payment"),
    ("onepay_credits_once", "card", False, "The credit history shows the plan's credits once"),
    ("notification_realtime", "operations", False, "Check the notification stream below, through Cloudflare"),
    ("alerts_delivered", "operations", False, "Stop one worker past its stale time, then start it: the admins are "
                                              "alerted"),
    ("support_round_trip", "operations", False, "User ticket → admin reply → user sees it"),
    ("cleanup_dry_run", "operations", False, "python -m app.media_maintenance --intermediates"),
    ("maintenance_timer", "operations", False, "systemctl list-timers reelforge-media-maintenance.timer"),
    ("backup_timer", "operations", False, "systemctl list-timers reelforge-backup.timer; a dump appears daily; "
                                          "retention reviewed"),
    ("backup_offsite", "operations", False, "The dumps and, separately, the master key are copied off the server"),
    ("restore_rehearsal", "operations", False, "deploy/restore-check.sh with --scratch-url and --master-key passes"),
    ("media_backup_verified", "operations", False, "Media copied elsewhere; python -m app.media_manifest verify "
                                                   "finds nothing missing"),
    ("server_reboot", "operations", False, "Reboot; every service and the tunnel come back; readiness is green"),
    # Movie sources (docs/MOVIE_SOURCE_VERIFICATION.md): optional, "not applicable" while the feature is off.
    ("movie_drive_connection", "movie_sources", False, "Admin → System settings → Movie sources → Test connection: "
                                                       "credentials, root folder, upload and delete pass; no test "
                                                       "file is left in Drive"),
    ("movie_import_local", "movie_sources", False, "Media → Movie sources → Add → Server file: a movie from the "
                                                   "import folder becomes Ready with the right size and duration"),
    ("movie_import_url", "movie_sources", False, "Add → Direct URL: an https movie you may use becomes Ready; "
                                                 "https://127.0.0.1/… and a page that is not a movie are refused"),
    ("movie_scratch_download", "movie_sources", False, "Two reviews of one source: the movie worker log shows "
                                                       "movie_scratch_downloaded once"),
    ("movie_pipeline_live", "movie_sources", True, "Use for Movie Review on a Ready source: frames analysed, a "
                                                   "review with time ranges, excerpts, voice, subtitles, a final MP4"),
    ("movie_source_deletion", "movie_sources", False, "Delete now on a source no run uses: Deleted, the Drive file "
                                                      "in the trash; a source in use is refused"),
    ("movie_retention_cleanup", "movie_sources", False, "A source past its retention, or past the grace period "
                                                        "after a successful review, is deleted automatically"),
    ("legal_terms_reviewed", "legal", False, "Terms of Service: every [bracketed] item filled in and reviewed; "
                                             "TERMS_VERSION set"),
    ("legal_privacy_reviewed", "legal", False, "Privacy Policy: processors, retention and contact filled in, reviewed"),
)
CHECKLIST_KEYS = tuple(item[0] for item in CHECKLIST)
# A provider or platform an installation may leave switched off: only these may be "not applicable". Every other
# gate must pass (docs/V1_RELEASE_STATUS.md).
OPTIONAL = frozenset({
    "runway_image_live", "runway_video_live", "tiktok_upload", "facebook_reel",
    "payos_config_saved", "payos_payment", "payos_webhook_received", "payos_credits_once",
    *(key for key in CHECKLIST_KEYS if key.startswith("onepay_")),
    "domain_tiktok_redirect", "domain_facebook_redirect", "domain_payos_webhook", "domain_onepay_urls",
    *(key for key in CHECKLIST_KEYS if key.startswith("movie_") and key not in ("movie_recap_live",
                                                                                 "movie_review_live")),
})
STATUSES = ("passed", "failed", "not_applicable", "not_checked")


def checklist_summary(statuses: dict) -> dict:
    """Counts per status and the gates still open: every gate passes, or is not applicable where that is allowed.

    ``statuses`` maps a key to what an administrator recorded (None or absent: not checked). Only recorded statuses
    count: the server never completes a gate by itself."""
    counts = dict.fromkeys(STATUSES, 0)
    still_open = []
    for key in CHECKLIST_KEYS:
        status = statuses.get(key) or "not_checked"
        counts[status if status in counts else "not_checked"] += 1
        if not (status == "passed" or (status == "not_applicable" and key in OPTIONAL)):
            still_open.append(key)
    return {**counts, "total": len(CHECKLIST_KEYS), "open": still_open, "complete": not still_open}


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


def movie_source_checks(db, now: datetime) -> list[dict]:
    """Movie sources (app/movie_sources.py): ``off`` until enabled, then Google Drive, the import folder, the
    scratch space, the Drive space they use and deletions that keep failing. Drive itself is not called here:
    Admin → System settings → Movie sources → Test connection does that on purpose."""
    from sqlalchemy import inspect

    from app import google_drive, movie_sources

    current = movie_sources.settings()
    if not inspect(db.connection()).has_table("movie_sources"):
        return [_check("movie_sources", "off", "not_migrated")]
    if not current.enabled:
        return [_check("movie_sources", "off", "disabled")]
    checks = [_check("movie_sources", "ok")]
    problem = google_drive.config().problem()
    checks.append(_check("google_drive", "missing" if problem else "ok", problem))
    root = movie_sources.import_root()
    found = root is not None and root.is_dir()
    checks.append(_check("import_folder", "ok" if found else "warning", None if found else "not_found"))
    scratch = movie_sources.scratch_root(db)
    if not scratch.is_dir():
        checks.append(_check("scratch_space", "warning", "not_created"))  # created by the first import
    else:
        probe = scratch / f".readiness-{uuid.uuid4()}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
            checks.append(_check("scratch_space", "ok"))
        except OSError:
            checks.append(_check("scratch_space", "error", "not_writable"))
    summary = movie_sources.summary(db, now)
    checks.append(_check("drive_usage", "warning" if summary["over_warning"] else "ok",
                         "over_warning" if summary["over_warning"] else None, files=summary["files"],
                         bytes=summary["bytes"], warning_bytes=summary["warning_bytes"]))
    checks.append(_check("deletions", "warning" if summary["delete_failures"] else "ok",
                         "failing" if summary["delete_failures"] else None, failing=summary["delete_failures"]))
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
    """Each OAuth app: configured or not, where its secret comes from, and the redirect URL in use."""
    from app import system_config
    from app.publishers import channel_oauth, google_oauth

    secrets = {"youtube": "social.youtube.client_secret", "tiktok": "social.tiktok.client_secret",
               "facebook": "social.facebook.app_secret"}
    origin = system_config.frontend_origin().rstrip("/")
    checks = []
    for channel in ("youtube", "tiktok", "facebook"):
        if channel == "youtube":
            try:
                google_oauth.GoogleOAuthConfig.from_environment()
                configured = True
            except google_oauth.OAuthError:
                configured = False
        else:
            configured = channel_oauth.configured(channel)
        redirect = system_config.redirect_uri(channel)
        status, detail = ("ok", None) if configured else ("missing", "not_configured")
        # An override left on another origin (an old domain, say) sends sign-ins where the API refuses them.
        if configured and origin and redirect and not redirect.startswith(origin + "/"):
            status, detail = "warning", "redirect_mismatch"
        checks.append(_check(channel, status, detail, source=system_config.source(secrets[channel]),
                             redirect=redirect or None))
    return checks


def configuration_checks() -> list[dict]:
    """Phase 21: how far this server still depends on environment variables (names only, never values)."""
    from app import runtime_env, system_config

    legacy = system_config.legacy_in_use()
    checks = [_check("legacy_settings", "warning" if legacy else "ok", "from_environment" if legacy else None,
                     count=len(legacy), names=legacy[:12])]
    loaded = runtime_env.LOADED
    checks.append(_check("runtime_file", "warning" if loaded["path"] else "ok", "loaded" if loaded["path"] else None,
                         path=loaded["path"], values=len(loaded["names"])))
    checks.append(_check("cache", "ok", cache_seconds=system_config.CACHE_SECONDS))
    return checks


def _source(env_name: str) -> str:
    from app import system_config

    setting = system_config.BY_ENV.get(env_name)
    return system_config.source(setting.key) if setting else "environment"


def security_checks(db=None) -> list[dict]:
    """The master key: from a file (chmod 600) is the target; the legacy variable still works, with a warning.
    Phase 25: an admin confirms the key is backed up off the server (it is never copied next to the dumps)."""
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
    checks = [_check("master_key", status, detail, path=info["path"], key_source=info["source"])]
    if db is not None:
        confirmation = master_key_backup(db)
        fingerprint = key_fingerprint()
        if fingerprint is None:
            checks.append(_check("master_key_backup", "off", "no_key"))
        elif confirmation is None:
            checks.append(_check("master_key_backup", "warning", "not_confirmed"))
        elif confirmation.get("fingerprint") != fingerprint:
            checks.append(_check("master_key_backup", "warning", "key_changed",
                                 confirmed_at=confirmation.get("confirmed_at")))
        else:
            checks.append(_check("master_key_backup", "ok", None, confirmed_at=confirmation.get("confirmed_at"),
                                 confirmed_by=confirmation.get("confirmed_by")))
    return checks


MASTER_KEY_BACKUP = "master_key_backup"


def key_fingerprint() -> str | None:
    """A short, irreversible fingerprint of the key in use, to notice when it changes (never the key)."""
    import hashlib

    from app import master_key

    key = master_key.load()
    return hashlib.sha256(("reelforge-key-fingerprint:" + key).encode("ascii")).hexdigest()[:16] if key else None


def master_key_backup(db) -> dict | None:
    row = db.get(SystemSetting, MASTER_KEY_BACKUP)
    try:
        return json.loads(row.value) if row else None
    except ValueError:
        return None


def backup_checks(db, now: datetime) -> list[dict]:
    """The last database backup (python -m app.backup run, the reelforge-backup timer)."""
    from sqlalchemy import inspect

    from app import backup

    if not inspect(db.connection()).has_table("backup_runs"):
        return [_check("database_backup", "warning", "not_migrated")]
    info = backup.status(db, now)
    last, failure = info["last_success"], info["last_failure"]
    values = {"directory": info["directory"], "last_success": last["at"] if last else None,
              "age_hours": last["age_hours"] if last else None, "file": last["file"] if last else None,
              "last_failure": failure["at"] if failure else None, "max_age_hours": info["max_age_hours"]}
    if last is None:
        status, detail = "error", "never"
    elif last["age_hours"] > info["max_age_hours"]:
        status, detail = "error", "overdue"
    elif failure and failure["at"] > last["at"]:
        status, detail = "warning", "last_failed"
    else:
        status, detail = "ok", None
    return [_check("database_backup", status, detail, **values)]


def email_checks(db) -> list[dict]:
    """Transactional email (Phase 22): password reset, verification and invitations need it."""
    from app import mailer

    problem = mailer.problem()
    cfg = mailer.config()
    if problem is None:
        status, detail = "ok", None
    elif problem == "disabled":
        status, detail = "warning", "disabled"
    else:
        status, detail = "error", problem
    stats = mailer.stats(db, since=datetime.now(timezone.utc) - timedelta(days=1))
    return [_check("email", status, detail, provider=cfg["provider"]),
            _check("outbox", "warning" if stats["recent_failed"] else "ok",
                   "failures" if stats["recent_failed"] else None, queued=stats["queued"],
                   failed_24h=stats["recent_failed"], last_error=stats["last_error"])]


def account_checks(db) -> list[dict]:
    """System admins should use two-factor authentication."""
    from app.models import User

    admins = db.scalars(select(User).where(User.is_admin.is_(True), User.is_active.is_(True))).all()
    without = [admin for admin in admins if not admin.totp_enabled_at]
    return [_check("admin_two_factor", "warning" if without else "ok", "missing" if without else None,
                   admins=len(admins), without_two_factor=len(without))]


def alert_checks(db) -> list[dict]:
    from app import alerts

    active = alerts.active(db)
    if not active:
        return [_check("alerts", "ok", None, active=0)]
    return [_check("alert", "error" if item["level"] == "critical" else "warning", item["key"], since=item["since"],
                   **{key: value for key, value in item["details"].items() if not isinstance(value, (dict, list))})
            for item in active]


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
    # What buyers get for "VietQR / Bank Transfer" right now: the provider of the current mode.
    from app import payment_providers

    active = payment_providers.for_method("vietqr")
    offered = active.offered()
    checks.insert(0, _check("vietqr", "ok" if offered else "off", None if offered else "not_available",
                            vietqr_mode=mode, provider=active.name))
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
        ("security", security_checks(db)),
        ("backups", backup_checks(db, now)),
        ("email", email_checks(db)),
        ("accounts", account_checks(db)),
        ("alerts", alert_checks(db)),
        ("configuration", configuration_checks()),
        ("movie_sources", movie_source_checks(db, now)),
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
