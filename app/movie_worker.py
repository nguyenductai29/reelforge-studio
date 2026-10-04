"""Movie worker: imports movie sources into Google Drive, deletes them, and runs the movie steps of a workflow.

Run ``python -m app.movie_worker`` next to the API (systemd: ``reelforge-worker@movie``; ``--once`` does one pass of
each lane, ``--check`` reports the tools and settings without doing anything). Two lanes run side by side, so a
long import never holds up a review in progress:

**Sources** (a lease on the ``movie_sources`` row, see app/movie_sources.py):

* *import* (``importing``): an import-folder file is copied, a direct URL downloaded (SSRF rules of
  app/movie_media.py) or a file of the workspace's Drive inbox downloaded into
  ``<scratch>/imports/<id>/source.part``, hashed (SHA-256 and MD5) on the way, recognized by its first bytes and
  checked with ffprobe (container, codecs, duration within the limit) → ``uploading``;
* *upload* (``uploading``): a resumable upload to ``<root>/movie-sources/<workspace>/<id>/source.<ext>`` (an inbox
  file is moved there instead). The session URL is stored encrypted, so a restart resumes the upload; Drive's
  size and MD5 must match → ``ready``, and the member who added the movie is notified;
* *deletion* (``delete_scheduled``): the Drive file goes to the trash (or is deleted, per the setting) and the
  local copies are removed → ``deleted``. The row stays, as history.

A network problem is tried again later (1, 2, 4… minutes, ``MAX_SOURCE_ATTEMPTS`` times; deletions without limit,
at most every ``MAX_DELETE_DELAY_SECONDS``); anything else fails the import at once with a stable code.

**Step jobs** (``movie:`` logical keys in ``workflow_jobs``):

* ``movie.prepare`` (free): the scratch copy (downloaded from Drive once per source, checked against its MD5 and
  shared by every step and run), the sound as mono 16 kHz MP3 (a ``movie_audio`` intermediate asset for
  Transcript), scene cuts and the sampled frames (small JPEGs in the run's scratch folder, never assets);
* ``vision.analyze`` (paid, one job per batch of frames): the chosen Text model describes the frames (images in,
  JSON out). Credits follow app/workflow/nodes/media.py: charged once on success, refunded after a definite
  rejection, held for reconciliation when the outcome is unknown. The step settles when every batch has ended;
* ``movie.clip_extract`` (free): the Clip Selector's excerpts, cut from the scratch copy (stream copy for an MP4
  source, else H.264/AAC) and stored as ``extracted_clip`` assets that record their ``movie_source_id``.

Scratch space is swept every ``SWEEP_INTERVAL_SECONDS``: a run's frames once the run has ended, a source's copy
once no running run uses it (after ``SCRATCH_IDLE_SECONDS``, when ``delete_scratch`` is on), and everything of a
deleted source. Nothing here logs a path, a URL, a token or a frame.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Callable
import uuid

import httpx
from sqlalchemy import select, update

from app import audit, google_drive, heartbeat, jobs, movie_media, movie_sources, render, secret_box, storage, usage
from app.logs import log_event
from app.models import (Asset, CreditReconciliation, MovieSource, UsageEvent, WorkflowJob, WorkflowRun,
                        WorkflowRunStep)
from app.provider_progress import provider_progress, safe_error_code, step_output
from app.providers.errors import CATEGORIES, ProviderError, error_category
from app.providers.text import TextImage, TextProviderError, create_text_provider, text_provider_config_issue
from app.runtime_env import start_process
from app.workflow import ExecutionContext, NodeError, NodeExecutionResult, default_executor
from app.workflow.nodes.movie import (VISION_MAX_TOKENS, VISION_SYSTEM_PROMPT, movie_value, parse_vision,
                                      vision_prompt)
from app.workflow.results import NEEDS_ATTENTION, QUEUED, RUNNING

logger = logging.getLogger(__name__)

WORKER = "movie_worker"
JOB_LEASE_SECONDS = 900
TICK_SECONDS = 10
MAX_SOURCE_ATTEMPTS = 5
MAX_JOB_ATTEMPTS = 3
MAX_DELETE_DELAY_SECONDS = 6 * 3600
SWEEP_INTERVAL_SECONDS = 600
SCRATCH_IDLE_SECONDS = 1800
LOCK_STALE_SECONDS = 6 * 3600
JOB_KINDS = ("movie.prepare", "vision.analyze", "movie.clip_extract")
MAX_MOVIE_CLIPS = 40
MAX_CUTS = 500
# Provider answers that prove the request was refused, so the batch's credits are returned.
VISION_DEFINITIVE = frozenset({"invalid_request", "authentication_error", "billing_error", "not_found", "missing_key",
                               "invalid_config", "unsupported_provider", "unsupported_model", "attempts_exhausted",
                               "rate_limited", "content_rejected"})
ACTIVE_RECORDS = ("queued", "submitting")
# Local problems before any provider call: refunded, and reported with their own code.
LOCAL_CODES = frozenset({"ffmpeg_missing", "source_expired", "invalid_frame", "scratch_busy", "checksum_mismatch",
                         "frame_extraction_failed", "attempts_exhausted", "worker_error"})

PREPARE_RUNNING_DETAIL = "Đang chuẩn bị phim."
PREPARE_DOWNLOAD_DETAIL = "Đang tải phim từ Google Drive về máy chủ: {done} / {total} MB."
PREPARE_AUDIO_DETAIL = "Đang tách âm thanh để phiên âm."
PREPARE_CUTS_DETAIL = "Đang tìm các điểm chuyển cảnh."
PREPARE_FRAMES_DETAIL = "Đang lấy khung hình: {done} / {total}."
PREPARE_DONE_DETAIL = "Đã chuẩn bị phim: {frames} khung hình, âm thanh để phiên âm."
PREPARE_FAILED_DETAIL = "Không chuẩn bị được phim."
VISION_RUNNING_DETAIL = "Đang phân tích hình ảnh: {done} / {total} lô ({frames} / {frame_total} khung hình)."
VISION_DONE_DETAIL = "Đã phân tích {frames} khung hình."
VISION_PARTIAL_DETAIL = "Đã phân tích {done} / {total} lô khung hình; các lô lỗi đã được hoàn credits."
VISION_FAILED_DETAIL = "Không phân tích được hình ảnh; đã hoàn credits."
VISION_ATTENTION_DETAIL = "Cần đối soát với provider; credit đang được giữ, không tự gửi lại."
VISION_RETRY_DETAIL = "Provider tạm giới hạn yêu cầu; sẽ thử lại."
VISION_REJECTED_DETAIL = "Provider từ chối lô khung hình; đã hoàn credits."
CLIPS_RUNNING_DETAIL = "Đang cắt đoạn trích từ phim: {done} / {total}."
CLIPS_DONE_DETAIL = "Đã cắt {count} đoạn trích ({seconds} giây)."
CLIPS_FAILED_DETAIL = "Cắt đoạn trích thất bại."
JOB_RETRY_DETAIL = "Tạm thời chưa tải được phim; sẽ thử lại."
_sweep = {"at": None}


class StageError(Exception):
    """An import, upload or deletion problem with a stable code (never a path or a URL)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code[:48]
        self.retryable = retryable


class JobError(Exception):
    """A step job problem with a stable code; ``retry_after`` (seconds) puts the job back instead of failing it."""

    def __init__(self, code: str, message: str, *, retry_after: int | None = None, category: str | None = None):
        super().__init__(message)
        self.code = code[:48]
        self.retry_after = retry_after
        self.category = category or ("configuration_error" if code in ("ffmpeg_missing", "storage_limit_exceeded")
                                     else "invalid_request")


class LeaseLost(Exception):
    """Another worker owns the work now: stop without writing anything."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_session_factory():
    # Imported here so tests can pass their own factory without reading instance/bootstrap.json.
    from app.db import Session
    return Session


def _move(path: Path, target: Path) -> None:
    """Rename, or copy then remove when the scratch space is on another file system than the media."""
    try:
        os.replace(path, target)
    except OSError:
        shutil.copyfile(path, target)
        path.unlink(missing_ok=True)


class Throttle:
    """Calls ``action`` at most every ``seconds`` (the first call too waits), for lease renewals during long work."""

    def __init__(self, action: Callable[..., None], seconds: float = TICK_SECONDS, clock=time.monotonic):
        self.action, self.seconds, self.clock = action, seconds, clock
        self.last = clock()

    def __call__(self, *args, force: bool = False, **kwargs) -> None:
        now = self.clock()
        if not force and now - self.last < self.seconds:
            return
        self.last = now
        self.action(*args, **kwargs)


# =================================================================================================================
# Sources: import, upload, deletion
# =================================================================================================================

@dataclass
class SourceClaim:
    source_id: str
    token: str
    status: str
    workspace_id: str
    attempt: int
    root: Path

    @property
    def fields(self) -> dict:
        return {"workspace_id": self.workspace_id, "movie_source_id": self.source_id, "attempt": self.attempt}


def _advance(source: MovieSource, status: str, now: datetime) -> None:
    """Move to the next status with a fresh attempt budget, no lease and no failure."""
    source.status, source.updated_at = status, now
    source.attempt_count, source.next_attempt_at = 0, now if status in movie_sources.WORK_STATUSES else None
    source.lease_token = source.lease_expires_at = source.worker_id = None
    source.failure_stage = source.failure_code = source.failure_message_safe = None
    source.progress_bytes = 0 if status in movie_sources.WORKING else None


def _stage_error(exc: Exception) -> StageError:
    if isinstance(exc, StageError):
        return exc
    if isinstance(exc, movie_media.MovieMediaError):
        return StageError(exc.code, str(exc), retryable=exc.retryable)
    if isinstance(exc, google_drive.DriveError):
        code = exc.code if exc.code in ("too_large",) else f"drive_{exc.code}"
        return StageError(code, str(exc), retryable=exc.retryable)
    if isinstance(exc, movie_sources.MovieSourceError):
        return StageError(exc.code, str(exc))
    if isinstance(exc, render.RenderError):
        return StageError(exc.code, str(exc))
    return StageError("worker_error", f"Movie worker error: {type(exc).__name__}", retryable=True)


def run_source_work(*, session_factory=None, worker_id: str | None = None, http_client: httpx.Client | None = None,
                    resolver=socket.getaddrinfo, drive_factory=None, runner=subprocess.run) -> bool:
    """Lease and do one import, upload or deletion; False when no source needs the worker."""
    Session = session_factory or _default_session_factory()
    worker_id = worker_id or f"movie-{os.getpid()}"
    drive_factory = drive_factory or google_drive.DriveClient
    with Session.begin() as db:
        source = movie_sources.claim_work(db, worker_id=worker_id)
        if source is None:
            return False
        if source.status == "delete_scheduled":
            source.status = "deleting"
        elif source.status == "importing" and source.attempt_count == 1 and source.failure_code is None:
            audit.record(db, "movie_source.import_started", actor_id=None, workspace_id=source.workspace_id,
                         target_type="movie_source", target_id=source.id, details={"source_type": source.source_type})
        claim = SourceClaim(source.id, source.lease_token, source.status, source.workspace_id, source.attempt_count,
                            movie_sources.scratch_root(db))
    stage = {"importing": "import", "uploading": "upload"}.get(claim.status, "delete")
    log_event(logger, "movie_source_claimed", **claim.fields, stage=stage, worker_id=worker_id)
    try:
        if stage == "import":
            _import(Session, claim, http_client=http_client, resolver=resolver, drive_factory=drive_factory,
                    runner=runner)
        elif stage == "upload":
            _upload(Session, claim, drive_factory=drive_factory)
        else:
            _delete(Session, claim, drive_factory=drive_factory)
    except LeaseLost:
        log_event(logger, "movie_source_lease_lost", level=logging.WARNING, **claim.fields, stage=stage)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the source with a stable code
        if not isinstance(exc, (StageError, movie_media.MovieMediaError, google_drive.DriveError,
                                movie_sources.MovieSourceError, render.RenderError)):
            logger.exception("movie source %s failed unexpectedly", claim.source_id)
        _source_failed(Session, claim, stage, _stage_error(exc))
    return True


def _keep_alive(Session, claim: SourceClaim) -> Throttle:
    def renew(moved: int | None = None) -> None:
        if not movie_sources.renew(Session, claim.source_id, claim.token, progress=moved):
            raise LeaseLost()
    return Throttle(renew)


def _source_failed(Session, claim: SourceClaim, stage: str, error: StageError) -> None:
    now = _now()
    with Session.begin() as db:
        source = movie_sources.live(db, claim.source_id, claim.token)
        if source is None:
            return
        first = source.failure_code is None
        source.failure_stage, source.failure_code = stage, error.code
        source.failure_message_safe = str(error)[:300]
        source.lease_token = source.lease_expires_at = None
        source.updated_at = now
        if stage == "delete":
            # A deletion is never given up: it is retried, less and less often, and admins are alerted.
            delay = min(MAX_DELETE_DELAY_SECONDS, 60 * 2 ** min(max(0, claim.attempt - 1), 12))
            source.status, source.next_attempt_at = "delete_scheduled", now + timedelta(seconds=delay)
            if first:
                audit.record(db, "movie_source.drive_failed", actor_id=None, workspace_id=source.workspace_id,
                             target_type="movie_source", target_id=source.id,
                             details={"stage": stage, "code": error.code})
            log_event(logger, "movie_source_delete_failed", level=logging.WARNING, **claim.fields,
                      error_code=error.code, retry_in_seconds=delay)
            return
        if error.retryable and claim.attempt < MAX_SOURCE_ATTEMPTS:
            delay = 60 * 2 ** (claim.attempt - 1)
            source.next_attempt_at = now + timedelta(seconds=delay)
            log_event(logger, "movie_source_retry_scheduled", level=logging.WARNING, **claim.fields, stage=stage,
                      error_code=error.code, retry_in_seconds=delay)
            return
        source.status, source.next_attempt_at, source.progress_bytes = "failed", None, None
        action = "movie_source.drive_failed" if stage == "upload" or error.code.startswith("drive_") \
            else "movie_source.import_failed"
        audit.record(db, action, actor_id=None, workspace_id=source.workspace_id, target_type="movie_source",
                     target_id=source.id, details={"stage": stage, "code": error.code})
        movie_sources.notify(db, source, "movie_source.failed")
        log_event(logger, "movie_source_failed", level=logging.WARNING, **claim.fields, stage=stage,
                  error_code=error.code)
    if stage == "import":
        movie_media.remove_tree(movie_sources.import_dir(claim.root, claim.source_id))


def _inbox_file(drive, workspace_id: str, file_id: str | None) -> dict:
    """A file of this workspace's Drive inbox, never any other Drive file."""
    if not google_drive.valid_id(file_id):
        raise StageError("invalid_drive_file", "Choose a file from the studio's Drive inbox")
    inbox = drive.inbox_folder(workspace_id)
    meta = drive.file(file_id)
    if (inbox is None or inbox not in (meta.get("parents") or []) or meta.get("trashed")
            or meta.get("mimeType") == google_drive.FOLDER_MIME):
        raise StageError("invalid_drive_file", "The file is not in this studio's Drive inbox")
    return {**meta, "inbox": inbox}


def _import(Session, claim: SourceClaim, *, http_client, resolver, drive_factory, runner) -> None:
    current = movie_sources.settings()
    with Session() as db:
        source = db.get(MovieSource, claim.source_id)
        kind, relative, drive_id = source.source_type, source.local_path, source.drive_import_id
        url = movie_sources.decrypt_url(source.url_ciphertext) if source.url_ciphertext else None
    folder = movie_sources.import_dir(claim.root, claim.source_id)
    movie_media.remove_tree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / "source.part"
    hashes, keep, limit, started = movie_media.Hashes(), _keep_alive(Session, claim), current.max_source_bytes, \
        time.monotonic()
    if kind == "local":
        # Checked again here: the file may have been replaced (by a link, say) since it was chosen.
        path = movie_sources.resolve_local(relative or "",
                                           root=movie_sources.workspace_import_root(claim.workspace_id))
        movie_media.copy_file(path, partial, limit=limit, hashes=hashes, progress=keep)
    elif kind == "url":
        if not url:
            raise StageError("url_missing", "The address is no longer stored; add the movie again")
        owned = http_client is None
        client = http_client or httpx.Client(follow_redirects=False, trust_env=False)
        try:
            movie_media.fetch_url(url, partial, limit=limit, client=client, hashes=hashes, resolver=resolver,
                                  progress=keep)
        finally:
            if owned:
                client.close()
    else:
        with drive_factory() as drive:
            meta = _inbox_file(drive, claim.workspace_id, drive_id)
            if int(meta.get("size") or 0) > limit:
                raise StageError("too_large", "The movie is larger than the server allows")
            drive.download(drive_id, partial, limit=limit, on_chunk=hashes.update, progress=keep)
            if meta.get("md5Checksum") and meta["md5Checksum"] != hashes.md5.hexdigest():
                raise StageError("checksum_mismatch", "The download does not match Drive's checksum", retryable=True)
    if hashes.size == 0:
        raise StageError("invalid_media", "The movie file is empty")
    sniffed = movie_media.sniff_file(partial)
    if sniffed is None:
        raise StageError("unsupported_type", "The file is not an MP4, MOV, MKV or WebM movie")
    _, ffprobe = render.tools()
    if not ffprobe:
        raise StageError("ffmpeg_missing", "ffprobe is not installed on the server")
    info = movie_media.probe(ffprobe, partial, sniffed, runner)
    if info.duration > current.max_duration_seconds:
        raise StageError("too_long", "The movie is longer than the server allows")
    partial.replace(folder / f"source{movie_sources.CONTAINER_EXTENSIONS[info.container]}")
    with Session.begin() as db:
        source = movie_sources.live(db, claim.source_id, claim.token)
        if source is None:
            raise LeaseLost()
        source.container, source.content_type = info.container, info.content_type
        source.duration_seconds, source.width, source.height = info.duration, info.width, info.height
        source.video_codec, source.audio_codec = info.video_codec, info.audio_codec
        source.bytes, source.checksum_sha256, source.checksum_md5 = hashes.size, hashes.sha256.hexdigest(), \
            hashes.md5.hexdigest()
        _advance(source, "uploading", _now())
    log_event(logger, "movie_source_imported", **claim.fields, source_type=kind, bytes=hashes.size,
              duration_seconds=round(info.duration), container=info.container,
              duration_ms=round((time.monotonic() - started) * 1000))


def _move_from_inbox(drive, claim: SourceClaim, file_id: str, folder: str, name: str) -> dict | None:
    """Move the inbox file into the source's folder; None when Drive does not allow it (the copy is uploaded)."""
    try:
        meta = drive.file(file_id)
        parents = meta.get("parents") or []
        if folder in parents:
            return meta  # moved before a restart
        inbox = drive.inbox_folder(claim.workspace_id)
        if inbox is None or inbox not in parents or meta.get("trashed"):
            raise StageError("invalid_drive_file", "The file left the studio's Drive inbox")
        return drive.move(file_id, folder, inbox, name)
    except google_drive.DriveError as exc:
        if exc.code in ("forbidden", "invalid_request"):
            log_event(logger, "movie_source_move_refused", level=logging.WARNING, **claim.fields, error_code=exc.code)
            return None
        raise


def _verify(meta: dict, size: int, md5: str | None) -> None:
    if not google_drive.valid_id(meta.get("id")) or str(meta.get("size")) != str(size):
        raise StageError("verify_failed", "Google Drive does not hold the whole movie", retryable=True)
    if meta.get("md5Checksum") and md5 and meta["md5Checksum"] != md5:
        raise StageError("verify_failed", "Google Drive's checksum does not match the movie", retryable=True)


def _store_session(Session, claim: SourceClaim, session: str | None) -> None:
    with Session.begin() as db:
        source = movie_sources.live(db, claim.source_id, claim.token)
        if source is None:
            raise LeaseLost()
        source.upload_session_ciphertext = secret_box.encrypt_json(movie_sources.SESSION_PURPOSE, {"url": session}) \
            if session else None
        source.progress_bytes = 0


def _resumable(Session, drive, claim: SourceClaim, *, session_ciphertext, folder, name, local: Path, size: int,
               content_type: str, keep) -> dict:
    """Upload ``local``, resuming the stored session when Drive still has it."""
    session, offset = None, 0
    if session_ciphertext:
        try:
            session = secret_box.decrypt_json(movie_sources.SESSION_PURPOSE, session_ciphertext)["url"]
        except Exception:  # noqa: BLE001 - an unreadable session is simply started again
            session = None
    if session:
        try:
            state = drive.upload_offset(session, size)
        except google_drive.DriveError as exc:
            if exc.code != "session_expired":
                raise
            session = None
        else:
            if isinstance(state, dict):
                return state  # finished before a restart
            offset = state
    if not session:
        session = drive.start_upload(folder, name, content_type, size)
        _store_session(Session, claim, session)
    log_event(logger, "movie_source_upload_started", **claim.fields, bytes=size, resumed_at=offset)
    return drive.upload(session, local, size, offset=offset, progress=keep)


def _upload(Session, claim: SourceClaim, *, drive_factory) -> None:
    with Session() as db:
        source = db.get(MovieSource, claim.source_id)
        size, md5, content_type = source.bytes, source.checksum_md5, source.content_type or "video/mp4"
        name, inbox_id, session_ciphertext = f"source{movie_sources.extension_for(source)}", source.drive_import_id, \
            source.upload_session_ciphertext
    local = movie_sources.import_dir(claim.root, claim.source_id) / name
    keep, started = _keep_alive(Session, claim), time.monotonic()
    with drive_factory() as drive:
        folder = drive.source_folder(claim.workspace_id, claim.source_id)
        meta = _move_from_inbox(drive, claim, inbox_id, folder, name) if inbox_id else None
        moved = meta is not None
        if meta is None:
            if not size or not local.is_file() or local.stat().st_size != size:
                # The checked copy is gone (the scratch space was cleaned): fetch the movie again.
                with Session.begin() as db:
                    source = movie_sources.live(db, claim.source_id, claim.token)
                    if source is None:
                        raise LeaseLost()
                    source.upload_session_ciphertext = None
                    _advance(source, "importing", _now())
                log_event(logger, "movie_source_reimport", **claim.fields)
                return
            meta = _resumable(Session, drive, claim, session_ciphertext=session_ciphertext, folder=folder, name=name,
                              local=local, size=size, content_type=content_type, keep=keep)
        try:
            _verify(meta, size, md5)
        except StageError:
            if not moved and google_drive.valid_id(meta.get("id")):
                try:
                    drive.delete(meta["id"])  # a damaged upload is never kept
                except google_drive.DriveError:
                    logger.warning("could not remove a damaged movie upload", exc_info=True)
            _store_session(Session, claim, None)
            raise
        if inbox_id and not moved:
            try:
                original = drive.file(inbox_id)
                inbox = drive.inbox_folder(claim.workspace_id)
                if inbox and inbox in (original.get("parents") or []):
                    drive.remove(inbox_id)  # the copy is in the source's folder now
            except google_drive.DriveError:
                logger.warning("could not remove an imported inbox file", exc_info=True)
    now = _now()
    with Session.begin() as db:
        source = movie_sources.live(db, claim.source_id, claim.token)
        if source is None:
            raise LeaseLost()
        source.drive_file_id, source.drive_folder_id = meta["id"], folder
        source.upload_session_ciphertext = source.url_ciphertext = None  # the full address is not needed any more
        source.ready_at = now
        _advance(source, "ready", now)
        audit.record(db, "movie_source.ready", actor_id=None, workspace_id=source.workspace_id,
                     target_type="movie_source", target_id=source.id,
                     details={"bytes": source.bytes, "duration_seconds": round(source.duration_seconds or 0),
                              "moved_from_inbox": moved})
        movie_sources.notify(db, source, "movie_source.ready")
    log_event(logger, "movie_source_ready", **claim.fields, bytes=size, moved_from_inbox=moved,
              duration_ms=round((time.monotonic() - started) * 1000))
    _after_upload(claim, local, md5)


def _after_upload(claim: SourceClaim, local: Path, md5: str | None) -> None:
    """Delete the local copy, or keep it as the scratch copy (the first review then needs no download)."""
    folder = movie_sources.import_dir(claim.root, claim.source_id)
    if not movie_sources.settings().delete_local_temp and local.is_file():
        work = movie_sources.work_dir(claim.root, claim.source_id)
        work.mkdir(parents=True, exist_ok=True)
        _move(local, work / local.name)
        (work / "source.ok").write_text(md5 or "", encoding="ascii")
    movie_media.remove_tree(folder)


def _delete(Session, claim: SourceClaim, *, drive_factory) -> None:
    with Session() as db:
        source = db.get(MovieSource, claim.source_id)
        file_id, folder_id, inbox_id = source.drive_file_id, source.drive_folder_id, source.drive_import_id
    outcome = "none"
    if file_id or inbox_id:
        with drive_factory() as drive:
            if file_id:
                outcome = "removed" if drive.remove(file_id) else "already_gone"
                if folder_id:
                    try:
                        drive.remove(folder_id)  # the source's own folder, now empty
                    except google_drive.DriveError:
                        logger.warning("could not remove a movie source folder", exc_info=True)
            else:
                # Never moved out of the inbox: the file was handed over for this source, so it goes too.
                try:
                    meta = drive.file(inbox_id)
                    inbox = drive.inbox_folder(claim.workspace_id)
                    if inbox and inbox in (meta.get("parents") or []) and not meta.get("trashed"):
                        outcome = "removed" if drive.remove(inbox_id) else "already_gone"
                except google_drive.DriveError as exc:
                    if exc.code != "not_found":
                        raise
                    outcome = "already_gone"
    movie_media.remove_tree(movie_sources.import_dir(claim.root, claim.source_id))
    movie_media.remove_tree(movie_sources.work_dir(claim.root, claim.source_id))
    now = _now()
    with Session.begin() as db:
        source = movie_sources.live(db, claim.source_id, claim.token)
        if source is None:
            raise LeaseLost()
        source.deleted_at = now
        source.upload_session_ciphertext = source.url_ciphertext = None
        _advance(source, "deleted", now)
        audit.record(db, "movie_source.deleted", actor_id=None, workspace_id=source.workspace_id,
                     target_type="movie_source", target_id=source.id, details={"drive": outcome})
    log_event(logger, "movie_source_deleted", **claim.fields, drive=outcome)


# =================================================================================================================
# Step jobs
# =================================================================================================================

@dataclass
class JobClaim:
    job_id: str
    token: str
    payload: dict
    workspace_id: str
    run_id: str
    step_id: str
    workflow_id: str | None
    attempt: int
    media: Path
    scratch: Path

    @property
    def fields(self) -> dict:
        return {"workspace_id": self.workspace_id, "workflow_id": self.workflow_id, "run_id": self.run_id,
                "step_id": self.step_id, "job_id": self.job_id, "kind": self.payload.get("kind")}

    @property
    def source_id(self) -> str:
        return str(self.payload.get("movie_source_id") or "")


def _lock_run(db, run_id) -> WorkflowRun:
    # Same lock order as the executor, reconciliation and the other workers: run, then account.
    db.execute(update(WorkflowRun).where(WorkflowRun.id == run_id).values(status=WorkflowRun.status))
    return db.get(WorkflowRun, run_id)


def _live(db, claim: JobClaim):
    """(job, run, step) while the lease is live and the step still waits for this job, else None."""
    job = jobs.live_lease(db, job_id=claim.job_id, lease_token=claim.token)
    if job is None or db.get(CreditReconciliation, job.id) is not None:
        return None
    run = _lock_run(db, job.run_id)
    step = db.get(WorkflowRunStep, job.step_id)
    return (job, run, step) if step.status in (QUEUED, RUNNING) else None


def _save(step, output: dict) -> None:
    step.output = json.dumps(output, separators=(",", ":"), ensure_ascii=False)


def _ticker(Session, claim: JobClaim, *, show: bool = True) -> Throttle:
    """Keeps the job's lease and (``show``) the step's progress line up to date during long local work."""
    def tick(detail: str | None = None, progress: dict | None = None) -> None:
        with Session.begin() as db:
            if not jobs.extend_lease(db, job_id=claim.job_id, lease_token=claim.token,
                                     lease_seconds=JOB_LEASE_SECONDS):
                raise LeaseLost()
            if show and (detail or progress):
                _lock_run(db, claim.run_id)
                step = db.get(WorkflowRunStep, claim.step_id)
                if step.status == RUNNING:
                    output = step_output(step)
                    if progress:
                        output["progress"] = progress
                    step.detail = detail or step.detail
                    _save(step, output)
    return Throttle(tick)


def run_job(*, session_factory=None, worker_id: str | None = None, drive_factory=None,
            provider_factory=create_text_provider, runner=subprocess.run) -> bool:
    """Claim and run one movie step job; False when none is due."""
    Session = session_factory or _default_session_factory()
    worker_id = worker_id or f"movie-{os.getpid()}"
    drive_factory = drive_factory or google_drive.DriveClient
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=JOB_LEASE_SECONDS,
                                      logical_key_prefix="movie:")
        if not claimed:
            return False
        job = claimed[0]
        run = _lock_run(db, job.run_id)
        step = db.get(WorkflowRunStep, job.step_id)
        claim = JobClaim(job.id, job.lease_token, job.payload, job.workspace_id, job.run_id, job.step_id,
                         run.workflow_id if run else None, job.attempt_count, storage.media_root(db),
                         movie_sources.scratch_root(db))
        kind = job.payload.get("kind")
        log_event(logger, "job_claimed", **claim.fields, worker_id=worker_id, attempt=job.attempt_count)
        if (kind not in JOB_KINDS or step.status not in (QUEUED, RUNNING)
                or db.get(CreditReconciliation, job.id) is not None):
            jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="invalid_step_state")
            log_event(logger, "job_failed", level=logging.WARNING, **claim.fields, error_code="invalid_step_state")
            return True
        if kind == "vision.analyze":
            action = _vision_begin(db, job, step)
            if action is None:
                return True
        else:
            action = "run"
            step.status = RUNNING
            step.detail = PREPARE_RUNNING_DETAIL if kind == "movie.prepare" else \
                CLIPS_RUNNING_DETAIL.format(done=0, total=len(job.payload.get("clips") or []))
    started = time.monotonic()
    try:
        if kind == "vision.analyze":
            _vision(Session, claim, action, provider_factory=provider_factory, drive_factory=drive_factory,
                    runner=runner)
        elif claim.attempt > MAX_JOB_ATTEMPTS:
            raise JobError("attempts_exhausted", "The job was interrupted too many times")
        elif kind == "movie.prepare":
            _prepare(Session, claim, drive_factory=drive_factory, runner=runner)
        else:
            _extract(Session, claim, drive_factory=drive_factory, runner=runner)
    except LeaseLost:
        log_event(logger, "job_lease_lost", level=logging.WARNING, **claim.fields)
    except Exception as exc:  # noqa: BLE001 - recorded on the step with a stable code
        error = _job_error(exc)
        if not isinstance(exc, (JobError, render.RenderError, movie_media.MovieMediaError, google_drive.DriveError)):
            logger.exception("movie job %s failed unexpectedly", claim.job_id)
        _job_failed(Session, claim, error, started)
    return True


def _job_error(exc: Exception) -> JobError:
    if isinstance(exc, JobError):
        return exc
    if isinstance(exc, render.RenderError):
        return JobError(exc.code, str(exc), category=exc.category)
    if isinstance(exc, movie_media.MovieMediaError):
        return JobError(exc.code, str(exc))
    if isinstance(exc, google_drive.DriveError):
        return JobError(f"drive_{exc.code}", str(exc), retry_after=60 if exc.retryable else None,
                        category="provider_unavailable" if exc.retryable else "configuration_error")
    return JobError("worker_error", f"Movie worker error: {type(exc).__name__}", category="generation_failed")


def _job_failed(Session, claim: JobClaim, error: JobError, started: float) -> None:
    """Retry later (a busy or unreachable download) or fail the step; nothing here is paid."""
    with Session.begin() as db:
        live = _live(db, claim)
        if not live:
            return
        _, run, step = live
        if error.retry_after and claim.attempt < MAX_JOB_ATTEMPTS:
            step.status, step.detail = QUEUED, JOB_RETRY_DETAIL
            if jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=error.code,
                             retry_delay_seconds=error.retry_after):
                log_event(logger, "job_retry_scheduled", **claim.fields, attempt=claim.attempt,
                          delay_seconds=error.retry_after, error_code=error.code)
            return
        if not jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=error.code):
            return
        detail = PREPARE_FAILED_DETAIL if claim.payload.get("kind") == "movie.prepare" else CLIPS_FAILED_DETAIL
        default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step, NodeExecutionResult.failed(
            NodeError(error.code, str(error)[:300], False, error.category), detail, output=step_output(step)))
    log_event(logger, "job_failed", level=logging.WARNING, **claim.fields, error_code=error.code,
              duration_ms=round((time.monotonic() - started) * 1000))


# --- the scratch copy ---------------------------------------------------------------------------------------------

def _acquire(lock: Path) -> bool:
    """An exclusive lock file (one download of a movie at a time across worker processes); stale after hours."""
    for _ in (1, 2):
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            return False
        with os.fdopen(handle, "w") as stream:
            stream.write(str(os.getpid()))
        return True
    return False


def _scratch_ok(target: Path, marker: Path, md5: str | None, size: int | None) -> bool:
    try:
        return (target.is_file() and marker.is_file() and target.stat().st_size == size
                and marker.read_text(encoding="ascii").strip() == (md5 or ""))
    except (OSError, ValueError):
        return False


def ensure_scratch(Session, claim: JobClaim, *, drive_factory, tick: Throttle | None = None) -> Path:
    """The local copy of the job's movie source: downloaded from Drive once, checked, then shared by every step."""
    with Session() as db:
        source = db.get(MovieSource, claim.source_id) if claim.source_id else None
        if (source is None or source.workspace_id != claim.workspace_id or source.status not in movie_sources.USABLE
                or not source.drive_file_id):
            raise JobError("source_expired", "The movie source is no longer available")
        file_id, md5, size, name = source.drive_file_id, source.checksum_md5, source.bytes, \
            f"source{movie_sources.extension_for(source)}"
    folder = movie_sources.work_dir(claim.scratch, claim.source_id)
    target, marker = folder / name, folder / "source.ok"
    if _scratch_ok(target, marker, md5, size):
        os.utime(marker)  # last use: the sweep keeps a copy that is still being read
        return target
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".download.lock"
    if not _acquire(lock):
        raise JobError("scratch_busy", "Another worker is downloading this movie", retry_after=30)
    try:
        if _scratch_ok(target, marker, md5, size):
            return target
        marker.unlink(missing_ok=True)
        partial = folder / "source.part"
        hashes = movie_media.Hashes()
        if partial.exists():
            if partial.stat().st_size > (size or 0):
                partial.unlink()
            else:
                hashes.feed_file(partial)  # resumed: the bytes already here count towards the checksum
        started = time.monotonic()
        total_mb = max(1, round((size or 0) / 1024 ** 2))

        def progress(written: int) -> None:
            if tick is not None:
                os.utime(lock)
                tick(PREPARE_DOWNLOAD_DETAIL.format(done=round(written / 1024 ** 2), total=total_mb),
                     {"stage": "download", "done": round(written / 1024 ** 2), "total": total_mb})

        with drive_factory() as drive:
            written = drive.download(file_id, partial, limit=size or 1, on_chunk=hashes.update, progress=progress)
        if written != size or (md5 and hashes.md5.hexdigest() != md5):
            partial.unlink(missing_ok=True)
            raise JobError("checksum_mismatch", "The downloaded movie does not match its checksum", retry_after=60)
        partial.replace(target)
        marker.write_text(md5 or "", encoding="ascii")
        log_event(logger, "movie_scratch_downloaded", **claim.fields, movie_source_id=claim.source_id, bytes=written,
                  duration_ms=round((time.monotonic() - started) * 1000))
        return target
    finally:
        lock.unlink(missing_ok=True)


# --- Prepare Movie ------------------------------------------------------------------------------------------------

def _prepare(Session, claim: JobClaim, *, drive_factory, runner) -> None:
    payload, started = claim.payload, time.monotonic()
    ffmpeg, ffprobe = render.tools()
    if not ffmpeg or not ffprobe:
        raise JobError("ffmpeg_missing", "FFmpeg or ffprobe is not installed on the server")
    with Session() as db:
        source = db.get(MovieSource, claim.source_id) if claim.source_id else None
        if source is None or source.workspace_id != claim.workspace_id:
            raise JobError("source_expired", "The movie source is no longer available")
        movie, duration, has_audio = movie_value(source), float(source.duration_seconds or 0), bool(source.audio_codec)
    if not has_audio:
        raise JobError("no_audio", "The movie has no sound track to transcribe")
    tick = _ticker(Session, claim)
    path = ensure_scratch(Session, claim, drive_factory=drive_factory, tick=tick)
    timeout = render.render_timeout_seconds()
    folder = movie_sources.run_dir(claim.scratch, claim.source_id, claim.run_id)
    tick(PREPARE_AUDIO_DETAIL, {"stage": "audio"}, force=True)
    audio = movie_media.extract_audio(ffmpeg, path, folder / f"audio-{claim.job_id[:8]}", timeout=timeout, run=runner)
    cuts = []
    if payload.get("scene_cuts", True):
        tick(PREPARE_CUTS_DETAIL, {"stage": "scene_cuts"}, force=True)
        cuts = movie_media.scene_cuts(ffmpeg, path, timeout=timeout, run=runner)[:MAX_CUTS]
    interval = int(payload.get("frame_interval") or movie_sources.settings().frame_interval_seconds)
    limit = int(payload.get("max_frames") or movie_sources.settings().max_frames)
    times = movie_media.frame_times(duration, interval=interval, max_frames=max(1, min(limit, 500)), cuts=cuts)
    frames = folder / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for done, frame in enumerate(times, 1):
        if not (frames / movie_media.frame_name(frame["index"])).is_file():
            movie_media.extract_frame(ffmpeg, path, frames, frame["time"], frame["index"], run=runner)
        tick(PREPARE_FRAMES_DETAIL.format(done=done, total=len(times)),
             {"stage": "frames", "done": done, "total": len(times)})
    movie["scene_cuts"] = cuts

    def result(entries):
        return NodeExecutionResult.completed(PREPARE_DONE_DETAIL.format(frames=len(times)), {
            "movie": movie, "audio_assets": entries, "frames": times, "frame_count": len(times),
            "scene_cut_count": len(cuts), "movie_source_id": claim.source_id,
            "progress": {"stage": "done", "done": len(times), "total": len(times)}})

    spec = {"filename": lambda asset_id: f"movie-audio-{asset_id[:8]}.mp3", "content_type": "audio/mpeg",
            "kind": storage.kind_for_node("movie_prepare"), "facts": {"duration": round(duration, 3)}}
    if _store_and_finish(Session, claim, [(audio, spec)], result):
        log_event(logger, "movie_prepared", **claim.fields, movie_source_id=claim.source_id, frames=len(times),
                  scene_cuts=len(cuts), duration_ms=round((time.monotonic() - started) * 1000))
    movie_media.remove_tree(audio.parent)


def _store_and_finish(Session, claim: JobClaim, files: list[tuple[Path, dict]],
                      make_result: Callable[[list[dict]], NodeExecutionResult]) -> bool:
    """Move each file into the workspace's media, record its asset and finish the step, in one transaction."""
    targets: list[Path] = []
    total = sum(path.stat().st_size for path, _ in files)
    try:
        with Session.begin() as db:
            live = _live(db, claim)
            if not live or not jobs.complete_job(db, job_id=claim.job_id, lease_token=claim.token):
                return False  # another worker owns the job now
            _, run, step = live
            storage.lock_workspace(db, claim.workspace_id)
            if not storage.has_room(db, claim.workspace_id, total):
                raise JobError("storage_limit_exceeded", "The workspace's media storage is full")
            folder = storage.file_in(claim.media, claim.workspace_id)
            folder.mkdir(parents=True, exist_ok=True)
            entries = []
            for path, spec in files:
                asset_id = str(uuid.uuid4())
                size = path.stat().st_size
                target = folder / asset_id
                _move(path, target)
                targets.append(target)
                filename = spec["filename"](asset_id)
                db.add(Asset(id=asset_id, workspace_id=claim.workspace_id, project_id=run.project_id, run_id=run.id,
                             step_id=step.id, provider="ffmpeg", model="local", filename=filename,
                             content_type=spec["content_type"], bytes=size, kind=spec["kind"],
                             movie_source_id=claim.source_id or None))
                entries.append({"id": asset_id, "asset_id": asset_id, "filename": filename,
                                "content_type": spec["content_type"], "provider": "ffmpeg", "model": "local",
                                **spec["facts"]})
            result = make_result(entries)
            output = step_output(step)
            output.pop("error", None)
            output.update(result.output or {})
            result.output = output
            result.asset_ids = tuple(entry["id"] for entry in entries)
            db.flush()
            default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step, result)
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise
    return True


# --- Extract Source Clips (from a movie source) ---------------------------------------------------------------------

def _extract(Session, claim: JobClaim, *, drive_factory, runner) -> None:
    payload, started = claim.payload, time.monotonic()
    requested = payload.get("clips") or []
    if not requested or len(requested) > MAX_MOVIE_CLIPS:
        raise JobError("invalid_request", "There are no clips to cut")
    ffmpeg, ffprobe = render.tools()
    if not ffmpeg or not ffprobe:
        raise JobError("ffmpeg_missing", "FFmpeg or ffprobe is not installed on the server")
    tick = _ticker(Session, claim)
    path = ensure_scratch(Session, claim, drive_factory=drive_factory, tick=tick)
    folder = movie_sources.run_dir(claim.scratch, claim.source_id, claim.run_id) / f"clips-{claim.job_id[:8]}"
    copyable = payload.get("content_type") == "video/mp4"
    try:
        cut = render.cut_clips(
            ffmpeg, ffprobe, requested, folder, lambda item: (path, copyable, {"movie_source_id": claim.source_id}),
            mode=payload.get("mode") or "copy_first", timeout=render.render_timeout_seconds(), run=runner,
            max_clips=MAX_MOVIE_CLIPS, max_seconds=120.0,
            progress=lambda done, total: tick(CLIPS_RUNNING_DETAIL.format(done=done, total=total),
                                              {"stage": "clips", "done": done, "total": total}))
        seconds = round(sum(facts["duration"] for _, facts in cut))

        def result(entries):
            return NodeExecutionResult.completed(CLIPS_DONE_DETAIL.format(count=len(entries), seconds=seconds), {
                "video_assets": entries, "clip_count": len(entries), "total_seconds": seconds,
                "movie_source_id": claim.source_id,
                "progress": {"stage": "done", "done": len(entries), "total": len(entries)}})

        files = [(clip, {"filename": lambda asset_id, number=number: f"clip-{number:02d}-{asset_id[:8]}.mp4",
                         "content_type": "video/mp4", "kind": storage.kind_for_node("extract_clips"), "facts": facts})
                 for number, (clip, facts) in enumerate(cut, 1)]
        if _store_and_finish(Session, claim, files, result):
            log_event(logger, "clips_extracted", **claim.fields, movie_source_id=claim.source_id, clip_count=len(cut),
                      copied=sum(1 for _, facts in cut if facts["cut"] == "copy"), seconds=seconds,
                      duration_ms=round((time.monotonic() - started) * 1000))
    finally:
        movie_media.remove_tree(folder)


# --- Visual Analysis (paid, one job per batch) ------------------------------------------------------------------

def _records(output: dict) -> dict:
    value = output.get("jobs")
    if not isinstance(value, dict):
        value = output["jobs"] = {}
    return value


def _vision_begin(db, job: WorkflowJob, step: WorkflowRunStep) -> str | None:
    """Claim-time bookkeeping: ``call``, ``uncertain`` (a call may have reached the provider) or None (closed)."""
    output = step_output(step)
    payload = job.payload
    record = _records(output).setdefault(job.id, {"operation": payload.get("operation"), "index": payload.get("index"),
                                                  "frames": len(payload.get("frames") or []), "status": "queued"})
    if record.get("status") not in ACTIVE_RECORDS:
        jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="invalid_step_state")
        return None
    progress = record.get("provider_job") if isinstance(record.get("provider_job"), dict) else {}
    action = "uncertain" if record["status"] == "submitting" and progress.get("submission_started_at") else "call"
    record["status"] = "submitting"
    step.status = RUNNING
    _save(step, output)
    return action


def _vision_live(db, claim: JobClaim):
    live = _live(db, claim)
    if not live:
        return None
    job, run, step = live
    output = step_output(step)
    record = _records(output).get(job.id)
    if not isinstance(record, dict) or record.get("status") not in ACTIVE_RECORDS:
        return None
    return job, run, step, output, record


def _frame_images(Session, claim: JobClaim, frames: list[dict], *, drive_factory, runner) -> list[TextImage]:
    """The batch's frames as JPEG images; frames missing from the run's folder are taken again from the movie."""
    folder = movie_sources.run_dir(claim.scratch, claim.source_id, claim.run_id) / "frames"
    missing = [frame for frame in frames if not (folder / movie_media.frame_name(frame["index"])).is_file()]
    if missing:
        ffmpeg, _ = render.tools()
        if not ffmpeg:
            raise JobError("ffmpeg_missing", "FFmpeg is not installed on the server")
        path = ensure_scratch(Session, claim, drive_factory=drive_factory, tick=_ticker(Session, claim, show=False))
        folder.mkdir(parents=True, exist_ok=True)
        for frame in missing:
            movie_media.extract_frame(ffmpeg, path, folder, float(frame["time"]), int(frame["index"]), run=runner)
    images = []
    for frame in frames:
        data = (folder / movie_media.frame_name(frame["index"])).read_bytes()
        if not 0 < len(data) <= 4 * 1024 * 1024:
            raise JobError("invalid_frame", "A sampled frame could not be read")
        images.append(TextImage(data, "image/jpeg"))
    return images


def _vision(Session, claim: JobClaim, action: str, *, provider_factory, drive_factory, runner) -> None:
    """One batch; an unexpected error refunds it only when the provider was certainly not called yet."""
    called = {"value": False}
    try:
        _vision_batch(Session, claim, action, called, provider_factory=provider_factory, drive_factory=drive_factory,
                      runner=runner)
    except LeaseLost:
        raise
    except Exception:  # noqa: BLE001 - recorded on the batch below
        logger.exception("vision job %s failed unexpectedly", claim.job_id)
        _vision_terminal(Session, claim, refund=not called["value"], code="worker_error",
                         detail=VISION_ATTENTION_DETAIL if called["value"] else VISION_FAILED_DETAIL)


def _vision_batch(Session, claim: JobClaim, action: str, called: dict, *, provider_factory, drive_factory,
                  runner) -> None:
    payload, started = claim.payload, time.monotonic()
    if action == "uncertain":
        _vision_terminal(Session, claim, refund=False, code="submission_unknown", detail=VISION_ATTENTION_DETAIL)
        return
    if claim.attempt > MAX_JOB_ATTEMPTS:
        _vision_terminal(Session, claim, refund=True, code="attempts_exhausted", detail=VISION_FAILED_DETAIL)
        return
    if issue := text_provider_config_issue(payload.get("provider") or ""):
        _vision_terminal(Session, claim, refund=True, code=issue[0], detail=issue[1])
        return
    frames = [frame for frame in payload.get("frames") or [] if isinstance(frame, dict)]
    try:
        images = _frame_images(Session, claim, frames, drive_factory=drive_factory, runner=runner)
    except (JobError, render.RenderError, google_drive.DriveError) as exc:
        error = _job_error(exc)
        if error.retry_after and claim.attempt < MAX_JOB_ATTEMPTS:
            _vision_requeue(Session, claim, delay=error.retry_after, code=error.code, detail=JOB_RETRY_DETAIL)
        else:
            _vision_terminal(Session, claim, refund=True, code=error.code, detail=VISION_FAILED_DETAIL)
        return
    if not _vision_mark(Session, claim):
        return
    called["value"] = True
    fields = {**claim.fields, "provider": payload.get("provider"), "model": payload.get("model"),
              "operation": payload.get("operation")}
    log_event(logger, "provider_request_started", **fields, frames=len(frames))
    try:
        provider = provider_factory(payload["provider"])
        try:
            result = provider.generate(model=payload["model"], prompt=vision_prompt(frames, payload.get("language") or "vi"),
                                       system_prompt=VISION_SYSTEM_PROMPT, temperature=0.2,
                                       max_tokens=VISION_MAX_TOKENS, response_format="json", images=tuple(images))
        finally:
            provider.close()
    except Exception as exc:  # noqa: BLE001 - every outcome is recorded below
        code = safe_error_code(getattr(exc, "code", None), fallback="submission_unknown")
        retryable = exc.retryable if isinstance(exc, TextProviderError) else True
        latency_ms = round((time.monotonic() - started) * 1000)
        if isinstance(exc, ProviderError):
            log_event(logger, "provider_request_failed", level=logging.WARNING, **fields, attempt=claim.attempt,
                      latency_ms=latency_ms, error=exc.describe())
        else:
            logger.exception("vision job %s failed unexpectedly", claim.job_id)
        if isinstance(exc, TextProviderError) and code == "rate_limited" and retryable \
                and claim.attempt < MAX_JOB_ATTEMPTS:
            _vision_requeue(Session, claim, delay=5 * 2 ** (claim.attempt - 1), code=code, detail=VISION_RETRY_DETAIL)
        elif isinstance(exc, TextProviderError) and code in VISION_DEFINITIVE:
            _vision_terminal(Session, claim, refund=True, code=code, detail=VISION_REJECTED_DETAIL,
                             category=getattr(exc, "category", None))
        else:
            _vision_terminal(Session, claim, refund=False, code=code, detail=VISION_ATTENTION_DETAIL,
                             category=getattr(exc, "category", None))
        return
    notes = parse_vision(result.text, frames)
    log_event(logger, "provider_request_completed", **fields, latency_ms=round((time.monotonic() - started) * 1000),
              usage=result.usage.as_dict(), notes=len(notes), frames=len(frames))
    _vision_success(Session, claim, notes, result)


def _vision_mark(Session, claim: JobClaim) -> bool:
    """Record that the provider call starts: a crash from here on holds the credits for reconciliation."""
    with Session.begin() as db:
        live = _vision_live(db, claim)
        if not live:
            return False
        _, _, step, output, record = live
        provider_progress(record, stage="submitting", started=True)
        _save(step, output)
        return True


def _vision_requeue(Session, claim: JobClaim, *, delay: int, code: str, detail: str) -> None:
    with Session.begin() as db:
        live = _vision_live(db, claim)
        if not live:
            return
        _, _, step, output, record = live
        record["status"] = "queued"
        record.pop("provider_job", None)  # refused, so sending it again is safe
        record["error_count"] = int(record.get("error_count") or 0) + 1
        step.detail = detail
        _save(step, output)
        if jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=code, retry_delay_seconds=delay):
            log_event(logger, "job_retry_scheduled", **claim.fields, attempt=claim.attempt, delay_seconds=delay,
                      error_code=code)


def _vision_terminal(Session, claim: JobClaim, *, refund: bool, code: str, detail: str,
                     category: str | None = None) -> None:
    """End one batch: refund a definite rejection or a local problem, else hold its credits for reconciliation."""
    if not (refund and (code in LOCAL_CODES or code.startswith("drive_"))):
        code = safe_error_code(code, fallback="worker_error" if refund else "provider_failed")
    category = category if category in CATEGORIES else error_category(code)
    payload = claim.payload
    with Session.begin() as db:
        live = _vision_live(db, claim)
        if not live or not jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=code):
            return
        job, run, step, output, record = live
        status = "failed" if refund else NEEDS_ATTENTION
        record.update(status=status, detail=detail[:300], error={"code": code, "category": category})
        provider_progress(record, stage=status)
        if refund:
            usage.post_credit(db, job.workspace_id, payload["credits"], "vision_refund", payload["refund_reference"])
            log_event(logger, "credit_refunded", **claim.fields, credits=payload["credits"],
                      reference=payload["refund_reference"])
        log_event(logger, "job_failed", level=logging.WARNING, **claim.fields, status=status, refund=refund,
                  error_code=code, category=category)
        _save(step, output)
        _vision_settle(db, run, step, output)


def _vision_success(Session, claim: JobClaim, notes: list[dict], result) -> None:
    payload = claim.payload
    with Session.begin() as db:
        live = _vision_live(db, claim)
        if not live or not jobs.complete_job(db, job_id=claim.job_id, lease_token=claim.token):
            return
        job, run, step, output, record = live
        provider_progress(record, stage="completed", accepted=True, status="completed",
                          request_id=result.raw_metadata.get("response_id"))
        record.update(status="succeeded", notes=notes, usage=result.usage.as_dict(), model=result.model)
        record.pop("error_count", None)
        if not db.scalar(select(UsageEvent.id).where(UsageEvent.reference == payload["usage_reference"])):
            db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=job.workspace_id,
                              tool=f"{payload['provider']}/vision",
                              units=max(1, result.usage.total_tokens or len(payload.get("frames") or [])),
                              credits=payload["credits"], reference=payload["usage_reference"], created_at=_now()))
        log_event(logger, "job_completed", **claim.fields, credits_charged=payload["credits"], notes=len(notes))
        _save(step, output)
        _vision_settle(db, run, step, output)


def _vision_settle(db, run: WorkflowRun, step: WorkflowRunStep, output: dict) -> None:
    """Update the step from its batches; once every batch has ended, finish it and continue the run."""
    job_rows = list(db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id)))
    recs = _records(output)
    finished = [record for record in recs.values() if isinstance(record, dict)
                and record.get("status") in ("succeeded", "failed", NEEDS_ATTENTION)]
    succeeded = [record for record in finished if record.get("status") == "succeeded"]
    total = int(output.get("expected") or len(job_rows))
    analysed = sum(int(record.get("frames") or 0) for record in succeeded)
    output["progress"] = {"stage": "vision", "done": len(finished), "total": total, "frames_done": analysed,
                          "frames_total": int(output.get("frame_count") or 0)}
    if any(job.state in ("queued", "leased") for job in job_rows):
        step.detail = VISION_RUNNING_DETAIL.format(done=len(finished), total=total, frames=analysed,
                                                   frame_total=int(output.get("frame_count") or 0))
        _save(step, output)
        return
    notes = sorted((note for record in succeeded for note in record.get("notes") or []),
                   key=lambda note: note.get("time") or 0)
    for record in recs.values():
        if isinstance(record, dict) and "notes" in record:
            record["note_count"] = len(record.pop("notes") or [])  # kept once, below
    output["visual"] = {"frames": notes, "frame_count": len(notes), "batches": total, "batches_succeeded":
                        len(succeeded), "provider": output.get("provider"), "model": output.get("model")}
    statuses = [recs.get(job.id, {}).get("status") for job in job_rows]
    uncertain = [recs[job.id] for job in job_rows if recs.get(job.id, {}).get("status") == NEEDS_ATTENTION]
    context = ExecutionContext.for_run(db, run, now=_now())
    if statuses and all(status == "succeeded" for status in statuses):
        result = NodeExecutionResult.completed(VISION_DONE_DETAIL.format(frames=len(notes)), output)
    elif not uncertain and succeeded and notes:
        # Batches the provider refused were refunded; the timeline is built from the frames that were described.
        result = NodeExecutionResult.completed(VISION_PARTIAL_DETAIL.format(done=len(succeeded), total=total), output)
    else:
        failed = uncertain or [recs.get(job.id, {}) for job in job_rows if recs.get(job.id, {}).get("status") != "succeeded"]
        error = failed[0].get("error") if failed and isinstance(failed[0].get("error"), dict) else {}
        code = str(error.get("code") or "provider_failed")
        category = error.get("category") if error.get("category") in CATEGORIES else error_category(code)
        node_error = NodeError(code, "One or more batches did not succeed", False, category)
        result = NodeExecutionResult(NEEDS_ATTENTION, VISION_ATTENTION_DETAIL, output, error=node_error) if uncertain \
            else NodeExecutionResult.failed(node_error, VISION_FAILED_DETAIL, output=output)
    log_event(logger, "vision_step_settled", workspace_id=run.workspace_id, workflow_id=run.workflow_id, run_id=run.id,
              step_id=step.id, status=result.status, batches=len(job_rows), succeeded=len(succeeded), notes=len(notes))
    default_executor.finish_step(context, step, result)


# =================================================================================================================
# Scratch sweep
# =================================================================================================================

def sweep_scratch(session_factory=None, *, force: bool = False, clock=time.time) -> int:
    """Remove local copies no running run needs; returns how many folders were removed."""
    moment = time.monotonic()
    if not force and _sweep["at"] is not None and moment - _sweep["at"] < SWEEP_INTERVAL_SECONDS:
        return 0
    _sweep["at"] = moment
    Session = session_factory or _default_session_factory()
    with Session() as db:
        root = movie_sources.scratch_root(db)
    current, removed, now = movie_sources.settings(), 0, clock()
    work_root, import_root = root / "movie-jobs", root / "imports"
    for folder in sorted(work_root.iterdir()) if work_root.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink():
            continue
        with Session() as db:
            source = db.get(MovieSource, folder.name)
            active = set(movie_sources.active_runs(db, folder.name)) if source is not None else set()
        if source is None or source.status in movie_sources.GONE:
            movie_media.remove_tree(folder)
            removed += 1
            continue
        for child in folder.iterdir():
            if child.is_dir() and not child.is_symlink() and child.name not in active:
                movie_media.remove_tree(child)  # a run that ended: its frames and audio are not needed any more
                removed += 1
        marker, lock = folder / "source.ok", folder / ".download.lock"
        try:
            idle = now - marker.stat().st_mtime if marker.exists() else now - folder.stat().st_mtime
        except OSError:
            continue
        if current.delete_scratch and not active and not lock.exists() and idle > SCRATCH_IDLE_SECONDS:
            movie_media.remove_tree(folder)
            removed += 1
    for folder in sorted(import_root.iterdir()) if import_root.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink():
            continue
        with Session() as db:
            source = db.get(MovieSource, folder.name)
        keep = source is not None and (source.status in movie_sources.WORKING
                                       or (source.status == "failed" and source.failure_stage == "upload"))
        if not keep:
            movie_media.remove_tree(folder)
            removed += 1
    if removed:
        log_event(logger, "movie_scratch_swept", folders=removed)
    return removed


# =================================================================================================================
# Process
# =================================================================================================================

def check(out=print) -> int:
    """Report the tools and settings the worker needs; nothing is imported, uploaded or deleted."""
    ffmpeg, ffprobe = render.tools()
    current = movie_sources.settings()
    drive = google_drive.config().problem()
    root = movie_sources.import_root()
    out(f"ffmpeg: {'ok' if ffmpeg else 'missing'}")
    out(f"ffprobe: {'ok' if ffprobe else 'missing'}")
    out(f"Movie sources: {'enabled' if current.enabled else 'disabled'}")
    out(f"Google Drive: {'configured' if not drive else drive}")
    out(f"Import folder: {'exists' if root and root.is_dir() else 'missing'}")
    ready = bool(ffmpeg and ffprobe and current.enabled and not drive)
    out("Movie worker ready." if ready else "Not ready.")
    return 0 if ready else 1


def _lane(name: str, work: Callable[[], bool], stop: threading.Event, once: bool, errors: dict) -> None:
    while not stop.is_set():
        try:
            worked = work()
            errors.pop(name, None)
        except Exception:  # noqa: BLE001 - a lane never dies; the heartbeat reports the error
            logger.exception("movie worker %s lane failed", name)
            errors[name] = time.monotonic()
            worked = False
        if once:
            return
        if not worked:
            stop.wait(2)


def main():
    parser = argparse.ArgumentParser(description="Import movie sources and run movie review jobs")
    parser.add_argument("--once", action="store_true", help="Do at most one piece of work in each lane, then exit")
    parser.add_argument("--check", action="store_true", help="Report FFmpeg, ffprobe and the settings, then exit")
    args = parser.parse_args()
    start_process(WORKER)
    if args.check:
        sys.exit(check())
    stop, errors = threading.Event(), {}

    def jobs_lane() -> bool:
        worked = run_job()
        sweep_scratch()
        return worked

    lanes = [threading.Thread(target=_lane, args=(name, work, stop, args.once, errors), name=f"movie-{name}",
                              daemon=True) for name, work in (("sources", run_source_work), ("jobs", jobs_lane))]
    for lane in lanes:
        lane.start()
    try:
        while any(lane.is_alive() for lane in lanes):
            heartbeat.beat(WORKER, status="error" if errors else "running",
                           detail=f"{', '.join(sorted(errors))} lane failed" if errors else None)
            if args.once:
                for lane in lanes:
                    lane.join()
                return
            time.sleep(5)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
