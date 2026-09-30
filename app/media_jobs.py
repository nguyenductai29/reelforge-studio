"""Durable child jobs for image, video and voice steps: one paid provider operation per job.

A step that makes several files queues one job per operation (one image, or
one scene's image, clip or narration; see app/workflow/nodes/media.py), each
with its own credit reservation. A worker advances one job at a time through
submit → poll → download → validate → store, keeping that job's facts under
``step.output["jobs"][<job_id>]``. A provider that answers with the file itself
(``MediaKind.generate``, e.g. text-to-speech) skips polling and downloading:
generate → validate → store.

* ``status``: queued, submitting, running, then succeeded, failed or needs_attention;
* ``scene_index``, ``operation``, and the stored ``assets`` once it succeeded;
* private progress (``submission``, ``provider_job``, ``error_count``), never returned by the API.

When every job of the step has finished, the step settles:

* all succeeded → ``completed`` with every asset, ordered by scene, and downstream steps run;
* any uncertain (the provider may have charged) → ``needs_attention``; each such job is
  reconciled on its own (app/reconciliation.py);
* otherwise → ``failed``; each failed job was refunded when it failed.

Successful assets are kept in every case, and no operation is retried
automatically. A definite rejection before the provider accepted the request
refunds that job; any later or unclear failure holds its credits. Every change
to a step takes the run's lock first, so parallel workers cannot lose updates.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable
import uuid

from sqlalchemy import func, select, update

from app import jobs, usage
from app.db import Session
from app.logs import log_event, payload_summary
from app.main import media_root, workspace_media_quota
from app.models import Asset, CreditReconciliation, UsageEvent, WorkflowJob, WorkflowRun, WorkflowRunStep
from app.provider_progress import provider_progress, safe_error_code, step_output
from app.providers.errors import CATEGORIES, ProviderError, error_category
from app.workflow import ExecutionContext, NodeError, NodeExecutionResult, default_executor
from app.workflow.results import NEEDS_ATTENTION

logger = logging.getLogger(__name__)

CHILD_MODES = frozenset({"prompt", "scenes"})
ACTIVE = ("queued", "submitting", "running")
LEASE_SECONDS = 300
MAX_POLL_ERRORS = 3
# Submit errors that prove the provider did not accept the request, so its credits are refunded.
DEFINITIVE_SUBMIT_REJECTIONS = frozenset({"invalid_request", "authentication_error", "billing_error",
                                          "not_found", "rate_limited"})
PRIVATE_FIELDS = ("submission", "provider_job", "error_count")


@dataclass(frozen=True)
class MediaKind:
    """How one modality talks to its providers; the job lifecycle is shared."""

    name: str                      # "image" or "video": job key prefix, credit reasons, usage tool suffix
    output_key: str                # the step output list the output port reads
    running_detail: str
    completed_detail: str
    attention_detail: str
    failed_detail: str
    max_age_seconds: Callable[[dict], int]         # per payload: a provider may allow less time
    config_issue: Callable[[str], tuple[str, str] | None]
    open_client: Callable[[dict], Any]
    inspect: Callable[[Path, dict], tuple[str, str, dict]]  # content type, extension, extra entry fields
    # Asynchronous providers: submit, then poll and download each output URL.
    submit: Callable[[Any, dict], dict] | None = None             # JSON-safe submission with a "request_id"
    status: Callable[[Any, dict, dict], Any] | None = None        # (client, payload, submission) → .state and .error
    result_urls: Callable[[Any, dict, dict], list[str]] | None = None
    validate_url: Callable[[Any, dict, str], None] | None = None  # SSRF guard for each output URL
    download: Callable[[str, Path], int] | None = None
    # Synchronous providers: one call returns the files' bytes and the remote request ID, if any.
    generate: Callable[[Any, dict], tuple[list[bytes], str | None]] | None = None
    errors: tuple[type[BaseException], ...] = (ProviderError,)


@dataclass
class Claimed:
    job_id: str
    token: str
    payload: dict
    created_at: datetime
    action: str
    record: dict
    storage_full: bool
    fields: dict = field(default_factory=dict)


@dataclass
class _Live:
    job: WorkflowJob
    run: WorkflowRun
    step: WorkflowRunStep
    output: dict
    record: dict


def is_child(payload) -> bool:
    return isinstance(payload, dict) and payload.get("mode") in CHILD_MODES and isinstance(payload.get("operation"), str)


def records(output: dict) -> dict:
    value = output.get("jobs")
    if not isinstance(value, dict):
        value = output["jobs"] = {}
    return value


def _now():
    return datetime.now(timezone.utc)


def _lock_run(db, run_id) -> WorkflowRun:
    # A no-op UPDATE takes the write lock on SQLite too (FOR UPDATE is ignored there);
    # the executor, reconciliation and other workers lock the same run before the account.
    db.execute(update(WorkflowRun).where(WorkflowRun.id == run_id).values(status=WorkflowRun.status))
    return db.get(WorkflowRun, run_id)


def _save(step, output) -> None:
    step.output = json.dumps(output, separators=(",", ":"), ensure_ascii=False)


def _fields(job, run, payload) -> dict:
    return {"workspace_id": job.workspace_id, "workflow_id": run.workflow_id if run else None, "run_id": job.run_id,
            "step_id": job.step_id, "job_id": job.id, "scene_index": payload.get("scene_index"),
            "job_operation": payload.get("operation"), "provider": payload.get("provider"),
            "model": payload.get("model") or payload.get("model_id")}


def _live(db, job_id, token, *, lock=True) -> _Live | None:
    """The job with a live lease on an unfinished step, or ``None``."""
    job = db.get(WorkflowJob, job_id)
    if not job or job.state != "leased" or job.lease_token != token or not job.lease_expires_at:
        return None
    expires = job.lease_expires_at if job.lease_expires_at.tzinfo else job.lease_expires_at.replace(tzinfo=timezone.utc)
    if expires <= _now():
        return None
    run = _lock_run(db, job.run_id) if lock else db.get(WorkflowRun, job.run_id)
    if db.get(CreditReconciliation, job.id) is not None:
        return None
    step = db.get(WorkflowRunStep, job.step_id)
    if step.status not in ("queued", "running"):
        return None
    output = step_output(step)
    record = records(output).get(job.id)
    if not isinstance(record, dict) or record.get("status") not in ACTIVE:
        return None
    return _Live(job, run, step, output, record)


def begin(db, kind: MediaKind, job: WorkflowJob, worker_id: str) -> Claimed | None:
    """Claim-time bookkeeping, in the claim's transaction; ``None`` when the job must not run."""
    payload = job.payload
    run = _lock_run(db, job.run_id)
    step = db.get(WorkflowRunStep, job.step_id)
    fields = _fields(job, run, payload)
    log_event(logger, "job_claimed", **fields, worker_id=worker_id, attempt=job.attempt_count)
    output = step_output(step)
    record = records(output).setdefault(job.id, {"operation": payload.get("operation"),
                                                 "scene_index": payload.get("scene_index"),
                                                 "index": payload.get("index"), "status": "queued"})
    if (db.get(CreditReconciliation, job.id) is not None or step.status not in ("queued", "running")
            or record.get("status") not in ACTIVE):
        jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="invalid_step_state")
        log_event(logger, "job_failed", level=logging.WARNING, **fields, error_code="invalid_step_state")
        return None
    action = record["status"]
    storage_full = False
    if action == "queued":
        stored = db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.workspace_id == job.workspace_id))
        storage_full = stored >= workspace_media_quota()
        record["status"] = "submitting"
        provider_progress(record, stage="preflight")
        step.status, step.detail = "running", kind.running_detail
    _save(step, output)
    created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc)
    return Claimed(job.id, job.lease_token, payload, created, action, dict(record), storage_full, fields)


def run_one(kind: MediaKind, *, client=None, download=None, poll_seconds: int = 5, worker_id: str | None = None) -> bool:
    """Claim and advance one due job of this kind; provider calls never hold a transaction."""
    worker_id = worker_id or f"{kind.name}-{os.getpid()}"
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=LEASE_SECONDS,
                                      logical_key_prefix=f"{kind.name}:")
        if not claimed:
            return False
        job = claimed[0]
        if not is_child(job.payload):
            jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="unknown_job_shape")
            return True
        started = begin(db, kind, job, worker_id)
    if started is not None:
        advance(kind, started, client=client, download=download, poll_seconds=poll_seconds)
    return True


def advance(kind: MediaKind, claimed: Claimed, *, client=None, download=None, poll_seconds: int = 5) -> None:
    payload, action = claimed.payload, claimed.action
    if claimed.storage_full:
        _terminal(kind, claimed, "Kho media của workspace đã đầy; đã hoàn credits.", refund=True,
                  code="storage_limit_exceeded")
        return
    if (_now() - claimed.created_at).total_seconds() > kind.max_age_seconds(payload):
        _terminal(kind, claimed, "Quá thời gian chờ provider; kiểm tra trước khi tạo lại.",
                  refund=action == "queued", code="job_expired")
        return
    owned = client is None
    if owned:
        if issue := kind.config_issue(payload.get("provider") or ""):
            _terminal(kind, claimed, issue[1], refund=action == "queued", code=issue[0])
            return
        try:
            client = kind.open_client(payload)
        except kind.errors as exc:
            _terminal(kind, claimed, "Cấu hình provider không hợp lệ.", refund=action == "queued",
                      code=getattr(exc, "code", None) or "invalid_config")
            return
    started = time.monotonic()
    try:
        if action == "queued" and kind.generate is not None:
            _generate(kind, claimed, client, started)
        elif action == "queued":
            _submit(kind, claimed, client, started, poll_seconds)
        elif action == "submitting":
            _terminal(kind, claimed, "Lần gửi trước chưa có mã yêu cầu; có thể provider đã nhận.", refund=False,
                      code="submission_unknown")
        elif not isinstance(claimed.record.get("submission"), dict):
            _terminal(kind, claimed, "Trạng thái job không hợp lệ.", refund=False, code="worker_error")
        else:
            _poll(kind, claimed, client, started, poll_seconds, download or kind.download)
    finally:
        if owned:
            client.close()


def _provider_failed(fields, operation, exc, started):
    error = exc.describe() if hasattr(exc, "describe") else {"code": "worker_error", "type": type(exc).__name__}
    log_event(logger, "provider_request_failed", level=logging.WARNING, **fields, operation=operation,
              latency_ms=round((time.monotonic() - started) * 1000), error=error)


def _progress(claimed: Claimed, **facts) -> bool:
    with Session.begin() as db:
        live = _live(db, claimed.job_id, claimed.token)
        if not live or live.record.get("status") not in ("submitting", "running"):
            return False
        provider_progress(live.record, **facts)
        _save(live.step, live.output)
        return True


def _submit(kind, claimed, client, started, poll_seconds):
    fields, payload = claimed.fields, claimed.payload
    if not _progress(claimed, stage="submitting", started=True):
        return
    log_event(logger, "provider_request_started", **fields, operation="submit", request=payload_summary(payload))
    try:
        submission = kind.submit(client, payload)
    except Exception as exc:  # noqa: BLE001 - classified below
        _provider_failed(fields, "submit", exc, started)
        rejected = isinstance(exc, kind.errors) and getattr(exc, "code", None) in DEFINITIVE_SUBMIT_REJECTIONS
        code = safe_error_code(getattr(exc, "code", None), fallback="submission_unknown")
        detail = ("Provider từ chối yêu cầu; đã hoàn credits." if rejected else
                  "Không rõ provider đã nhận yêu cầu.")
        _terminal(kind, claimed, detail, refund=rejected, code=code, category=getattr(exc, "category", None))
        return
    log_event(logger, "provider_request_completed", **fields, operation="submit",
              provider_job_id=submission.get("request_id"), latency_ms=round((time.monotonic() - started) * 1000))
    with Session.begin() as db:
        live = _live(db, claimed.job_id, claimed.token)
        if live:
            live.record["submission"] = submission
            provider_progress(live.record, stage="submitted", accepted=True, status="queued",
                              request_id=submission.get("request_id"))
            live.record["status"] = "running"
            _save(live.step, live.output)
            jobs.fail_job(db, job_id=claimed.job_id, lease_token=claimed.token, error="poll_pending",
                          retry_delay_seconds=poll_seconds)


def _generate(kind, claimed, client, started):
    """One synchronous provider call; its files are stored at once, never requested twice."""
    fields, payload = claimed.fields, claimed.payload
    if not _progress(claimed, stage="submitting", started=True):
        return
    log_event(logger, "provider_request_started", **fields, operation="generate", request=payload_summary(payload))
    try:
        blobs, request_id = kind.generate(client, payload)
    except Exception as exc:  # noqa: BLE001 - classified below
        _provider_failed(fields, "generate", exc, started)
        rejected = isinstance(exc, kind.errors) and getattr(exc, "code", None) in DEFINITIVE_SUBMIT_REJECTIONS
        code = safe_error_code(getattr(exc, "code", None), fallback="submission_unknown")
        detail = ("Provider từ chối yêu cầu; đã hoàn credits." if rejected else
                  "Không rõ provider đã xử lý yêu cầu.")
        _terminal(kind, claimed, detail, refund=rejected, code=code, category=getattr(exc, "category", None))
        return
    log_event(logger, "provider_request_completed", **fields, operation="generate", provider_job_id=request_id,
              latency_ms=round((time.monotonic() - started) * 1000))
    if not _progress(claimed, stage="submitted", accepted=True, status="completed", request_id=request_id):
        return
    try:
        _store_files(kind, claimed, [lambda path, blob=blob: path.write_bytes(blob) for blob in blobs or []])
    except Exception as exc:  # noqa: BLE001 - the provider already answered, so its credits are held
        _provider_failed(fields, "store", exc, started)
        _terminal(kind, claimed, "Không lưu được kết quả từ provider.", refund=False,
                  code=getattr(exc, "code", None) or ("invalid_response" if isinstance(exc, ValueError) else "worker_error"))


def _poll(kind, claimed, client, started, poll_seconds, download):
    fields, submission = claimed.fields, claimed.record["submission"]
    try:
        if not _progress(claimed, stage="polling", polled=True):
            return
        state = kind.status(client, claimed.payload, submission)
        if not _progress(claimed, stage="polling", status=state.state):
            return
        failure = state.error if state.state == "failed" else None
        log_event(logger, "provider_request_completed", **fields, operation="status", state=state.state,
                  provider_job_id=submission.get("request_id"), latency_ms=round((time.monotonic() - started) * 1000),
                  **({"provider_error": failure.code, "provider_detail": (failure.message or "")[:200]}
                     if failure else {}))
        if state.state in ("queued", "running"):
            _requeue(claimed, delay=poll_seconds, error_count=0)
        elif state.state in ("failed", "cancelled"):
            _terminal(kind, claimed, "Provider báo thất bại; cần kiểm tra phí xử lý.", refund=False,
                      code=safe_error_code(failure.code if failure else "provider_failed"))
        elif state.state == "completed":
            _store(kind, claimed, client, kind.result_urls(client, claimed.payload, submission), download)
        else:
            raise ValueError("Unknown provider state")
    except Exception as exc:  # noqa: BLE001 - classified below
        _provider_failed(fields, "status_or_result", exc, started)
        errors = int(claimed.record.get("error_count", 0) or 0) + 1
        if errors >= MAX_POLL_ERRORS or isinstance(exc, ValueError) or (
                isinstance(exc, kind.errors) and not getattr(exc, "retryable", False)):
            _terminal(kind, claimed, "Không lấy được kết quả từ provider.", refund=False,
                      code=getattr(exc, "code", None) or type(exc).__name__)
        else:
            _requeue(claimed, delay=max(poll_seconds, 2), error_count=errors)


def _requeue(claimed, *, delay, error_count):
    with Session.begin() as db:
        live = _live(db, claimed.job_id, claimed.token)
        if not live:
            return
        live.record["error_count"] = error_count
        _save(live.step, live.output)
        jobs.fail_job(db, job_id=claimed.job_id, lease_token=claimed.token, error="poll_pending",
                      retry_delay_seconds=delay)


def _terminal(kind, claimed, detail, *, refund: bool, code: str | None, category: str | None = None):
    """End one job: refund a definite rejection, or hold its credits for reconciliation."""
    code = safe_error_code(code, fallback="worker_error" if refund else "provider_failed")
    category = category if category in CATEGORIES else error_category(code)
    payload, fields = claimed.payload, claimed.fields
    with Session.begin() as db:
        live = _live(db, claimed.job_id, claimed.token)
        if not live or not jobs.fail_job(db, job_id=claimed.job_id, lease_token=claimed.token, error=detail[:500]):
            return
        status = "failed" if refund else NEEDS_ATTENTION
        live.record.update(status=status, detail=detail[:300], error={"code": code, "category": category})
        provider_progress(live.record, stage=status)
        log_event(logger, "job_failed", level=logging.WARNING, **fields, status=status, refund=refund,
                  error_code=code, category=category)
        if refund:
            usage.post_credit(db, live.job.workspace_id, payload["credits"], f"{kind.name}_refund",
                              payload["refund_reference"])
            log_event(logger, "credit_refunded", **fields, credits=payload["credits"],
                      reference=payload["refund_reference"])
        _save(live.step, live.output)
        settle(db, kind, live.run, live.step, live.output)


def _store(kind, claimed, client, urls, download):
    if not urls:
        raise ValueError("The provider returned no file")
    for url in urls:
        kind.validate_url(client, claimed.payload, url)
    _store_files(kind, claimed, [lambda path, url=url: download(url, path) for url in urls])


def _store_files(kind, claimed, writers: list[Callable[[Path], Any]]):
    """Write each file to ``<asset_id>.part``, check it, then record every asset and settle the step."""
    payload, fields = claimed.payload, claimed.fields
    if not writers:
        raise ValueError("The provider returned no file")
    with Session() as db:
        live = _live(db, claimed.job_id, claimed.token, lock=False)
        if not live:
            return
        root = media_root(db) / live.job.workspace_id
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    stored = []
    started = time.monotonic()
    try:
        for write in writers:
            asset_id = str(uuid.uuid4())
            partial = root / f"{asset_id}.part"
            paths.append(partial)
            write(partial)
            size = partial.stat().st_size
            content_type, extension, extra = kind.inspect(partial, payload)
            target = root / asset_id
            partial.replace(target)
            paths[-1] = target
            stored.append((asset_id, size, content_type, extension, extra))
        with Session.begin() as db:
            live = _live(db, claimed.job_id, claimed.token)
            if not live or not jobs.complete_job(db, job_id=claimed.job_id, lease_token=claimed.token):
                raise _Discard()
            used = db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(
                Asset.workspace_id == live.job.workspace_id))
            if used + sum(item[1] for item in stored) > workspace_media_quota():
                raise ValueError("Workspace media quota reached")
            model = payload.get("model") or payload.get("model_id")
            entries = []
            for asset_id, size, content_type, extension, extra in stored:
                filename = f"{kind.name}-{asset_id[:8]}.{extension}"
                db.add(Asset(id=asset_id, workspace_id=live.job.workspace_id, project_id=live.run.project_id,
                             run_id=live.run.id, step_id=live.step.id, provider=payload["provider"], model=model,
                             filename=filename, content_type=content_type, bytes=size))
                entries.append({"id": asset_id, "asset_id": asset_id, "filename": filename,
                                "content_type": content_type, "provider": payload["provider"], "model": model,
                                "scene_index": payload.get("scene_index"), **extra})
            record = live.record
            provider_progress(record, stage="completed", status="completed")
            for key in ("submission", "error_count"):
                record.pop(key, None)
            record.update(status="succeeded", assets=entries)
            if not db.scalar(select(UsageEvent.id).where(UsageEvent.reference == payload["usage_reference"])):
                db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=live.job.workspace_id,
                                  tool=f"{payload['provider']}/{kind.name}", units=len(entries),
                                  credits=payload["credits"], reference=payload["usage_reference"], created_at=_now()))
            log_event(logger, "job_completed", **fields, asset_ids=[entry["id"] for entry in entries],
                      bytes=sum(item[1] for item in stored), download_ms=round((time.monotonic() - started) * 1000),
                      credits_charged=payload["credits"])
            _save(live.step, live.output)
            db.flush()
            settle(db, kind, live.run, live.step, live.output)
    except _Discard:
        _remove(paths)
    except Exception:
        _remove(paths)
        raise


class _Discard(Exception):
    """The lease was lost before the result could be recorded; another worker owns the job."""


def _remove(paths):
    for path in paths:
        path.unlink(missing_ok=True)


def _order(entry):
    scene = entry.get("scene_index")
    return (scene if isinstance(scene, int) else 0, entry.get("filename") or "")


def settle(db, kind: MediaKind, run: WorkflowRun, step: WorkflowRunStep, output: dict) -> None:
    """Update the step from its jobs; once all have finished, finish the step and continue the run."""
    job_rows = list(db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id)))
    recs = records(output)
    succeeded = sorted((entry for record in recs.values() if isinstance(record, dict)
                        and record.get("status") == "succeeded" for entry in record.get("assets") or []), key=_order)
    output[kind.output_key] = succeeded
    if any(job.state in ("queued", "leased") for job in job_rows):
        _save(step, output)
        return
    statuses = [recs.get(job.id, {}).get("status") for job in job_rows]
    asset_ids = tuple(entry["id"] for entry in succeeded)
    now = _now()
    context = ExecutionContext.for_run(db, run, now=now)
    if statuses and all(status == "succeeded" for status in statuses):
        result = NodeExecutionResult.completed(kind.completed_detail, output, asset_ids=asset_ids)
    else:
        uncertain = [recs[job.id] for job in job_rows if recs.get(job.id, {}).get("status") == NEEDS_ATTENTION]
        failed = uncertain or [recs.get(job.id, {}) for job in job_rows if recs.get(job.id, {}).get("status") != "succeeded"]
        error = failed[0].get("error") if failed and isinstance(failed[0].get("error"), dict) else {}
        code = safe_error_code(error.get("code"), fallback="provider_failed")
        category = error.get("category") if error.get("category") in CATEGORIES else error_category(code)
        node_error = NodeError(code, "One or more operations did not succeed", False, category)
        if uncertain:
            result = NodeExecutionResult(NEEDS_ATTENTION, kind.attention_detail, output, error=node_error,
                                         asset_ids=asset_ids)
        else:
            result = NodeExecutionResult.failed(node_error, kind.failed_detail, output=output, asset_ids=asset_ids)
    log_event(logger, "media_step_settled", workspace_id=run.workspace_id, workflow_id=run.workflow_id,
              run_id=run.id, step_id=step.id, status=result.status, succeeded=len(succeeded),
              jobs=len(job_rows))
    default_executor.finish_step(context, step, result)


def public_records(output: dict) -> dict:
    """The per-job facts safe for API responses: no submission handles or provider progress."""
    value = output.get("jobs")
    if not isinstance(value, dict):
        return output
    output["jobs"] = {job_id: {key: item for key, item in record.items() if key not in PRIVATE_FIELDS}
                      for job_id, record in value.items() if isinstance(record, dict)}
    return output
