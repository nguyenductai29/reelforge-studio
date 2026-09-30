"""Run video jobs outside request transactions and save private MP4 assets."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
import uuid

from sqlalchemy import func, select, update

from app.db import Session
from app import jobs, usage
from app.logs import log_event, payload_summary
from app.main import MAX_UPLOAD, media_root, workspace_media_quota
from app.models import Asset, CreditReconciliation, UsageEvent, WorkflowJob, WorkflowRun, WorkflowRunStep, Workspace
from app.providers.catalog import PROVIDER_ERRORS, VIDEO_PROVIDERS, dola_max_job_age_seconds, video_provider_config_issue
from app.providers.errors import error_category
from app.provider_progress import provider_progress, safe_error_code, step_output
from app.runtime_env import start_process
from app.video_files import download_video, valid_mp4
from app.workflow import ExecutionContext, NodeError, NodeExecutionResult, default_executor

# Submit errors that prove the provider did not accept the job, so its credits can be refunded.
DEFINITIVE_SUBMIT_REJECTIONS = frozenset({"invalid_request", "authentication_error", "billing_error",
                                          "not_found", "rate_limited"})

logger = logging.getLogger(__name__)


def _job_fields(job_id, payload, *, run_id=None, step_id=None, workspace_id=None) -> dict:
    return {"job_id": job_id, "workspace_id": workspace_id, "run_id": run_id, "step_id": step_id,
            "provider": payload.get("provider"), "model": payload.get("model_id")}


def _provider_failed(fields, operation, exc, started):
    error = exc.describe() if hasattr(exc, "describe") else {"code": "worker_error", "type": type(exc).__name__}
    log_event(logger, "provider_request_failed", level=logging.WARNING, **fields, operation=operation,
              latency_ms=round((time.monotonic() - started) * 1000), error=error)


def _now():
    return datetime.now(timezone.utc)


def video_job_max_age_seconds() -> int:
    """Bound polling and credit holds when a provider never reaches a final state."""
    try:
        seconds = int(os.environ.get("VIDEO_JOB_MAX_AGE_SECONDS", "21600"))
    except ValueError as exc:
        raise RuntimeError("VIDEO_JOB_MAX_AGE_SECONDS must be an integer") from exc
    if not 60 <= seconds <= 86400:
        raise RuntimeError("VIDEO_JOB_MAX_AGE_SECONDS must be between 60 and 86400")
    return seconds


def _live_lease(db, job_id, token):
    job = db.get(WorkflowJob, job_id)
    if not job or job.state != "leased" or job.lease_token != token or not job.lease_expires_at:
        return None
    if db.get(CreditReconciliation, job.step_id) is not None:
        return None
    step = db.get(WorkflowRunStep, job.step_id)
    if step.status not in {"queued", "submitting", "running"}:
        return None
    expires = job.lease_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return job if expires > _now() else None


def _output(step):
    return step_output(step)


def _save_output(step, value):
    step.output = json.dumps(value, separators=(",", ":"))


def _download_video(url: str, target: Path) -> int:
    return download_video(url, target, max_bytes=MAX_UPLOAD)


def _valid_mp4(path: Path) -> bool:
    return valid_mp4(path, max_bytes=MAX_UPLOAD)


def _requeue(job_id: str, token: str, *, delay: int, detail: str, error_count: int | None = None):
    with Session.begin() as db:
        job = _live_lease(db, job_id, token)
        if not job:
            return
        step = db.get(WorkflowRunStep, job.step_id)
        step.detail = detail
        if error_count is not None:
            output = _output(step)
            output["error_count"] = error_count
            _save_output(step, output)
        jobs.fail_job(db, job_id=job_id, lease_token=token, error=detail, retry_delay_seconds=delay)


def _terminal_failure(job_id: str, token: str, detail: str, *, refund: bool, code: str | None = None):
    code = safe_error_code(code, fallback="worker_error" if refund else "provider_failed")
    with Session.begin() as db:
        job = _live_lease(db, job_id, token)
        if not job or not jobs.fail_job(db, job_id=job_id, lease_token=token, error=detail):
            return
        step = db.get(WorkflowRunStep, job.step_id)
        # Reconciliation and sibling workers always lock run before credit account.
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == job.run_id).with_for_update())
        status = "failed" if refund else "needs_attention"
        detail = (detail if refund else
                       f"{detail} Cần đối soát với provider; credit đang được giữ, không tự gửi lại.")[:500]
        output = provider_progress(_output(step), stage=status)
        fields = _job_fields(job.id, job.payload, run_id=job.run_id, step_id=job.step_id, workspace_id=job.workspace_id)
        log_event(logger, "job_failed", level=logging.WARNING, **fields, status=status, refund=refund,
                  error_code=code, category=error_category(code) if code else None)
        if refund:
            usage.post_credit(db, job.workspace_id, job.payload["credits"], "video_refund", f"refund:{job.run_id}")
            log_event(logger, "credit_refunded", **fields, credits=job.payload["credits"], reference=f"refund:{job.run_id}")
        default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step,
                                     NodeExecutionResult(status, detail, output,
                                                         error=NodeError(code, detail, False, error_category(code))))


def _record_progress(job_id, token, **facts):
    with Session.begin() as db:
        job = _live_lease(db, job_id, token)
        if not job:
            return False
        step = db.get(WorkflowRunStep, job.step_id)
        if step.status not in {"submitting", "running"}:
            return False
        _save_output(step, provider_progress(_output(step), **facts))
        return True


def _store_result(job_id: str, token: str, result, download):
    asset_id = str(uuid.uuid4())
    with Session() as db:
        job = _live_lease(db, job_id, token)
        if not job:
            return
        VIDEO_PROVIDERS[job.payload["provider"]].module.validate_media_url(result.video_url)
        root = media_root(db)
        target = root / job.workspace_id / asset_id
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(asset_id + ".part")
    started = time.monotonic()
    try:
        download(result.video_url, partial)
        size = partial.stat().st_size
        if not 0 < size <= MAX_UPLOAD or not _valid_mp4(partial):
            raise ValueError("Provider result is not a valid MP4 or is too large")
        partial.replace(target)
        with Session.begin() as db:
            job = _live_lease(db, job_id, token)
            if not job or not jobs.complete_job(db, job_id=job_id, lease_token=token):
                target.unlink(missing_ok=True)
                return
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == job.run_id).with_for_update())
            db.execute(update(Workspace).where(Workspace.id == job.workspace_id).values(name=Workspace.name))
            stored_bytes = db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.workspace_id == job.workspace_id))
            if stored_bytes + size > workspace_media_quota():
                raise ValueError("Workspace media quota reached")
            step = db.get(WorkflowRunStep, job.step_id)
            payload = job.payload
            db.add(Asset(id=asset_id, workspace_id=job.workspace_id, project_id=run.project_id,
                         run_id=run.id, step_id=step.id, provider=payload["provider"], model=payload["model_id"],
                         filename=f"video-{asset_id[:8]}.mp4", content_type="video/mp4", bytes=size))
            output = _output(step)
            provider_progress(output, stage="completed", status="completed")
            output.pop("submission", None)
            output.pop("error_count", None)
            output["asset_id"] = asset_id
            output["filename"] = f"video-{asset_id[:8]}.mp4"
            # The standard key the video's output port reads (app/workflow/ports.py).
            output["video_assets"] = [{"id": asset_id, "filename": output["filename"], "content_type": "video/mp4"}]
            if not db.scalar(select(UsageEvent.id).where(UsageEvent.reference == f"video:{step.id}")):
                db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=job.workspace_id,
                                  tool=f"{payload['provider']}/video", units=1, credits=payload["credits"],
                                  reference=f"video:{step.id}", created_at=_now()))
            db.flush()
            log_event(logger, "job_completed", **_job_fields(job_id, payload, run_id=run.id, step_id=step.id,
                                                             workspace_id=job.workspace_id),
                      asset_id=asset_id, bytes=size, download_ms=round((time.monotonic() - started) * 1000),
                      credits_charged=payload["credits"])
            # Records the step and lets its children (e.g. review) run.
            default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step,
                                         NodeExecutionResult.completed("Đã lưu video vào kho media riêng.", output,
                                                                       asset_ids=(asset_id,)))
    except Exception:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def run_one(*, client=None, download=None, poll_seconds: int = 10, worker_id: str | None = None) -> bool:
    """Claim and advance one job; provider calls never hold a database transaction."""
    max_age_seconds = video_job_max_age_seconds()
    worker_id = worker_id or f"video-{os.getpid()}"
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=300,
                                      logical_key_prefix="video:")
        if not claimed:
            return False
        job = claimed[0]
        job_id, token, payload, job_created_at = job.id, job.lease_token, job.payload, job.created_at
        fields = _job_fields(job_id, payload, run_id=job.run_id, step_id=job.step_id, workspace_id=job.workspace_id)
        step = db.get(WorkflowRunStep, job.step_id)
        action = step.status
        log_event(logger, "job_claimed", **fields, worker_id=worker_id, step_status=action,
                  attempt=job.attempt_count)
        if action not in {"queued", "submitting", "running"} or db.get(CreditReconciliation, step.id) is not None:
            jobs.fail_job(db, job_id=job_id, lease_token=token, error="invalid_step_state")
            return True
        storage_full = False
        if action == "queued":
            stored_bytes = db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(
                Asset.workspace_id == job.workspace_id))
            storage_full = stored_bytes >= workspace_media_quota()
            step.status, step.detail = "submitting", "Đang gửi yêu cầu tạo video."
            _save_output(step, provider_progress(_output(step), stage="preflight"))
        current_output = _output(step)
    if storage_full:
        _terminal_failure(job_id, token, "Kho media của workspace đã đầy; đã hoàn credits.",
                          refund=True, code="storage_limit_exceeded")
        return True
    provider = VIDEO_PROVIDERS.get(payload.get("provider"))
    if not provider:
        _terminal_failure(job_id, token, "Provider video không được hỗ trợ.", refund=action == "queued",
                          code="unsupported_provider")
        return True
    provider_module, provider_client_type, key_name = provider.module, provider.client_type, provider.key_env
    created_at = job_created_at if job_created_at.tzinfo else job_created_at.replace(tzinfo=timezone.utc)
    if (_now() - created_at).total_seconds() > max_age_seconds:
        _terminal_failure(job_id, token, "Video quá thời gian chờ; kiểm tra provider trước khi tạo lại.",
                          refund=action == "queued", code="job_expired")
        return True
    if payload["provider"] == "dola":
        issue = video_provider_config_issue("dola")
        if issue:
            _terminal_failure(job_id, token, issue[1], refund=action == "queued")
            return True
        if (_now() - created_at).total_seconds() > dola_max_job_age_seconds():
            _terminal_failure(job_id, token, "Dola quá thời gian chờ; cần kiểm tra gateway trước khi tạo lại.", refund=action == "queued")
            return True
    owned_client = client is None
    if owned_client:
        key = os.environ.get(key_name)
        if not key:
            _terminal_failure(job_id, token, f"Server chưa cấu hình {key_name}.", refund=action == "queued")
            return True
        try:
            client = provider_client_type(key)
        except PROVIDER_ERRORS:
            _terminal_failure(job_id, token, "Cấu hình provider video không hợp lệ.", refund=action == "queued")
            return True
    started = time.monotonic()
    try:
        if action == "queued":
            if not _record_progress(job_id, token, stage="submitting", started=True):
                return True
            log_event(logger, "provider_request_started", **fields, operation="submit", request=payload_summary(payload))
            try:
                submission = client.submit(provider_module.VideoRequest(model_id=payload["model_id"], prompt=payload["prompt"],
                    aspect_ratio=payload["aspect_ratio"], duration=payload["duration"],
                    resolution=payload["resolution"], generate_audio=payload["generate_audio"]))
            except Exception as exc:
                _provider_failed(fields, "submit", exc, started)
                # Only a known pre-submit rejection can release the reservation.
                rejected = isinstance(exc, PROVIDER_ERRORS) and exc.code in DEFINITIVE_SUBMIT_REJECTIONS
                code = safe_error_code(getattr(exc, "code", None), fallback="submission_unknown")
                detail = ("Provider từ chối yêu cầu tạo video; đã hoàn credits." if rejected else
                          "Không rõ provider đã nhận yêu cầu tạo video.")
                _terminal_failure(job_id, token, detail, refund=rejected, code=code)
                return True
            log_event(logger, "provider_request_completed", **fields, operation="submit",
                      provider_job_id=getattr(submission, "request_id", None),
                      latency_ms=round((time.monotonic() - started) * 1000))
            with Session.begin() as db:
                job = _live_lease(db, job_id, token)
                if job:
                    step = db.get(WorkflowRunStep, job.step_id)
                    output = _output(step)
                    output["submission"] = asdict(submission)
                    provider_progress(output, stage="submitted", accepted=True, status="queued",
                                      request_id=getattr(submission, "request_id", None))
                    _save_output(step, output)
                    step.status, step.detail = "running", "Provider đang tạo video."
                    jobs.fail_job(db, job_id=job_id, lease_token=token, error="poll_pending", retry_delay_seconds=poll_seconds)
            return True
        if action == "submitting":
            _terminal_failure(job_id, token, "Lần gửi trước chưa có mã yêu cầu; có thể provider đã nhận.",
                              refund=False, code="submission_unknown")
            return True
        if action != "running" or "submission" not in current_output:
            _terminal_failure(job_id, token, "Trạng thái job video không hợp lệ.", refund=False)
            return True
        try:
            submission = provider_module.Submission(**current_output["submission"])
            if not _record_progress(job_id, token, stage="polling", polled=True):
                return True
            state = client.status(submission)
            if not _record_progress(job_id, token, stage="polling", status=state.state):
                return True
            failure = state.error if state.state == "failed" else None
            log_event(logger, "provider_request_completed", **fields, operation="status", state=state.state,
                      provider_job_id=getattr(submission, "request_id", None),
                      latency_ms=round((time.monotonic() - started) * 1000),
                      **({"provider_error": failure.code, "provider_detail": (failure.message or "")[:200]}
                         if failure else {}))
            if state.state in {"queued", "running"}:
                _requeue(job_id, token, delay=poll_seconds, detail="Provider đang tạo video.", error_count=0)
            elif state.state in {"failed", "cancelled"}:
                code = safe_error_code(state.error.code if state.error else "provider_failed")
                _terminal_failure(job_id, token, "Provider báo video thất bại; cần kiểm tra phí xử lý.",
                                  refund=False, code=code)
            elif state.state == "completed":
                _store_result(job_id, token, client.result(submission), download or _download_video)
            else:
                raise ValueError("Unknown provider state")
        except Exception as exc:
            _provider_failed(fields, "status_or_result", exc, started)
            errors = int(current_output.get("error_count", 0)) + 1
            if errors >= 3 or isinstance(exc, ValueError) or (isinstance(exc, PROVIDER_ERRORS) and not exc.retryable):
                _terminal_failure(job_id, token, f"Không lấy được video: {type(exc).__name__}", refund=False,
                                  code=getattr(exc, "code", None) or type(exc).__name__)
            else:
                _requeue(job_id, token, delay=max(poll_seconds, 2), detail="Tạm gián đoạn kết nối provider; sẽ thử lại.", error_count=errors)
        return True
    finally:
        if owned_client:
            client.close()


def main():
    parser = argparse.ArgumentParser(description="Process ReelForge video jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    args = parser.parse_args()
    start_process("video_worker")
    while True:
        worked = run_one()
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
