"""System alerts (Phase 24): conditions an operator must act on, sent to system admins without spam.

``evaluate`` checks, from the database and the server itself:

* a worker that stopped reporting (``stale``) or reports errors;
* the media disk at 80 % (warning) or 90 % (critical);
* no successful database backup within ``backups.max_age_hours``, or the last backup failed;
* many failed jobs in the last hour;
* rejected payment callbacks (bad signature, wrong amount) in the last hour;
* the master key missing or unable to decrypt stored secrets;
* transactional email failing;
* movie sources (app/movie_sources.py): deletions from Google Drive failing for over an hour, the Drive space
  they use above ``movie_sources.drive.warning_bytes``, or the feature enabled while Drive is not configured.

Each condition has a stable key and a row in ``system_alerts``. Admins get one in-app
notification (``system.alert``) when it starts, then at most one per ``COOLDOWN`` while it
lasts; it resolves silently when it clears. The scheduler worker evaluates every
``INTERVAL_SECONDS`` (``python -m app.alerts`` does it once, by hand).
"""
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import time

from sqlalchemy import func, inspect, select

from app.logs import log_event

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=12)
INTERVAL_SECONDS = 300
DISK_WARNING, DISK_CRITICAL = 80, 90
FAILED_JOBS_PER_HOUR = 10
CALLBACK_ERRORS_PER_HOUR = 3
EMAIL_FAILURES_PER_HOUR = 3
_last_run = {"at": None}


@dataclass
class Condition:
    key: str
    level: str  # warning or critical
    message: str
    details: dict = field(default_factory=dict)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def conditions(db, now: datetime) -> list[Condition]:
    from app import health, heartbeat, master_key, storage, system_config
    from app.models import AuditEvent, BackupRun, EmailOutbox, User, WorkflowJob

    found: list[Condition] = []
    for row in heartbeat.worker_health(db, now):
        if row["status"] == "stale":
            found.append(Condition(f"worker:{row['worker']}", "critical", f"Worker {row['worker']} stopped reporting",
                                   {"worker": row["worker"], "seconds": row.get("seconds_ago")}))
        elif row["status"] == "error":
            found.append(Condition(f"worker:{row['worker']}", "warning", f"Worker {row['worker']} reports errors",
                                   {"worker": row["worker"]}))
    disk = storage.disk_usage(db)
    if disk and disk["percent"] is not None and disk["percent"] >= DISK_WARNING:
        level = "critical" if disk["percent"] >= DISK_CRITICAL else "warning"
        found.append(Condition("disk:media", level, f"Media disk {disk['percent']}% full",
                               {"percent": disk["percent"], "free_bytes": disk["free_bytes"]}))

    if inspect(db.connection()).has_table("backup_runs"):
        max_age = timedelta(hours=int(system_config.get("backups.max_age_hours") or 26))
        last_ok = db.scalar(select(func.max(BackupRun.finished_at)).where(BackupRun.status == "succeeded"))
        last = db.scalars(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1)).first()
        oldest_user = db.scalar(select(func.min(User.created_at)))
        installed_long_enough = oldest_user is not None and now - _aware(oldest_user) > max_age
        if last is not None and last.status == "failed":
            found.append(Condition("backup:failed", "critical", "The last database backup failed",
                                   {"at": _aware(last.started_at).isoformat()}))
        if (last_ok is None and (last is not None or installed_long_enough)) or \
                (last_ok is not None and now - _aware(last_ok) > max_age):
            found.append(Condition("backup:overdue", "critical", "No recent database backup",
                                   {"last_success": _aware(last_ok).isoformat() if last_ok else None,
                                    "max_age_hours": int(max_age.total_seconds() // 3600)}))

    hour = now - timedelta(hours=1)
    failed_jobs = db.scalar(select(func.count()).select_from(WorkflowJob)
                            .where(WorkflowJob.state == "failed", WorkflowJob.updated_at >= hour)) or 0
    if failed_jobs >= FAILED_JOBS_PER_HOUR:
        found.append(Condition("jobs:failures", "warning", f"{failed_jobs} jobs failed in the last hour",
                               {"count": int(failed_jobs)}))
    if inspect(db.connection()).has_table("audit_events"):
        callbacks = db.scalar(select(func.count()).select_from(AuditEvent)
                              .where(AuditEvent.action == "payment.callback_rejected",
                                     AuditEvent.created_at >= hour)) or 0
        if callbacks >= CALLBACK_ERRORS_PER_HOUR:
            found.append(Condition("payments:callbacks", "warning",
                                   f"{callbacks} payment callbacks were rejected in the last hour",
                                   {"count": int(callbacks)}))
    key = master_key.status()
    if key["problem"]:
        found.append(Condition("master_key", "critical", "The master encryption key is not usable",
                               {"problem": key["problem"]}))
    else:
        unreadable = health.undecryptable_secrets(db.connection())
        if unreadable:
            found.append(Condition("master_key", "critical", "Stored secrets cannot be decrypted with this key",
                                   {"problem": "cannot_decrypt", "count": unreadable}))
    if inspect(db.connection()).has_table("email_outbox"):
        failures = db.scalar(select(func.count()).select_from(EmailOutbox)
                             .where(EmailOutbox.last_error.is_not(None), EmailOutbox.next_attempt_at >= hour,
                                    EmailOutbox.status.in_(("queued", "failed")))) or 0
        if failures >= EMAIL_FAILURES_PER_HOUR:
            found.append(Condition("email:failures", "warning", f"{failures} emails could not be sent",
                                   {"count": int(failures)}))
    if inspect(db.connection()).has_table("movie_sources"):
        found += _movie_source_conditions(db, now)
    return found


def _movie_source_conditions(db, now: datetime) -> list[Condition]:
    from app import movie_sources
    from app.models import MovieSource

    found = []
    stuck = db.scalar(select(func.count()).select_from(MovieSource).where(
        MovieSource.status.in_(("delete_scheduled", "deleting")), MovieSource.failure_code.is_not(None),
        MovieSource.delete_requested_at <= now - timedelta(hours=1))) or 0
    if stuck:
        found.append(Condition("movie_sources:deletion", "warning",
                               f"{stuck} movie sources could not be deleted from Google Drive", {"count": int(stuck)}))
    summary = movie_sources.summary(db, now)
    if summary["over_warning"]:
        found.append(Condition("movie_sources:drive_usage", "warning",
                               "Movie sources use more Google Drive space than the warning level",
                               {"bytes": summary["bytes"], "warning_bytes": summary["warning_bytes"],
                                "files": summary["files"]}))
    if summary["enabled"] and summary["drive_problem"]:
        found.append(Condition("movie_sources:drive", "warning",
                               "Movie sources are enabled but Google Drive is not configured",
                               {"problem": summary["drive_problem"]}))
    return found


def evaluate(db, now: datetime | None = None) -> dict:
    from app import notifications
    from app.models import SystemAlert

    now = now or datetime.now(timezone.utc)
    found = {condition.key: condition for condition in conditions(db, now)}
    rows = {row.key: row for row in db.scalars(select(SystemAlert))}
    notified = 0
    for key, condition in found.items():
        row = rows.get(key)
        if row is None:
            row = SystemAlert(key=key, first_seen_at=now, active=False)
            db.add(row)
        if not row.active:
            row.active, row.first_seen_at, row.resolved_at, row.last_notified_at = True, now, None, None
        row.level, row.message = condition.level, condition.message[:300]
        row.details_json, row.last_seen_at = json.dumps(condition.details, default=str), now
        last = _aware(row.last_notified_at)
        if last is None or now - last >= COOLDOWN:
            params = {"key": key, "level": condition.level,
                      **{name: value for name, value in condition.details.items() if not isinstance(value, dict)}}
            notifications.notify_admins(db, "system.alert", condition.message, condition.message,
                                        link="/admin?tab=verification", params=params,
                                        dedupe=f"alert:{key}:{now:%Y%m%d%H%M}")
            row.last_notified_at = now
            notified += 1
            log_event(logger, "system_alert", level=logging.WARNING, key=key, alert_level=condition.level)
    for key, row in rows.items():
        if row.active and key not in found:
            row.active, row.resolved_at = False, now
            log_event(logger, "system_alert_resolved", key=key)
    return {"active": len(found), "notified": notified}


def active(db) -> list[dict]:
    from app.models import SystemAlert

    if not inspect(db.connection()).has_table("system_alerts"):
        return []
    rows = db.scalars(select(SystemAlert).where(SystemAlert.active.is_(True))
                      .order_by(SystemAlert.level, SystemAlert.first_seen_at)).all()
    return [{"key": row.key, "level": row.level, "message": row.message,
             "details": json.loads(row.details_json or "{}"), "since": _aware(row.first_seen_at).isoformat(),
             "last_seen_at": _aware(row.last_seen_at).isoformat()} for row in rows]


def maybe_evaluate(session_factory=None, *, force: bool = False) -> dict | None:
    """Evaluate at most every INTERVAL_SECONDS (the scheduler worker calls this on every pass)."""
    moment = time.monotonic()
    if not force and _last_run["at"] is not None and moment - _last_run["at"] < INTERVAL_SECONDS:
        return None
    _last_run["at"] = moment
    if session_factory is None:
        from app.db import Session as session_factory
    with session_factory.begin() as db:
        if not inspect(db.connection()).has_table("system_alerts"):
            return None
        return evaluate(db)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate ReelForge system alerts once and print the active ones")
    parser.parse_args(argv)
    from app.runtime_env import start_process

    start_process("alerts")
    result = maybe_evaluate(force=True) or {}
    from app.db import Session

    with Session() as db:
        print(json.dumps({**result, "alerts": active(db)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
