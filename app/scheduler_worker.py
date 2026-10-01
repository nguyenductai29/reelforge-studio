"""Scheduler worker: queue the uploads of scheduled publications when their time comes.

Run ``python -m app.scheduler_worker`` next to the API (``--once`` makes one pass).
Every pass locks the due ``scheduled`` publications (``SKIP LOCKED`` on
PostgreSQL, so several schedulers can run), checks each one again under the lock
and queues its upload job in the same transaction (``publications.dispatch_due``).
A crash before the commit leaves the publication scheduled for the next pass; a
crash after it leaves a queued job that the channel's worker picks up. Nothing is
ever queued twice, and nothing is published without the explicit request that
scheduled it. Times are UTC.

Each pass also retries transactional email that is due (``app/mailer.py``) and, every few
minutes, evaluates the system alerts (``app/alerts.py``).
"""
import argparse
from datetime import datetime, timezone
import logging
import time

from app import alerts, heartbeat, mailer, publications
from app.logs import log_event
from app.runtime_env import start_process

logger = logging.getLogger(__name__)
POLL_SECONDS = 15
BATCH = 20


def _default_session_factory():
    from app.db import Session
    return Session


def run_once(*, session_factory=None, now: datetime | None = None) -> int:
    """One pass; returns how many publications were handled."""
    Session = session_factory or _default_session_factory()
    handled = 0
    while True:
        with Session.begin() as db:
            outcomes = publications.dispatch_due(db, now=now or datetime.now(timezone.utc), limit=BATCH)
        for publication_id, outcome in outcomes:
            log_event(logger, "publication_dispatched" if outcome == "queued" else "publication_dispatch_failed",
                      level=logging.INFO if outcome == "queued" else logging.WARNING,
                      publication_id=publication_id, outcome=outcome)
        handled += len(outcomes)
        if len(outcomes) < BATCH:
            return handled


def main():
    parser = argparse.ArgumentParser(description="Queue scheduled ReelForge publications when they are due")
    parser.add_argument("--once", action="store_true", help="Make one pass, then exit")
    args = parser.parse_args()
    start_process("scheduler_worker")
    while True:
        try:
            handled = run_once()
            mailer.deliver_pending()
            alerts.maybe_evaluate()
            heartbeat.beat("scheduler_worker", detail=f"dispatched {handled}" if handled else None)
        except Exception:  # noqa: BLE001 - a database outage must not stop the scheduler for good
            logger.exception("scheduler pass failed")
            heartbeat.beat("scheduler_worker", status="error", detail="pass failed", force=True)
        if args.once:
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
