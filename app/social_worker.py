"""Upload approved videos to TikTok and Facebook through their official APIs, one step per claim.

Run ``python -m app.social_worker`` next to the API (``--once`` advances one due
job). It claims jobs keyed ``publish:tiktok:`` and ``publish:facebook:``; each
publication has its own job, so one platform's failure never affects another.
Every step saves its progress (encrypted with the token key) before the next
network call and hands the job back to the queue, so a restart resumes where it
stopped and no network call happens inside a database transaction.

* **TikTok** (Content Posting API, ``video.upload``): initialize an inbox upload,
  send the chunks, then poll the status. ``SEND_TO_USER_INBOX`` is recorded as
  succeeded with ``remote_status = "sent_to_inbox"`` and no ``published_at``: the
  video is a draft in the creator's TikTok inbox, and the creator finishes the post
  in the TikTok app. Nothing is posted directly.
* **Facebook** (Page Reels): start, transfer the file, finish with
  ``video_state`` PUBLISHED (visibility ``public``) or DRAFT (``private``), then
  poll the status until Meta reports it published (or processed, for a draft).

A failure before any media was sent can be retried from the Publishing page. Once
media was sent, an unclear outcome becomes ``needs_attention`` and is never sent
again automatically (a duplicate post is worse than a manual check).
"""
import argparse
from dataclasses import asdict
import logging
import os
from pathlib import Path
import re
import shutil
import time

import httpx

from app import heartbeat, jobs, publications, storage
from app.logs import log_event
from app.media_paths import media_root
from app.models import Asset
from app.publishers import channel_oauth, facebook, tiktok
from app.runtime_env import start_process
from app import system_config

logger = logging.getLogger(__name__)

LEASE_SECONDS = 900
POLL_SECONDS = 15
MAX_POLLS = 240
MAX_TRANSIENT_ERRORS = 6
PREFIXES = ("publish:tiktok:", "publish:facebook:")
TEMP_FOLDER = ".publish-tmp"
_REASON = re.compile(r"[^a-z0-9_]+")


def _default_session_factory():
    from app.db import Session
    return Session


def _delay(errors: int, poll_seconds: int) -> int | None:
    if errors >= MAX_TRANSIENT_ERRORS:
        return None
    return min(3600, max(poll_seconds, 5) * (2 ** max(0, errors - 1)))


def _reason(value) -> str:
    return _REASON.sub("_", str(value or "unknown").lower()).strip("_")[:60] or "unknown"


class _Job:
    """One claimed upload step and the facts copied out of its transaction."""

    def __init__(self, Session, job, publication, path: Path, config, poll_seconds: int):
        self.Session = Session
        self.job_id, self.token, self.attempt = job.id, job.lease_token, job.attempt_count
        self.generation = job.payload.get("connection_generation")
        self.publication_id, self.channel = publication.id, publication.channel
        self.workspace_id = publication.workspace_id
        self.title, self.description = publication.title, publication.description
        self.tags, self.privacy = publications.publication_tags(publication), publication.privacy_status
        self.had_session = publication.upload_session_ciphertext is not None
        self.was_queued = publication.state == "queued"
        self.path, self.config, self.poll_seconds = path, config, poll_seconds

    @property
    def fields(self) -> dict:
        return {"job_id": self.job_id, "publication_id": self.publication_id, "workspace_id": self.workspace_id,
                "channel": self.channel}

    def fail(self, code: str, *, retry_delay: int | None = None, needs_attention: bool = False,
             session: dict | None = None, clear_session: bool = False) -> None:
        with self.Session.begin() as db:
            if session is not None:
                publications.save_upload_session(db, publication_id=self.publication_id, job_id=self.job_id,
                                                 lease_token=self.token, session=session,
                                                 encryption_key=self.config.encryption_key)
            recorded = publications.fail_publication(db, publication_id=self.publication_id, job_id=self.job_id,
                                                     lease_token=self.token, error=code,
                                                     retry_delay_seconds=retry_delay, needs_attention=needs_attention)
            if recorded and clear_session:
                db.get(publications.Publication, self.publication_id).upload_session_ciphertext = None
        log_event(logger, "publication_failed" if retry_delay is None else "publication_retry_scheduled",
                  level=logging.WARNING, **self.fields, error_code=code, retry_delay=retry_delay,
                  needs_attention=needs_attention)

    def save(self, session: dict, *, delay: int) -> None:
        """Persist progress, then give the job back to the queue for the next step."""
        with self.Session.begin() as db:
            if publications.save_upload_session(db, publication_id=self.publication_id, job_id=self.job_id,
                                                lease_token=self.token, session=session,
                                                encryption_key=self.config.encryption_key):
                publications.continue_upload(db, publication_id=self.publication_id, job_id=self.job_id,
                                             lease_token=self.token, delay_seconds=delay)

    def start(self) -> bool:
        """Mark the publication as uploading before the first request that creates anything remotely."""
        if self.had_session or not self.was_queued:
            self.fail("submission_without_saved_session", needs_attention=True)
            return False
        with self.Session.begin() as db:
            return publications.mark_uploading(db, publication_id=self.publication_id, job_id=self.job_id,
                                               lease_token=self.token)

    def finish(self, remote_id: str, *, status: str, privacy: str, published: bool) -> None:
        with self.Session.begin() as db:
            publications.finish_publication(db, publication_id=self.publication_id, job_id=self.job_id,
                                            lease_token=self.token, remote_id=remote_id, upload_status=status,
                                            privacy_status=privacy, published=published)
        log_event(logger, "publication_succeeded", **self.fields, remote_status=status, published=published)


# TikTok ------------------------------------------------------------------------

def approved_tiktok_scopes() -> frozenset[str]:
    """Scopes the TikTok app is approved for (``TIKTOK_APPROVED_SCOPES``, comma-separated)."""
    value = system_config.env("TIKTOK_APPROVED_SCOPES", "user.info.basic,video.upload")
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _tiktok(work: _Job, session: dict | None, client: httpx.Client) -> None:
    try:
        with work.Session() as db:
            token, granted = channel_oauth.tiktok_access_token(db, work.config, workspace_id=work.workspace_id,
                                                               client=client, expected_generation=work.generation)
    except channel_oauth.ChannelOAuthError as exc:
        delay = _delay(work.attempt, work.poll_seconds) if exc.retryable else None
        work.fail(f"oauth:{exc.code}", retry_delay=delay, needs_attention=work.had_session and delay is None)
        return
    api = tiktok.TikTokClient(token, approved_scopes=approved_tiktok_scopes(), granted_scopes=granted,
                              http_client=client)
    if session is None:
        if not work.start():
            return
        try:
            upload = api.init_draft_upload(work.path)
        except tiktok.TikTokError as exc:
            if exc.code == "submission_unknown":
                work.fail("start:submission_unknown", needs_attention=True)
            else:
                work.fail(f"start:{_reason(exc.code)}",
                          retry_delay=_delay(work.attempt, work.poll_seconds) if exc.retryable else None)
            return
        work.save({"stage": "upload", "tiktok": asdict(upload), "polls": 0, "errors": 0}, delay=0)
        return
    upload = tiktok.UploadSession(**session["tiktok"])
    errors = int(session.get("errors") or 0)
    if session.get("stage") == "upload":
        if upload.next_chunk_index < upload.total_chunk_count:
            try:
                upload = api.upload_next_chunk(upload, work.path)
            except tiktok.TikTokError as exc:
                delay = _delay(errors + 1, work.poll_seconds) if exc.code == "upload_unknown" else None
                work.fail(f"upload:{_reason(exc.code)}", retry_delay=delay, needs_attention=delay is None,
                          session={**session, "errors": errors + 1})
                return
            work.save({**session, "tiktok": asdict(upload), "errors": 0}, delay=0)
            return
        work.save({**session, "stage": "status"}, delay=work.poll_seconds)
        return
    try:
        status = api.fetch_status(upload.publish_id)
    except tiktok.TikTokError as exc:
        delay = _delay(errors + 1, work.poll_seconds) if exc.retryable else None
        if delay is None:
            work.fail(f"status:{_reason(exc.code)}", needs_attention=True)
        else:
            work.save({**session, "errors": errors + 1}, delay=delay)
        return
    if status.state == "awaiting_creator":
        work.finish(upload.publish_id, status="sent_to_inbox", privacy="private", published=False)
    elif status.state == "published":
        work.finish(upload.publish_id, status="published", privacy="private", published=True)
    elif status.state == "failed":
        # TikTok discarded the upload: nothing exists remotely, so a retry is safe.
        work.fail(f"remote_failed:{_reason(status.fail_reason)}", clear_session=True)
    elif int(session.get("polls") or 0) >= MAX_POLLS:
        work.fail("status:timeout", needs_attention=True)
    else:
        work.save({**session, "polls": int(session.get("polls") or 0) + 1, "errors": 0}, delay=work.poll_seconds)


# Facebook ----------------------------------------------------------------------

def _facebook(work: _Job, session: dict | None, client: httpx.Client) -> None:
    try:
        with work.Session() as db:
            _, page_token = channel_oauth.facebook_page_token(db, work.config, workspace_id=work.workspace_id,
                                                              expected_generation=work.generation)
    except channel_oauth.ChannelOAuthError as exc:
        work.fail(f"oauth:{exc.code}", needs_attention=work.had_session)
        return
    request = facebook.FacebookReelRequest(
        file_path=work.path, title=work.title, description=publications.social_text(work.description, work.tags),
        video_state="PUBLISHED" if work.privacy == "public" else "DRAFT")
    if session is None:
        if not work.start():
            return
        try:
            reel = facebook.start_reel_upload(request, page_token, client=client)
        except facebook.FacebookReelError as exc:
            # Starting only reserves an empty video ID; nothing is shown on the Page.
            work.fail(f"start:{_reason(exc.code)}",
                      retry_delay=_delay(work.attempt, work.poll_seconds) if exc.retryable else None)
            return
        work.save({"stage": "transfer", "facebook": asdict(reel), "polls": 0, "errors": 0}, delay=0)
        return
    reel = facebook.ReelSession(**session["facebook"])
    errors = int(session.get("errors") or 0)
    stage = session.get("stage")
    if stage == "transfer":
        try:
            if errors:
                # Meta documents no partial resume: check whether the bytes arrived before sending them again.
                current = facebook.get_reel_status(reel, page_token, client=client)
                if current.uploading_status == "complete":
                    work.save({**session, "stage": "finish", "errors": 0}, delay=0)
                    return
            facebook.upload_reel_file(request, reel, page_token, client=client)
        except facebook.FacebookReelError as exc:
            delay = _delay(errors + 1, work.poll_seconds) if exc.retryable else None
            work.fail(f"upload:{_reason(exc.code)}", retry_delay=delay, needs_attention=delay is None,
                      session={**session, "errors": errors + 1})
            return
        work.save({**session, "stage": "finish", "errors": 0}, delay=0)
        return
    if stage == "finish":
        try:
            facebook.publish_reel(request, reel, page_token, client=client)
        except facebook.FacebookReelError as exc:
            # A lost answer may still have published the Reel: never ask twice.
            work.fail(f"finish:{_reason(exc.code)}", needs_attention=True)
            return
        work.save({**session, "stage": "status", "errors": 0}, delay=work.poll_seconds)
        return
    try:
        status = facebook.get_reel_status(reel, page_token, client=client)
    except facebook.FacebookReelError as exc:
        delay = _delay(errors + 1, work.poll_seconds) if exc.retryable else None
        if delay is None:
            work.fail(f"status:{_reason(exc.code)}", needs_attention=True)
        else:
            work.save({**session, "errors": errors + 1}, delay=delay)
        return
    phases = (status.uploading_status, status.processing_status, status.publishing_status)
    if status.video_status == "error" or "error" in phases:
        work.fail("remote_failed:facebook_processing", needs_attention=True)
    elif request.video_state == "PUBLISHED" and status.publishing_status == "complete":
        work.finish(reel.video_id, status="published", privacy="public", published=True)
    elif request.video_state == "DRAFT" and (status.video_status == "ready" or status.processing_status == "complete"):
        work.finish(reel.video_id, status="draft", privacy="private", published=False)
    elif int(session.get("polls") or 0) >= MAX_POLLS:
        work.fail("status:timeout", needs_attention=True)
    else:
        work.save({**session, "polls": int(session.get("polls") or 0) + 1, "errors": 0}, delay=work.poll_seconds)


# Loop --------------------------------------------------------------------------

def _mp4_link(source: Path, folder: Path, job_id: str) -> Path:
    """The asset under an ``.mp4`` name the publisher libraries accept: a hard link, else a copy."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{job_id}.mp4"
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)
    return target


def run_one(*, client: httpx.Client | None = None, poll_seconds: int = POLL_SECONDS, worker_id: str | None = None,
            session_factory=None) -> bool:
    """Advance one TikTok or Facebook upload; returns False when none is due."""
    Session = session_factory or _default_session_factory()
    worker_id = worker_id or f"social-{os.getpid()}"
    with Session.begin() as db:
        job = None
        for prefix in PREFIXES:
            claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=LEASE_SECONDS,
                                          logical_key_prefix=prefix)
            if claimed:
                job = claimed[0]
                break
        if job is None:
            return False
        publication = db.get(publications.Publication, job.payload.get("publication_id"))
        if (publication is None or publication.job_id != job.id or publication.channel not in ("tiktok", "facebook")
                or job.payload.get("channel") != publication.channel):
            jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="invalid_publication")
            return True
        asset = db.get(Asset, publication.asset_id)
        if asset is None or asset.workspace_id != publication.workspace_id or asset.content_type != "video/mp4":
            publications.fail_publication(db, publication_id=publication.id, job_id=job.id,
                                          lease_token=job.lease_token, error="asset_unavailable")
            return True
        connection = db.get(channel_oauth.ChannelConnection, (publication.workspace_id, publication.channel))
        expected = job.payload.get("connection_generation")
        if (not isinstance(expected, str) or connection is None
                or channel_oauth.connection_generation(connection) != expected):
            publications.fail_publication(db, publication_id=publication.id, job_id=job.id,
                                          lease_token=job.lease_token, error="connection_changed",
                                          needs_attention=publication.upload_session_ciphertext is not None)
            return True
        root = media_root(db)
        try:
            config = channel_oauth.ChannelConfig.from_environment(publication.channel)
        except channel_oauth.ChannelOAuthError:
            publications.fail_publication(db, publication_id=publication.id, job_id=job.id,
                                          lease_token=job.lease_token, error="oauth:not_configured",
                                          needs_attention=publication.upload_session_ciphertext is not None)
            return True
        source = storage.file_in(root, publication.workspace_id, asset.id)
        work = _Job(Session, job, publication, source, config, poll_seconds)
        try:
            session = publications.load_upload_session(publication, encryption_key=config.encryption_key)
        except ValueError:
            session = False
    if session is False:
        work.fail("session_unreadable", needs_attention=True)
        return True
    if not source.is_file():
        work.fail("asset_unavailable", needs_attention=work.had_session)
        return True
    log_event(logger, "publication_step", **work.fields, stage=(session or {}).get("stage", "start"))
    folder = root / TEMP_FOLDER
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False)
    try:
        work.path = _mp4_link(source, folder, work.job_id)
        (_tiktok if work.channel == "tiktok" else _facebook)(work, session, client)
    except Exception as exc:  # noqa: BLE001 - an unknown outcome is never retried blindly
        logger.exception("social upload step failed")
        work.fail(f"worker:{type(exc).__name__}", needs_attention=True)
    finally:
        (folder / f"{work.job_id}.mp4").unlink(missing_ok=True)
        if owned:
            client.close()
    return True


def main():
    parser = argparse.ArgumentParser(description="Process approved TikTok and Facebook uploads")
    parser.add_argument("--once", action="store_true", help="Advance at most one due upload")
    args = parser.parse_args()
    start_process("social_worker")
    while True:
        worked = run_one()
        heartbeat.beat("social_worker")
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
