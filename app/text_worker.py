"""Run text generation jobs outside request transactions.

Run ``python -m app.text_worker`` next to the API. Each job is one provider
call. A generation has no side effect besides its cost, so a transient failure,
or a worker that died mid-call, is retried up to ``MAX_ATTEMPTS`` times. When
no text can be produced, the step fails and its reserved credits are refunded.
"""
import argparse
from datetime import datetime, timezone
import logging
import os
import time
import uuid

from sqlalchemy import select

from app import jobs, usage
from app.logs import log_event, payload_summary
from app.providers.errors import ProviderError
from app.models import UsageEvent, WorkflowRun, WorkflowRunStep
from app.providers.text import TextProviderError, create_text_provider
from app.runtime_env import start_process
from app.workflow import ExecutionContext, NodeError, NodeExecutionResult, default_executor, default_registry
from app.workflow.nodes import TextNodeHandler
from app.workflow.results import COMPLETED, QUEUED, RUNNING

logger = logging.getLogger(__name__)

LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
RUNNING_DETAIL = "Đang tạo nội dung."
RETRY_DETAIL = "Tạm gián đoạn kết nối provider; sẽ thử lại."
COMPLETED_DETAIL = "Đã tạo nội dung."
REJECTED_DETAIL = "Provider từ chối yêu cầu tạo nội dung; đã hoàn credits."
FAILED_DETAIL = "Không tạo được nội dung; đã hoàn credits."


def _now():
    return datetime.now(timezone.utc)


def _default_session_factory():
    # Imported here so tests can pass their own factory without reading instance/bootstrap.json.
    from app.db import Session
    return Session


def _job_fields(job) -> dict:
    return {"job_id": job.id, "workspace_id": job.workspace_id, "run_id": job.run_id, "step_id": job.step_id,
            "provider": job.payload.get("provider"), "model": job.payload.get("model")}


def _requeue(Session, job_id, token, *, delay, code):
    with Session.begin() as db:
        job = jobs.live_lease(db, job_id=job_id, lease_token=token)
        if not job:
            return
        step = db.get(WorkflowRunStep, job.step_id)
        step.status, step.detail = QUEUED, RETRY_DETAIL
        if jobs.fail_job(db, job_id=job_id, lease_token=token, error=code, retry_delay_seconds=delay):
            log_event(logger, "job_retry_scheduled", **_job_fields(job), attempt=job.attempt_count,
                      delay_seconds=delay, error_code=code)


def _finish(Session, job_id, token, result: NodeExecutionResult, *, units: int | None = None) -> None:
    """Close the job, settle credits, record the step and continue the run, in one transaction."""
    with Session.begin() as db:
        job = jobs.live_lease(db, job_id=job_id, lease_token=token)
        if not job:
            return
        succeeded = result.status == COMPLETED
        closed = (jobs.complete_job(db, job_id=job_id, lease_token=token) if succeeded else
                  jobs.fail_job(db, job_id=job_id, lease_token=token, error=result.error.code))
        if not closed:
            return
        payload, step_id = job.payload, job.step_id
        fields = _job_fields(job)
        if succeeded:
            reference = f"text:{step_id}"
            if not db.scalar(select(UsageEvent.id).where(UsageEvent.reference == reference)):
                db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=job.workspace_id,
                                  tool=f"{payload['provider']}/text", units=max(1, units or 1),
                                  credits=payload["credits"], reference=reference, created_at=_now()))
            log_event(logger, "job_completed", **fields, credits_charged=payload["credits"], units=units)
        else:
            usage.post_credit(db, job.workspace_id, payload["credits"], "text_refund", f"text-refund:{step_id}")
            log_event(logger, "job_failed", level=logging.WARNING, **fields, error_code=result.error.code,
                      category=result.error.category)
            log_event(logger, "credit_refunded", **fields, credits=payload["credits"],
                      reference=f"text-refund:{step_id}")
        run = db.get(WorkflowRun, job.run_id)
        default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()),
                                     db.get(WorkflowRunStep, step_id), result)


def _failure(error: TextProviderError | Exception) -> NodeExecutionResult:
    if isinstance(error, TextProviderError):
        return NodeExecutionResult.failed(NodeError(error.code, str(error), error.retryable, error.category),
                                          detail=FAILED_DETAIL if error.retryable else REJECTED_DETAIL)
    return NodeExecutionResult.failed(NodeError("worker_error", type(error).__name__, True), detail=FAILED_DETAIL)


def run_one(*, provider_factory=create_text_provider, session_factory=None, worker_id: str | None = None,
            retry_seconds: int = 5) -> bool:
    """Claim and run one text job; returns False when none is due."""
    Session = session_factory or _default_session_factory()
    worker_id = worker_id or f"text-{os.getpid()}"
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=LEASE_SECONDS,
                                      logical_key_prefix="text:")
        if not claimed:
            return False
        job = claimed[0]
        job_id, token, payload, attempt = job.id, job.lease_token, job.payload, job.attempt_count
        fields = _job_fields(job)
        log_event(logger, "job_claimed", **fields, worker_id=worker_id, attempt=attempt,
                  node_type=payload.get("node_type"))
        step = db.get(WorkflowRunStep, job.step_id)
        if step.status not in (QUEUED, RUNNING):
            jobs.fail_job(db, job_id=job_id, lease_token=token, error="invalid_step_state")
            log_event(logger, "job_failed", level=logging.WARNING, **fields, error_code="invalid_step_state")
            return True
        step.status, step.detail = RUNNING, RUNNING_DETAIL

    if attempt > MAX_ATTEMPTS:
        _finish(Session, job_id, token, _failure(TextProviderError("attempts_exhausted", "Too many attempts")))
        return True
    handler = default_registry.resolve(payload.get("node_type"))
    request = payload_summary(payload)
    log_event(logger, "provider_request_started", **fields, request=request)
    started = time.monotonic()
    try:
        provider = provider_factory(payload["provider"])
        try:
            result = provider.generate(model=payload["model"], prompt=payload["prompt"],
                                       system_prompt=payload.get("system_prompt"),
                                       temperature=payload.get("temperature"), max_tokens=payload["max_tokens"],
                                       response_format=payload.get("response_format", "text"))
        finally:
            provider.close()
    except Exception as exc:
        retryable = exc.retryable if isinstance(exc, TextProviderError) else True
        latency_ms = round((time.monotonic() - started) * 1000)
        if isinstance(exc, ProviderError):
            log_event(logger, "provider_request_failed", level=logging.WARNING, **fields, attempt=attempt,
                      latency_ms=latency_ms, error=exc.describe())
        else:
            logger.exception("Text job %s failed unexpectedly", job_id)
            log_event(logger, "provider_request_failed", level=logging.WARNING, **fields, attempt=attempt,
                      latency_ms=latency_ms, error={"code": "worker_error", "type": type(exc).__name__})
        if retryable and attempt < MAX_ATTEMPTS:
            _requeue(Session, job_id, token, delay=retry_seconds * 2 ** (attempt - 1),
                     code=getattr(exc, "code", type(exc).__name__))
        else:
            _finish(Session, job_id, token, _failure(exc))
        return True

    log_event(logger, "provider_request_completed", **fields, response_model=result.model,
              latency_ms=round((time.monotonic() - started) * 1000), usage=result.usage.as_dict(),
              output_chars=len(result.text), finish_reason=result.raw_metadata.get("finish_reason"))
    output = (handler.output_from(payload, result) if isinstance(handler, TextNodeHandler) else
              {"text": result.text, "provider": result.provider, "model": result.model,
               "usage": result.usage.as_dict()})
    _finish(Session, job_id, token, NodeExecutionResult.completed(COMPLETED_DETAIL, output),
            units=result.usage.total_tokens)
    return True


def main():
    parser = argparse.ArgumentParser(description="Process ReelForge text generation jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    args = parser.parse_args()
    start_process("text_worker")
    while True:
        worked = run_one()
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
