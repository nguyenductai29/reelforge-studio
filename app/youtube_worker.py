"""Upload approved MP4 assets to YouTube with resumable sessions.

Each publication chooses its visibility (private by default, or unlisted or
public) and tags; YouTube's reported upload status and visibility are stored.
"""
import argparse
from dataclasses import asdict
import os
import time

import httpx

from app import jobs, publications
from app.db import Session
from app.main import media_root
from app.models import Asset
from app.publishers import google_oauth, youtube
from app.runtime_env import start_process


MAX_TRANSIENT_ATTEMPTS = 6


def _transient_retry_delay(attempt_count: int, poll_seconds: int) -> int | None:
    if attempt_count >= MAX_TRANSIENT_ATTEMPTS:
        return None
    return min(3600, max(poll_seconds, 5) * (2 ** (attempt_count - 1)))


def _fail(publication_id, job_id, token, code, *, retry_delay_seconds=None, needs_attention=False,
          remote_id=None):
    with Session.begin() as db:
        recorded = publications.fail_publication(db, publication_id=publication_id, job_id=job_id,
            lease_token=token, error=code, retry_delay_seconds=retry_delay_seconds,
            needs_attention=needs_attention)
        if recorded and needs_attention and remote_id:
            db.get(publications.Publication, publication_id).remote_id = remote_id


def _connection_matches(workspace_id: str, expected_generation: str) -> bool:
    with Session() as db:
        connection = db.get(google_oauth.YouTubeConnection, workspace_id)
        return bool(connection and
                    google_oauth.connection_generation(connection) == expected_generation)


def run_one(*, client: httpx.Client | None = None, poll_seconds: int = 15,
            worker_id: str | None = None) -> bool:
    """Advance one YouTube upload without holding a DB transaction over network I/O."""
    worker_id = worker_id or f"youtube-{os.getpid()}"
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=900,
                                      logical_key_prefix="publish:youtube:")
        if not claimed:
            return False
        job = claimed[0]
        job_id, token, attempt_count = job.id, job.lease_token, job.attempt_count
        publication_id = job.payload["publication_id"]
        publication = db.get(publications.Publication, publication_id)
        if publication is None or publication.job_id != job_id or publication.channel != "youtube":
            jobs.fail_job(db, job_id=job_id, lease_token=token, error="invalid_publication")
            return True
        asset = db.get(Asset, publication.asset_id)
        if asset is None or asset.workspace_id != publication.workspace_id or asset.content_type != "video/mp4":
            publications.fail_publication(db, publication_id=publication_id, job_id=job_id,
                lease_token=token, error="asset_unavailable")
            return True
        expected_generation = job.payload.get("connection_generation")
        connection = db.get(google_oauth.YouTubeConnection, publication.workspace_id)
        if (not isinstance(expected_generation, str) or not expected_generation or connection is None
                or google_oauth.connection_generation(connection) != expected_generation):
            publications.fail_publication(db, publication_id=publication_id, job_id=job_id,
                lease_token=token, error="connection_changed", needs_attention=True)
            return True
        file_path = media_root(db) / publication.workspace_id / asset.id
        title, description, workspace_id = publication.title, publication.description, publication.workspace_id
        privacy, tags = publication.privacy_status or "private", tuple(publications.publication_tags(publication))
        had_session = publication.upload_session_ciphertext is not None
        was_queued = publication.state == "queued"
    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False)
    try:
        try:
            config = google_oauth.GoogleOAuthConfig.from_environment()
            with Session() as db:
                access_token = google_oauth.get_access_token(db, config, workspace_id=workspace_id,
                    client=client, expected_generation=expected_generation)
            with Session() as db:
                publication = db.get(publications.Publication, publication_id)
                session_data = publications.load_upload_session(publication,
                    encryption_key=config.encryption_key)
        except google_oauth.OAuthError as exc:
            delay = _transient_retry_delay(attempt_count, poll_seconds) if exc.retryable else None
            _fail(publication_id, job_id, token, f"oauth_or_session:{exc.code}",
                  retry_delay_seconds=delay,
                  needs_attention=not exc.retryable or (exc.retryable and delay is None and had_session))
            return True
        except ValueError as exc:
            _fail(publication_id, job_id, token, f"oauth_or_session:{type(exc).__name__}",
                  needs_attention=True)
            return True

        request = youtube.YouTubeUploadRequest(file_path=file_path, title=title, description=description,
                                               contains_synthetic_media=True, privacy_status=privacy, tags=tags)
        if not _connection_matches(workspace_id, expected_generation):
            _fail(publication_id, job_id, token, "connection_changed", needs_attention=True)
            return True
        if session_data is None:
            if had_session or not was_queued:
                _fail(publication_id, job_id, token, "submission_without_saved_session", needs_attention=True)
                return True
            with Session.begin() as db:
                if not publications.mark_uploading(db, publication_id=publication_id,
                                                   job_id=job_id, lease_token=token):
                    return True
            try:
                session = youtube.start_private_upload(request, access_token, client=client)
            except youtube.YouTubeUploadError as exc:
                delay = _transient_retry_delay(attempt_count, poll_seconds) if exc.retryable else None
                _fail(publication_id, job_id, token, f"start:{exc.code}",
                      retry_delay_seconds=delay,
                      needs_attention=not exc.retryable and exc.code == "invalid_response")
                return True
            with Session.begin() as db:
                if publications.save_upload_session(db, publication_id=publication_id,
                        job_id=job_id, lease_token=token, session=asdict(session),
                        encryption_key=config.encryption_key):
                    jobs.fail_job(db, job_id=job_id, lease_token=token,
                                  error="upload_session_saved", retry_delay_seconds=poll_seconds)
            return True

        try:
            if not _connection_matches(workspace_id, expected_generation):
                _fail(publication_id, job_id, token, "connection_changed", needs_attention=True)
                return True
            session = youtube.UploadSession(**session_data)
            result = youtube.upload_private_video(request, access_token, client=client, session=session)
        except youtube.YouTubeUploadError as exc:
            delay = _transient_retry_delay(attempt_count, poll_seconds) if exc.retryable else None
            _fail(publication_id, job_id, token, f"upload:{exc.code}",
                  retry_delay_seconds=delay,
                  needs_attention=(exc.retryable and delay is None) or
                      exc.code in {"expired_session", "invalid_response", "unexpected_visibility"},
                  remote_id=exc.remote_id)
            return True
        except Exception as exc:
            _fail(publication_id, job_id, token, f"upload:{type(exc).__name__}", needs_attention=True)
            return True
        with Session.begin() as db:
            publications.finish_publication(db, publication_id=publication_id, job_id=job_id,
                                            lease_token=token, remote_id=result.video_id,
                                            upload_status=result.upload_status, privacy_status=result.privacy_status)
        return True
    finally:
        if owned_client:
            client.close()


def main():
    parser = argparse.ArgumentParser(description="Process approved YouTube uploads")
    parser.add_argument("--once", action="store_true", help="Process at most one due upload")
    args = parser.parse_args()
    start_process("youtube_worker")
    while True:
        worked = run_one()
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
