"""Operational metrics in the Prometheus text format (Phase 24): ``GET /internal/metrics``.

Two kinds:

* **This API process** (counters since it started): HTTP requests by method, route
  template and status, their duration, login failures, webhook errors, open
  notification streams.
* **The installation** (read from the database at each scrape, so every process and
  worker counts): jobs by state and recent failures, worker heartbeat ages, workflow
  runs and their recent durations, publications and recent failures, payment orders
  waiting or failed, email outbox, media storage and free disk, the last backup, active
  alerts, recent login failures.

Labels are bounded sets (routes, states, node types, channels, providers, workers);
never an email address, an ID, a title or any user text.

The endpoint is not under ``/api/``, so the Next.js proxy never exposes it; the API itself
only answers it for a loopback client (a Prometheus or node agent on the server) or a
signed-in system admin.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import threading

from sqlalchemy import func, select

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_lock = threading.Lock()
_requests: dict[tuple[str, str, str], int] = defaultdict(int)
_durations: dict[tuple[str, str], list] = {}
_counters: dict[tuple[str, tuple], int] = defaultdict(int)
_gauges: dict[str, float] = {}


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    with _lock:
        _requests[(method, route, str(status))] += 1
        entry = _durations.setdefault((method, route), [0.0, 0, [0] * len(BUCKETS)])
        entry[0] += seconds
        entry[1] += 1
        for index, bound in enumerate(BUCKETS):
            if seconds <= bound:
                entry[2][index] += 1


def inc(name: str, **labels: str) -> None:
    with _lock:
        _counters[(name, tuple(sorted(labels.items())))] += 1


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


def _escape(value) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**labels) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in labels.items()) + "}"


class _Out:
    def __init__(self):
        self.lines: list[str] = []
        self.seen: set[str] = set()

    def metric(self, name: str, kind: str, help_text: str, samples: list[tuple[dict, float]]) -> None:
        if name not in self.seen:
            self.lines.append(f"# HELP {name} {help_text}")
            self.lines.append(f"# TYPE {name} {kind}")
            self.seen.add(name)
        for labels, value in samples:
            self.lines.append(f"{name}{_labels(**labels)} {float(value):g}")


def _process(out: _Out) -> None:
    with _lock:
        requests = dict(_requests)
        durations = {key: (value[0], value[1], list(value[2])) for key, value in _durations.items()}
        counters = dict(_counters)
        gauges = dict(_gauges)
    out.metric("reelforge_http_requests_total", "counter", "HTTP requests served by this API process",
               [({"method": m, "route": r, "status": s}, v) for (m, r, s), v in sorted(requests.items())])
    out.metric("reelforge_http_errors_total", "counter", "HTTP responses with status 500 or above",
               [({"method": m, "route": r}, v) for (m, r, s), v in sorted(requests.items()) if s.startswith("5")])
    samples = []
    for (method, route), (total, count, buckets) in sorted(durations.items()):
        for bound, value in zip(BUCKETS, buckets):
            samples.append(({"method": method, "route": route, "le": f"{bound:g}"}, value))
        samples.append(({"method": method, "route": route, "le": "+Inf"}, count))
    out.metric("reelforge_http_request_duration_seconds_bucket", "counter", "Request durations (histogram buckets)",
               samples)
    out.metric("reelforge_http_request_duration_seconds_sum", "counter", "Total request time",
               [({"method": m, "route": r}, d[0]) for (m, r), d in sorted(durations.items())])
    out.metric("reelforge_http_request_duration_seconds_count", "counter", "Requests timed",
               [({"method": m, "route": r}, d[1]) for (m, r), d in sorted(durations.items())])
    by_name: dict[str, list] = defaultdict(list)
    for (name, labels), value in counters.items():
        by_name[name].append((dict(labels), value))
    for name, samples in sorted(by_name.items()):
        out.metric(f"reelforge_{name}_total", "counter", f"{name.replace('_', ' ')} counted by this API process",
                   samples)
    for name, value in sorted(gauges.items()):
        out.metric(f"reelforge_{name}", "gauge", name.replace("_", " "), [({}, value)])


def _database(out: _Out, db, now: datetime) -> None:
    from app import heartbeat, storage
    from app.models import (AuditEvent, BackupRun, EmailOutbox, PaymentOrder, SystemAlert, WorkflowJob, WorkflowRun,
                            WorkflowRunStep)
    from app.publications import Publication

    day = now - timedelta(hours=24)
    out.metric("reelforge_up", "gauge", "The API answered this scrape", [({}, 1)])
    jobs = db.execute(select(WorkflowJob.state, func.count()).group_by(WorkflowJob.state)).all()
    out.metric("reelforge_jobs", "gauge", "Workflow jobs by state", [({"state": state}, n) for state, n in jobs])
    failed = db.execute(select(WorkflowRunStep.node_type, func.count()).select_from(WorkflowJob)
                        .join(WorkflowRunStep, WorkflowRunStep.id == WorkflowJob.step_id)
                        .where(WorkflowJob.state == "failed", WorkflowJob.updated_at >= day)
                        .group_by(WorkflowRunStep.node_type)).all()
    out.metric("reelforge_job_failures_24h", "gauge", "Jobs that failed in the last 24 hours, by step type",
               [({"node_type": kind}, n) for kind, n in failed])
    for row in heartbeat.worker_health(db, now):
        age = row.get("seconds_ago")
        out.metric("reelforge_worker_heartbeat_age_seconds", "gauge", "Seconds since each worker reported",
                   [({"worker": row["worker"], "status": row["status"]}, age if age is not None else -1)])
    runs = db.execute(select(WorkflowRun.status, func.count()).group_by(WorkflowRun.status)).all()
    out.metric("reelforge_workflow_runs", "gauge", "Workflow runs by status", [({"status": s}, n) for s, n in runs])
    finished = db.execute(select(WorkflowRun.created_at, WorkflowRun.finished_at)
                          .where(WorkflowRun.finished_at >= day, WorkflowRun.status == "completed")).all()
    seconds = [(_aware(end) - _aware(start)).total_seconds() for start, end in finished if start and end]
    out.metric("reelforge_workflow_duration_seconds_24h_count", "gauge", "Runs completed in the last 24 hours",
               [({}, len(seconds))])
    out.metric("reelforge_workflow_duration_seconds_24h_sum", "gauge", "Their total duration",
               [({}, sum(seconds))])
    out.metric("reelforge_workflow_duration_seconds_24h_max", "gauge", "The longest of them",
               [({}, max(seconds) if seconds else 0)])
    publications = db.execute(select(Publication.channel, Publication.state, func.count())
                              .group_by(Publication.channel, Publication.state)).all()
    out.metric("reelforge_publications", "gauge", "Publications by channel and state",
               [({"channel": c, "state": s}, n) for c, s, n in publications])
    publish_failed = db.execute(select(Publication.channel, func.count())
                                .where(Publication.state.in_(("failed", "needs_attention")),
                                       Publication.updated_at >= day).group_by(Publication.channel)).all()
    out.metric("reelforge_publication_failures_24h", "gauge", "Publications that failed in the last 24 hours",
               [({"channel": c}, n) for c, n in publish_failed])
    orders = db.execute(select(PaymentOrder.provider, PaymentOrder.status, func.count())
                        .where(PaymentOrder.status.in_(("pending", "awaiting_confirmation", "paid_unapplied")))
                        .group_by(PaymentOrder.provider, PaymentOrder.status)).all()
    out.metric("reelforge_payments_waiting", "gauge", "Payment orders waiting for money, an admin or a review",
               [({"provider": p, "status": s}, n) for p, s, n in orders])
    failed_orders = db.execute(select(PaymentOrder.provider, func.count())
                               .where(PaymentOrder.status.in_(("failed", "rejected")), PaymentOrder.created_at >= day)
                               .group_by(PaymentOrder.provider)).all()
    out.metric("reelforge_payment_failures_24h", "gauge", "Payment orders failed or rejected in the last 24 hours",
               [({"provider": p}, n) for p, n in failed_orders])
    outbox = db.execute(select(EmailOutbox.status, func.count()).group_by(EmailOutbox.status)).all()
    out.metric("reelforge_email_outbox", "gauge", "Transactional emails by status",
               [({"status": s}, n) for s, n in outbox])
    stored = db.scalar(select(func.coalesce(func.sum(_asset_bytes()), 0)))
    out.metric("reelforge_media_stored_bytes", "gauge", "Bytes of media files stored", [({}, stored or 0)])
    disk = storage.disk_usage(db)
    if disk:
        out.metric("reelforge_media_disk_free_bytes", "gauge", "Free bytes on the media disk", [({}, disk["free_bytes"])])
        out.metric("reelforge_media_disk_used_ratio", "gauge", "Used share of the media disk",
                   [({}, disk["used_bytes"] / disk["total_bytes"] if disk["total_bytes"] else 0)])
    backup = db.scalar(select(func.max(BackupRun.finished_at)).where(BackupRun.status == "succeeded"))
    out.metric("reelforge_backup_age_seconds", "gauge", "Seconds since the last successful database backup (-1: none)",
               [({}, (now - _aware(backup)).total_seconds() if backup else -1)])
    alerts = db.execute(select(SystemAlert.level, func.count()).where(SystemAlert.active.is_(True))
                        .group_by(SystemAlert.level)).all()
    out.metric("reelforge_alerts_active", "gauge", "Active system alerts by level", [({"level": l}, n) for l, n in alerts])
    login_failures = db.scalar(select(func.count()).select_from(AuditEvent)
                               .where(AuditEvent.action == "auth.login", AuditEvent.outcome != "success",
                                      AuditEvent.created_at >= day))
    out.metric("reelforge_login_failures_24h", "gauge", "Failed or refused sign-ins in the last 24 hours",
               [({}, login_failures or 0)])


def _asset_bytes():
    from app.models import Asset

    return Asset.bytes


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def render(db=None) -> str:
    out = _Out()
    _process(out)
    if db is not None:
        try:
            _database(out, db, datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 - an unreachable database is itself the signal
            out.metric("reelforge_up", "gauge", "The API answered this scrape", [({}, 0)])
    return "\n".join(out.lines) + "\n"
