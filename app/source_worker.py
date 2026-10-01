"""Source worker: fetch web pages for URL Source steps and transcribe media for Transcript steps.

Run ``python -m app.source_worker`` next to the API (``--once`` processes one due
job). It claims the jobs whose logical key starts with ``source:``:

* ``source.fetch`` (free): one public web page, fetched with the SSRF rules of
  ``app/sources.py``. A timeout or an unreachable host is tried again twice; any
  other problem (a blocked address, not HTML, too large, no readable text) fails
  the step with a stable code. Nothing is executed and no paywall is bypassed.
* ``transcription.generate`` (paid): FFmpeg extracts mono 16 kHz MP3 audio from the
  audio or video asset, in pieces of at most 20 minutes, inside a private folder
  (``<media>/.source-tmp/<job_id>``, always removed). Each piece goes to the
  workspace's transcription provider, and the timed segments are joined into one
  transcript. A definite provider rejection or a local problem refunds the
  credits; a lost response, or a crash while the provider was being called, holds
  them for credit reconciliation and is never sent again automatically.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
import uuid

import httpx
from sqlalchemy import select, update

from app import heartbeat, jobs, render, sources, storage, usage
from app.logs import log_event, payload_summary
from app.media_paths import media_root
from app.models import CreditReconciliation, UsageEvent, WorkflowRun, WorkflowRunStep
from app.provider_progress import provider_progress, safe_error_code, step_output
from app.providers.errors import ProviderError, error_category
from app.providers.transcription import TranscriptionProviderError, create_transcription_provider
from app.runtime_env import start_process
from app.workflow import ExecutionContext, NodeError, NodeExecutionResult, default_executor
from app.workflow.results import COMPLETED, NEEDS_ATTENTION, QUEUED, RUNNING
from app import system_config

logger = logging.getLogger(__name__)

LEASE_SECONDS = 1800
MAX_ATTEMPTS = 3
TEMP_FOLDER = ".source-tmp"
CHUNK_SECONDS = 1200
MAX_SEGMENTS = 5000
FETCH_RETRY_CODES = frozenset({"timeout", "unreachable"})
DEFINITIVE_FAILURES = frozenset({"invalid_request", "authentication_error", "billing_error", "not_found",
                                 "missing_key", "invalid_config", "unsupported_provider", "unsupported_model",
                                 "attempts_exhausted", "rate_limited", "content_rejected", "empty_output"})
FETCH_RUNNING_DETAIL = "Đang tải nội dung trang web."
FETCH_DONE_DETAIL = "Đã lấy nội dung trang web."
FETCH_RETRY_DETAIL = "Trang web chưa phản hồi; sẽ thử lại."
TRANSCRIBE_RUNNING_DETAIL = "Đang phiên âm."
TRANSCRIBE_DONE_DETAIL = "Đã phiên âm."
RETRY_DETAIL = "Provider tạm giới hạn yêu cầu; sẽ thử lại."
FAILED_DETAIL = "Không phiên âm được; đã hoàn credits."
ATTENTION_DETAIL = "Cần đối soát với provider; credit đang được giữ, không tự gửi lại."
FETCH_DETAILS = {
    "blocked_url": "Địa chỉ này bị chặn: chỉ lấy được trang công khai qua https://.",
    "invalid_url": "Địa chỉ trang web không hợp lệ.",
    "too_large": "Trang web lớn hơn 2 MB.",
    "unsupported_type": "Chỉ lấy được trang HTML hoặc văn bản thuần.",
    "no_text": "Không tìm thấy nội dung đọc được (trang có thể cần JavaScript hoặc đăng nhập).",
    "fetch_failed": "Không tải được trang web.",
    "timeout": "Trang web không phản hồi kịp.",
    "unreachable": "Không kết nối được tới trang web.",
}
_ASSET_ID = re.compile(r"[A-Za-z0-9-]{1,64}\Z")


class SourceJobError(Exception):
    """A local failure before or around the provider call, with a stable code; credits are refunded."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Claim:
    job_id: str
    token: str
    payload: dict
    workspace_id: str
    run_id: str
    step_id: str
    attempt: int
    root: Path

    @property
    def fields(self) -> dict:
        return {"job_id": self.job_id, "workspace_id": self.workspace_id, "run_id": self.run_id,
                "step_id": self.step_id}


def _now():
    return datetime.now(timezone.utc)


def _default_session_factory():
    # Imported here so tests can pass their own factory without reading instance/bootstrap.json.
    from app.db import Session
    return Session


def _live(db, claim: Claim):
    job = jobs.live_lease(db, job_id=claim.job_id, lease_token=claim.token)
    if job is None or db.get(CreditReconciliation, job.id) is not None:
        return None
    # Same lock order as the executor, reconciliation and the other workers: run, then account.
    db.execute(update(WorkflowRun).where(WorkflowRun.id == job.run_id).values(status=WorkflowRun.status))
    run = db.get(WorkflowRun, job.run_id)
    step = db.get(WorkflowRunStep, job.step_id)
    return (job, run, step) if step.status in (QUEUED, RUNNING) else None


def _paid(payload) -> bool:
    return payload.get("kind") == "transcription.generate"


def _finish(Session, claim: Claim, result: NodeExecutionResult, *, units: int | None = None) -> None:
    """Close the job, settle credits, record the step and continue the run, in one transaction."""
    with Session.begin() as db:
        live = _live(db, claim)
        if not live:
            return
        _, run, step = live
        succeeded = result.status == COMPLETED
        closed = (jobs.complete_job(db, job_id=claim.job_id, lease_token=claim.token) if succeeded else
                  jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=result.error.code))
        if not closed:
            return
        payload = claim.payload
        output = step_output(step)
        output.update(result.output or {})
        if succeeded:
            output.pop("error", None)
        if _paid(payload):
            output = provider_progress(output, stage=result.status, accepted=succeeded,
                                       status="completed" if succeeded else None)
            if succeeded:
                reference = payload["usage_reference"]
                if not db.scalar(select(UsageEvent.id).where(UsageEvent.reference == reference)):
                    db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=claim.workspace_id,
                                      tool=f"{payload['provider']}/transcription", units=max(1, units or 1),
                                      credits=payload["credits"], reference=reference, created_at=_now()))
                log_event(logger, "job_completed", **claim.fields, credits_charged=payload["credits"], units=units)
            elif result.status != NEEDS_ATTENTION:
                usage.post_credit(db, claim.workspace_id, payload["credits"], "transcription_refund",
                                  payload["refund_reference"])
                log_event(logger, "credit_refunded", **claim.fields, credits=payload["credits"],
                          reference=payload["refund_reference"])
        result.output = output
        if result.status not in (COMPLETED,):
            log_event(logger, "job_failed", level=logging.WARNING, **claim.fields, kind=payload.get("kind"),
                      error_code=result.error.code if result.error else None, status=result.status)
        default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step, result)


def _requeue(Session, claim: Claim, *, delay: int, code: str, detail: str) -> None:
    with Session.begin() as db:
        live = _live(db, claim)
        if not live:
            return
        _, _, step = live
        step.status, step.detail = QUEUED, detail
        if jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=code, retry_delay_seconds=delay):
            log_event(logger, "job_retry_scheduled", **claim.fields, attempt=claim.attempt, delay_seconds=delay,
                      error_code=code)


def _failed(code: str, message: str, detail: str, category: str | None = None) -> NodeExecutionResult:
    return NodeExecutionResult.failed(NodeError(code, message[:300], False, category or error_category(code)),
                                      detail=detail)


def _uncertain(code="submission_unknown") -> NodeExecutionResult:
    code = safe_error_code(code, fallback="submission_unknown")
    return NodeExecutionResult(NEEDS_ATTENTION, ATTENTION_DETAIL,
                               error=NodeError(code, ATTENTION_DETAIL, False, error_category(code)))


# Web pages -----------------------------------------------------------------------

def _fetch(Session, claim: Claim, client: httpx.Client | None, resolver) -> None:
    url = claim.payload.get("url")
    owned = client is None
    client = client or httpx.Client(follow_redirects=False, trust_env=False, timeout=sources.TIMEOUT)
    started = time.monotonic()
    try:
        source = sources.page_source(sources.fetch_page(url if isinstance(url, str) else "", client=client,
                                                        resolver=resolver))
    except sources.SourceError as exc:
        log_event(logger, "source_fetch_failed", level=logging.WARNING, **claim.fields, error_code=exc.code,
                  attempt=claim.attempt, duration_ms=round((time.monotonic() - started) * 1000))
        if exc.code in FETCH_RETRY_CODES and claim.attempt < MAX_ATTEMPTS:
            _requeue(Session, claim, delay=10 * claim.attempt, code=exc.code, detail=FETCH_RETRY_DETAIL)
        else:
            _finish(Session, claim, _failed(exc.code, str(exc), FETCH_DETAILS.get(exc.code, FETCH_DETAILS["fetch_failed"]),
                                            "invalid_request"))
        return
    finally:
        if owned:
            client.close()
    log_event(logger, "source_fetched", **claim.fields, chars=len(source["text"]),
              duration_ms=round((time.monotonic() - started) * 1000))
    _finish(Session, claim, NodeExecutionResult.completed(FETCH_DONE_DETAIL, {
        "source": source, "text": source["text"], "title": source["title"], "source_url": source["source_url"]}))


# Transcription -------------------------------------------------------------------

def _audio_parts(claim: Claim, folder: Path, runner) -> tuple[list[Path], float]:
    """Mono MP3 pieces of the asset's audio and the media duration; raises ``SourceJobError``."""
    asset_id = claim.payload.get("asset_id")
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        raise SourceJobError("input_missing", "The media asset ID is invalid")
    path = storage.file_in(claim.root, claim.workspace_id, asset_id)
    if not path.is_file():
        raise SourceJobError("input_missing", "The media file is missing from storage")
    ffmpeg, ffprobe = render.tools()
    if not ffmpeg or not ffprobe:
        raise SourceJobError("ffmpeg_missing", "FFmpeg or ffprobe is not installed")
    try:
        info = render.probe(ffprobe, path, runner)
    except render.RenderError as exc:
        raise SourceJobError("invalid_media", "The media file could not be read") from exc
    if not info["has_audio"]:
        raise SourceJobError("no_audio", "The media file has no audio track")
    limit = transcription_max_seconds()
    if info["duration"] > limit:
        raise SourceJobError("too_long", f"The media is longer than {limit // 60} minutes")
    folder.mkdir(parents=True, exist_ok=True)
    try:
        render.run_ffmpeg([ffmpeg, "-nostdin", "-hide_banner", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar",
                           "16000", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "segment", "-segment_time",
                           str(CHUNK_SECONDS), "-reset_timestamps", "1", "part-%03d.mp3"],
                          folder, render.render_timeout_seconds(), runner)
    except render.RenderError as exc:
        raise SourceJobError("audio_extraction_failed", str(exc)) from exc
    parts = sorted(folder.glob("part-*.mp3"))
    if not parts:
        raise SourceJobError("audio_extraction_failed", "FFmpeg produced no audio")
    return parts, info["duration"]


def transcription_max_seconds() -> int:
    """Longest media one Transcript step accepts (``TRANSCRIPTION_MAX_SECONDS``, default 3 hours)."""
    try:
        value = int(system_config.env("TRANSCRIPTION_MAX_SECONDS").strip() or str(3 * 3600))
    except ValueError as exc:
        raise RuntimeError("TRANSCRIPTION_MAX_SECONDS must be a positive integer") from exc
    if not 60 <= value <= 12 * 3600:
        raise RuntimeError("TRANSCRIPTION_MAX_SECONDS must be between 60 and 43200")
    return value


def _mark_submitting(Session, claim: Claim) -> bool:
    with Session.begin() as db:
        live = _live(db, claim)
        if not live or live[2].status != RUNNING:
            return False
        step = live[2]
        step.output = json.dumps(provider_progress(step_output(step), stage="submitting", started=True))
        return True


def _transcribe(Session, claim: Claim, provider_factory, runner) -> None:
    payload = claim.payload
    folder = claim.root / TEMP_FOLDER / claim.job_id
    started = time.monotonic()
    try:
        try:
            parts, duration = _audio_parts(claim, folder, runner)
        except SourceJobError as exc:
            log_event(logger, "transcription_failed", level=logging.WARNING, **claim.fields, error_code=exc.code)
            _finish(Session, claim, _failed(exc.code, str(exc), FAILED_DETAIL, "invalid_request"))
            return
        if not _mark_submitting(Session, claim):
            return
        log_event(logger, "provider_request_started", **claim.fields, request=payload_summary(payload),
                  parts=len(parts))
        text_parts, segments, language, request_id = [], [], None, None
        try:
            provider = provider_factory(payload["provider"])
            try:
                for index, part in enumerate(parts):
                    result = provider.transcribe(part, model=payload["model"], language=payload.get("language"))
                    offset = index * CHUNK_SECONDS
                    text_parts.append(result.text)
                    language = language or result.language
                    request_id = request_id or result.remote_request_id
                    segments += [{"start": round(segment.start + offset, 3), "end": round(segment.end + offset, 3),
                                  "text": segment.text} for segment in result.segments]
            finally:
                provider.close()
        except Exception as exc:  # noqa: BLE001 - every outcome is recorded below
            code = safe_error_code(getattr(exc, "code", None), fallback="submission_unknown")
            if isinstance(exc, ProviderError):
                log_event(logger, "provider_request_failed", level=logging.WARNING, **claim.fields,
                          attempt=claim.attempt, error=exc.describe())
            else:
                logger.exception("Transcription job %s failed unexpectedly", claim.job_id)
            if (isinstance(exc, TranscriptionProviderError) and code == "rate_limited" and exc.retryable
                    and claim.attempt < MAX_ATTEMPTS):
                _requeue(Session, claim, delay=5 * 2 ** (claim.attempt - 1), code=code, detail=RETRY_DETAIL)
            elif isinstance(exc, TranscriptionProviderError) and code in DEFINITIVE_FAILURES:
                _finish(Session, claim, _failed(code, "Transcription failed", FAILED_DETAIL))
            else:
                _finish(Session, claim, _uncertain(code))
            return
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    text = " ".join(part.strip() for part in text_parts if part.strip())
    transcript = sources.make_source(
        "transcript", title=payload.get("title") or "", text=text, language=language or payload.get("language"),
        asset_id=payload.get("asset_id"), segments=segments[:MAX_SEGMENTS],
        metadata={"duration": round(duration, 3), "provider": payload["provider"], "model": payload["model"],
                  "parts": len(parts)})
    log_event(logger, "provider_request_completed", **claim.fields, parts=len(parts), segments=len(segments),
              output_chars=len(text), media_seconds=round(duration), latency_ms=round((time.monotonic() - started) * 1000))
    _finish(Session, claim, NodeExecutionResult.completed(TRANSCRIBE_DONE_DETAIL, {
        "transcript": transcript, "text": transcript["text"], "language": transcript["language"],
        "segment_count": len(transcript["segments"] or []), "duration": transcript["metadata"]["duration"],
        "provider": payload["provider"], "model": payload["model"], "request_id": request_id}),
        units=max(1, round(duration / 60)))


def run_one(*, client: httpx.Client | None = None, resolver=socket.getaddrinfo,
            provider_factory=create_transcription_provider, runner=subprocess.run, session_factory=None,
            worker_id: str | None = None) -> bool:
    """Claim and run one source job; returns False when none is due."""
    Session = session_factory or _default_session_factory()
    worker_id = worker_id or f"source-{os.getpid()}"
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=LEASE_SECONDS,
                                      logical_key_prefix="source:")
        if not claimed:
            return False
        job = claimed[0]
        claim = Claim(job.id, job.lease_token, job.payload, job.workspace_id, job.run_id, job.step_id,
                      job.attempt_count, media_root(db))
        kind = job.payload.get("kind")
        log_event(logger, "job_claimed", **claim.fields, worker_id=worker_id, attempt=job.attempt_count, kind=kind)
        step = db.get(WorkflowRunStep, job.step_id)
        if (step.status not in (QUEUED, RUNNING) or db.get(CreditReconciliation, job.id) is not None
                or kind not in ("source.fetch", "transcription.generate")):
            jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="invalid_step_state")
            return True
        # A crash after the provider call started leaves an unknown outcome: never send it again.
        progress = step_output(step).get("provider_job")
        interrupted = (step.status == RUNNING and _paid(job.payload) and isinstance(progress, dict)
                       and bool(progress.get("submission_started_at")))
        step.status = RUNNING
        step.detail = FETCH_RUNNING_DETAIL if kind == "source.fetch" else TRANSCRIBE_RUNNING_DETAIL
    if interrupted:
        _finish(Session, claim, _uncertain())
        return True
    if claim.attempt > MAX_ATTEMPTS:
        code = "attempts_exhausted"
        _finish(Session, claim, _failed(code, "Too many attempts", FAILED_DETAIL if _paid(claim.payload)
                                        else FETCH_DETAILS["fetch_failed"]))
        return True
    if kind == "source.fetch":
        _fetch(Session, claim, client, resolver)
    else:
        _transcribe(Session, claim, provider_factory, runner)
    return True


def main():
    parser = argparse.ArgumentParser(description="Process ReelForge source jobs (web pages and transcription)")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    args = parser.parse_args()
    start_process("source_worker")
    while True:
        worked = run_one()
        heartbeat.beat("source_worker")
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
