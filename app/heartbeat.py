"""Worker heartbeats: each worker process reports in, and admins see which ones are alive.

Every worker's main loop calls ``beat(name)``; at most once per
``INTERVAL_SECONDS`` it upserts one ``worker_heartbeats`` row per worker kind
(the latest process of that kind wins). ``worker_health`` classifies them:

* ``ok``: seen within ``STALE_SECONDS``;
* ``error``: seen recently, but its last pass reported an error (the scheduler);
* ``stale``: seen before, but not recently (stopped, crashed or stuck);
* ``missing``: never seen on this database.

This is deliberately small: no metrics, no history, no alerting.
"""
from datetime import datetime, timezone
import logging
import os
import socket
import time

from app.models import WorkerHeartbeat

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 15
STALE_SECONDS = 120
# Every long-running worker process; the API is not one of them.
WORKERS = ("text_worker", "image_worker", "video_worker", "voice_worker", "render_worker", "source_worker",
           "youtube_worker", "social_worker", "scheduler_worker", "movie_worker")
# Workers only an optional feature needs, with the setting that turns it on: while it is off, the worker is
# neither expected (never "missing") nor watched (a stopped one is not "stale").
OPTIONAL_WORKERS = {"movie_worker": "movie_sources.enabled"}
_last: dict[str, float] = {}
_started = datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def beat(worker: str, *, status: str = "running", detail: str | None = None, force: bool = False,
         session_factory=None) -> bool:
    """Record that ``worker`` is alive; never raises (a heartbeat must not stop a worker)."""
    now = time.monotonic()
    if not force and now - _last.get(worker, -INTERVAL_SECONDS) < INTERVAL_SECONDS:
        return False
    _last[worker] = now
    try:
        if session_factory is None:
            from app.db import Session as session_factory
        with session_factory.begin() as db:
            row = db.get(WorkerHeartbeat, worker)
            stamp = datetime.now(timezone.utc)
            if row is None:
                row = WorkerHeartbeat(worker=worker, started_at=_started)
                db.add(row)
            if row.pid != os.getpid() or row.started_at is None:
                row.started_at = _started  # another process took over this worker's name
            row.host, row.pid, row.status = socket.gethostname()[:255], os.getpid(), status[:24]
            row.detail, row.last_seen_at = (detail or None) and detail[:255], stamp
        return True
    except Exception:  # noqa: BLE001 - reported, never fatal
        logger.warning("worker heartbeat failed", exc_info=True)
        return False


def expected(name: str) -> bool:
    """Whether this server needs the worker: always, or while its optional feature is on."""
    setting = OPTIONAL_WORKERS.get(name)
    if setting is None:
        return True
    from app import system_config

    return bool(system_config.get(setting))


def worker_health(db, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = {row.worker: row for row in db.query(WorkerHeartbeat).all()}
    health = []
    for name in (*WORKERS, *sorted(set(rows) - set(WORKERS))):
        row = rows.get(name)
        if not expected(name) and (row is None or (now - _utc(row.last_seen_at)).total_seconds() > STALE_SECONDS):
            continue
        if row is None:
            health.append({"worker": name, "status": "missing", "last_seen_at": None, "host": None, "pid": None,
                           "detail": None})
            continue
        age = (now - _utc(row.last_seen_at)).total_seconds()
        state = "stale" if age > STALE_SECONDS else "error" if row.status == "error" else "ok"
        health.append({"worker": name, "status": state,
                       "last_seen_at": _utc(row.last_seen_at).isoformat(), "seconds_ago": max(0, round(age)),
                       "host": row.host, "pid": row.pid, "detail": row.detail})
    return health
