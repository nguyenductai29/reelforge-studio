"""Transactional durable queue for workflow run steps.

A lease that expires (its worker crashed or hung) makes the job claimable again;
each such reclaim is logged as ``job_lease_reclaimed`` with the previous worker,
so stuck work and its recovery can be audited (see ``stuck_jobs``).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.logs import log_event
from app.models import WorkflowJob, WorkflowRun, WorkflowRunStep

logger = logging.getLogger(__name__)


def enqueue_job(
    db: Session,
    *,
    workspace_id: str,
    run_id: str,
    step_id: str,
    logical_key: str,
    payload: Mapping[str, Any],
    available_at: datetime | None = None,
) -> WorkflowJob:
    """Enqueue once per logical key; the caller commits the transaction."""
    if not logical_key or len(logical_key) > 255:
        raise ValueError("logical_key must contain 1 to 255 characters")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a JSON object")
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    belongs = db.scalar(
        select(WorkflowRunStep.id)
        .join(WorkflowRun, WorkflowRunStep.run_id == WorkflowRun.id)
        .where(WorkflowRunStep.id == step_id, WorkflowRun.id == run_id, WorkflowRun.workspace_id == workspace_id)
    )
    if belongs is None:
        raise ValueError("step does not belong to the run and workspace")

    now = datetime.now(timezone.utc)
    values = dict(
        id=str(uuid4()), workspace_id=workspace_id, run_id=run_id, step_id=step_id,
        logical_key=logical_key, payload_json=payload_json, state="queued",
        available_at=available_at or now, attempt_count=0, created_at=now, updated_at=now,
    )
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(WorkflowJob).values(**values).on_conflict_do_nothing(index_elements=["logical_key"])
    elif dialect == "sqlite":
        statement = sqlite_insert(WorkflowJob).values(**values).on_conflict_do_nothing(index_elements=["logical_key"])
    else:
        raise NotImplementedError(f"Queue enqueue is unsupported on {dialect}")
    db.execute(statement)
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.logical_key == logical_key))
    if (job.workspace_id, job.run_id, job.step_id, job.payload_json) != (workspace_id, run_id, step_id, payload_json):
        raise ValueError("logical_key already belongs to different job input")
    return job


def _due_condition(now: datetime, logical_key_prefix: str | None = None):
    condition = or_(
        and_(WorkflowJob.state == "queued", WorkflowJob.available_at <= now),
        and_(WorkflowJob.state == "leased", WorkflowJob.lease_expires_at <= now),
    )
    return and_(condition, WorkflowJob.logical_key.startswith(logical_key_prefix, autoescape=True)) if logical_key_prefix else condition


def claim_due_jobs(
    db: Session,
    *,
    worker_id: str,
    limit: int = 1,
    lease_seconds: int = 60,
    now: datetime | None = None,
    logical_key_prefix: str | None = None,
) -> list[WorkflowJob]:
    """Claim due work in the caller's transaction; expired leases can be reclaimed."""
    if not worker_id or len(worker_id) > 128:
        raise ValueError("worker_id must contain 1 to 128 characters")
    if limit < 0 or lease_seconds <= 0:
        raise ValueError("limit must be nonnegative and lease_seconds must be positive")
    if limit == 0:
        return []
    now = now or datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_seconds)
    due = _due_condition(now, logical_key_prefix)
    order = (WorkflowJob.available_at, WorkflowJob.created_at, WorkflowJob.id)

    if db.get_bind().dialect.name == "postgresql":
        jobs = db.scalars(
            select(WorkflowJob).where(due).order_by(*order).limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            if job.state == "leased":
                _log_reclaim(job, job.worker_id, now)
            job.state = "leased"
            job.attempt_count += 1
            job.worker_id = worker_id
            job.lease_token = str(uuid4())
            job.lease_expires_at = expires_at
            job.updated_at = now
        db.flush()
        return jobs

    if db.get_bind().dialect.name != "sqlite":
        raise NotImplementedError("Queue claims require PostgreSQL or SQLite")
    # Expired leases before claiming, only to log which claims are recoveries.
    expired_query = select(WorkflowJob.id, WorkflowJob.worker_id).where(
        WorkflowJob.state == "leased", WorkflowJob.lease_expires_at <= now)
    if logical_key_prefix:
        expired_query = expired_query.where(WorkflowJob.logical_key.startswith(logical_key_prefix, autoescape=True))
    expired = dict(db.execute(expired_query.limit(50)).all())
    jobs = []
    for _ in range(limit):
        candidate = select(WorkflowJob.id).where(due).order_by(*order).limit(1).scalar_subquery()
        claimed_id = db.execute(
            update(WorkflowJob)
            .where(WorkflowJob.id == candidate, due)
            .values(
                state="leased", attempt_count=WorkflowJob.attempt_count + 1,
                worker_id=worker_id, lease_token=str(uuid4()),
                lease_expires_at=expires_at, updated_at=now,
            )
            .returning(WorkflowJob.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed_id is None:
            break
        job = db.get(WorkflowJob, claimed_id)
        db.refresh(job)
        if claimed_id in expired:
            _log_reclaim(job, expired[claimed_id], now)
        jobs.append(job)
    return jobs


def _log_reclaim(job: WorkflowJob, previous_worker: str | None, now: datetime) -> None:
    log_event(logger, "job_lease_reclaimed", level=logging.WARNING, job_id=job.id, workspace_id=job.workspace_id,
              run_id=job.run_id, step_id=job.step_id, queue=job.logical_key.split(":", 1)[0],
              previous_worker=previous_worker, attempt=job.attempt_count, reclaimed_at=now.isoformat())


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def stuck_jobs(db: Session, *, now: datetime | None = None, overdue_minutes: int = 10,
               limit: int = 100) -> dict[str, list[dict[str, Any]]]:
    """An audit of work that is not moving, with safe fields only (never a payload).

    * ``expired_leases``: a worker claimed the job and stopped reporting; the next
      worker for that queue reclaims it (and logs ``job_lease_reclaimed``).
    * ``overdue``: queued for longer than ``overdue_minutes`` after it was due, which
      usually means no worker for that queue is running.
    * ``orphan_steps``: a step still queued or running although none of its jobs is.
    """
    now = now or datetime.now(timezone.utc)

    def row(job: WorkflowJob) -> dict[str, Any]:
        return {"id": job.id, "queue": job.logical_key.split(":", 1)[0], "state": job.state,
                "attempt_count": job.attempt_count, "worker_id": job.worker_id, "workspace_id": job.workspace_id,
                "run_id": job.run_id, "step_id": job.step_id,
                "available_at": _aware(job.available_at).isoformat() if job.available_at else None,
                "lease_expires_at": _aware(job.lease_expires_at).isoformat() if job.lease_expires_at else None}

    expired = db.scalars(select(WorkflowJob).where(WorkflowJob.state == "leased",
                                                   WorkflowJob.lease_expires_at <= now)
                         .order_by(WorkflowJob.lease_expires_at).limit(limit)).all()
    overdue = db.scalars(select(WorkflowJob).where(WorkflowJob.state == "queued",
                                                   WorkflowJob.available_at <= now - timedelta(minutes=overdue_minutes))
                         .order_by(WorkflowJob.available_at).limit(limit)).all()
    active = select(WorkflowJob.step_id).where(WorkflowJob.state.in_(("queued", "leased")))
    orphan_steps = db.execute(
        select(WorkflowRunStep.id, WorkflowRunStep.run_id, WorkflowRunStep.node_type, WorkflowRunStep.status)
        .join(WorkflowJob, WorkflowJob.step_id == WorkflowRunStep.id)
        .where(WorkflowRunStep.status.in_(("queued", "running")), WorkflowRunStep.id.notin_(active))
        .distinct().limit(limit)).all()
    return {"expired_leases": [row(job) for job in expired], "overdue": [row(job) for job in overdue],
            "orphan_steps": [{"step_id": step_id, "run_id": run_id, "node_type": node_type, "status": status}
                             for step_id, run_id, node_type, status in orphan_steps]}


def live_lease(db: Session, *, job_id: str, lease_token: str, now: datetime | None = None) -> WorkflowJob | None:
    """The job, if ``lease_token`` still holds an unexpired lease on it."""
    job = db.get(WorkflowJob, job_id)
    if job is None or job.state != "leased" or job.lease_token != lease_token or job.lease_expires_at is None:
        return None
    expires = job.lease_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return job if expires > (now or datetime.now(timezone.utc)) else None


def extend_lease(db: Session, *, job_id: str, lease_token: str, lease_seconds: int,
                 now: datetime | None = None) -> bool:
    """Keep a live lease alive during long local work (a movie download); False once another worker owns it."""
    now = now or datetime.now(timezone.utc)
    result = db.execute(
        update(WorkflowJob)
        .where(WorkflowJob.id == job_id, WorkflowJob.lease_token == lease_token,
               WorkflowJob.state == "leased", WorkflowJob.lease_expires_at > now)
        .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
        .execution_options(synchronize_session="fetch")
    )
    return bool(result.rowcount)


def complete_job(
    db: Session, *, job_id: str, lease_token: str, now: datetime | None = None,
) -> bool:
    """Complete only the current live lease, or confirm an earlier completion."""
    now = now or datetime.now(timezone.utc)
    result = db.execute(
        update(WorkflowJob)
        .where(WorkflowJob.id == job_id, WorkflowJob.lease_token == lease_token,
               WorkflowJob.state == "leased", WorkflowJob.lease_expires_at > now)
        .values(state="succeeded", lease_expires_at=None, finished_at=now, updated_at=now)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount:
        return True
    return db.scalar(
        select(WorkflowJob.id).where(WorkflowJob.id == job_id,
                                     WorkflowJob.lease_token == lease_token,
                                     WorkflowJob.state == "succeeded")
    ) is not None


def fail_job(
    db: Session,
    *,
    job_id: str,
    lease_token: str,
    error: str,
    retry_delay_seconds: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Requeue a transient failure or record a terminal failure once."""
    if retry_delay_seconds is not None and retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be nonnegative")
    now = now or datetime.now(timezone.utc)
    retry = retry_delay_seconds is not None
    result = db.execute(
        update(WorkflowJob)
        .where(WorkflowJob.id == job_id, WorkflowJob.lease_token == lease_token,
               WorkflowJob.state == "leased", WorkflowJob.lease_expires_at > now)
        .values(
            state="queued" if retry else "failed",
            available_at=now + timedelta(seconds=retry_delay_seconds) if retry else WorkflowJob.available_at,
            lease_expires_at=None, worker_id=None, last_error=error,
            finished_at=None if retry else now, updated_at=now,
        )
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount:
        return True
    expected_state = "queued" if retry else "failed"
    return db.scalar(
        select(WorkflowJob.id).where(WorkflowJob.id == job_id,
                                     WorkflowJob.lease_token == lease_token,
                                     WorkflowJob.state == expected_state)
    ) is not None
