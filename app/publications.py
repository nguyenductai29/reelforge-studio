"""Durable, workspace-scoped publication records for approved video assets.

The HTTP layer checks the requester's permissions. This module verifies the
approved run and asset lineage again before creating an idempotent publication
and its queue job in the caller's transaction. Workers must use the lease-
fenced helpers to persist resumable sessions and final results.

Metadata follows YouTube's limits (``validate_metadata``): a title of 1–100
characters, a description of at most 5,000 bytes, tags of at most 500
characters in total, no ``<`` or ``>``, and a visibility of ``private``,
``unlisted`` or ``public``. When a run has a completed Render step, only its
final MP4 can be published; runs without one keep publishing their clip.
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import jobs
from app.models import Asset, Base, WorkflowJob, WorkflowRun, WorkflowRunStep
from app.publishers import google_oauth
from app.publishers.youtube import MAX_TAGS_LENGTH, PRIVACY_STATUSES, tags_length


_CHANNEL_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_BYTES = 5000
MAX_TAG_CHARS = 100
_ANGLES = re.compile(r"[<>]")


class MetadataError(ValueError):
    """Invalid publication metadata; ``code`` and ``field`` say which value to fix."""

    def __init__(self, code: str, field: str, message: str):
        super().__init__(message)
        self.code = code
        self.field = field


def normalize_tags(tags: Any) -> list[str]:
    """Tags without "#", surrounding spaces, blanks or repeats (case-insensitive), in order."""
    if tags is None:
        return []
    if not isinstance(tags, (list, tuple)) or not all(isinstance(tag, str) for tag in tags):
        raise MetadataError("invalid_tags", "tags", "Tags must be a list of text values")
    seen, result = set(), []
    for tag in tags:
        clean = " ".join(tag.strip().lstrip("#").split())
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(clean)
    return result


def validate_metadata(title: Any, description: Any, tags: Any = None,
                      privacy_status: Any = "private") -> tuple[str, str, list[str], str]:
    """YouTube-compatible (title, description, tags, privacy), or ``MetadataError``."""
    if not isinstance(title, str) or not title.strip():
        raise MetadataError("invalid_title", "title", "A title is required")
    title = title.strip()
    if len(title) > MAX_TITLE_CHARS or "\n" in title or "\r" in title or _ANGLES.search(title):
        raise MetadataError("invalid_title", "title", "The title must be one line of at most 100 characters without < or >")
    if not isinstance(description, str):
        raise MetadataError("invalid_description", "description", "The description must be text")
    if len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES or _ANGLES.search(description):
        raise MetadataError("invalid_description", "description",
                            "The description must be at most 5000 bytes without < or >")
    tags = normalize_tags(tags)
    if (any(len(tag) > MAX_TAG_CHARS or _ANGLES.search(tag) or "," in tag for tag in tags)
            or tags_length(tags) > MAX_TAGS_LENGTH):
        raise MetadataError("invalid_tags", "tags",
                            "Tags must total at most 500 characters, each without commas, < or >")
    if privacy_status not in PRIVACY_STATUSES:
        raise MetadataError("invalid_privacy", "privacy_status", "Visibility must be private, unlisted or public")
    return title, description, tags, privacy_status


def fit_metadata(title: Any, description: Any, tags: Any = None) -> dict[str, Any]:
    """Generated or connected text made to fit YouTube's limits, never raising: for prefilling, not validating."""
    title = " ".join(_ANGLES.sub("", title).split()) if isinstance(title, str) else ""
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0] or title[:MAX_TITLE_CHARS]
    description = _ANGLES.sub("", description).strip() if isinstance(description, str) else ""
    description = description.encode("utf-8")[:MAX_DESCRIPTION_BYTES].decode("utf-8", "ignore")
    fitted: list[str] = []
    for tag in normalize_tags([tag for tag in tags if isinstance(tag, str)] if isinstance(tags, (list, tuple)) else []):
        tag = " ".join(_ANGLES.sub("", tag.replace(",", " ")).split())[:MAX_TAG_CHARS]
        if tag and tags_length([*fitted, tag]) <= MAX_TAGS_LENGTH:
            fitted.append(tag)
    return {"title": title, "description": description, "tags": fitted}


def publication_tags(publication: "Publication") -> list[str]:
    try:
        value = json.loads(publication.tags or "[]")
    except (TypeError, ValueError):
        return []
    return [tag for tag in value if isinstance(tag, str)] if isinstance(value, list) else []


def final_video(db: Session, run: WorkflowRun) -> Asset | None:
    """The run's video to publish: its final render when a Render step completed, else its first clip."""
    steps = db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id,
                                                     WorkflowRunStep.status == "completed",
                                                     WorkflowRunStep.node_type.in_(("render", "video")))
                       .order_by(WorkflowRunStep.position)).all()
    for node_type in ("render", "video"):
        for step in steps:
            if step.node_type != node_type:
                continue
            try:
                output = json.loads(step.output or "{}")
            except (TypeError, ValueError):
                continue
            output = output if isinstance(output, dict) else {}
            clips = output.get("video_assets") if isinstance(output.get("video_assets"), list) else []
            candidates = [output.get("asset_id"), *(clip.get("id") for clip in clips if isinstance(clip, dict))]
            for asset_id in candidates:
                if not isinstance(asset_id, str):
                    continue
                asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.run_id == run.id,
                                                      Asset.step_id == step.id, Asset.content_type == "video/mp4",
                                                      Asset.bytes > 0))
                if asset is not None:
                    return asset
    return None


class Publication(Base):
    """One approved run's upload to one social channel."""

    __tablename__ = "publications"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'uploading', 'succeeded', 'failed', 'needs_attention')",
            name="ck_publications_state",
        ),
        CheckConstraint("privacy_status IN ('private', 'unlisted', 'public')", name="ck_publications_privacy"),
        UniqueConstraint("workspace_id", "run_id", "channel", name="uq_publications_run_channel"),
        UniqueConstraint("job_id", name="uq_publications_job_id"),
        Index("ix_publications_workspace_state", "workspace_id", "state", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_jobs.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="queued")
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upload_session_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Migration 0013: the requested visibility and tags, and what YouTube reported after the upload.
    privacy_status: Mapped[str] = mapped_column(String(16), default="private", server_default="private")
    tags: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    remote_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remote_privacy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _approved_review(db: Session, *, workspace_id: str, run_id: str, asset_id: str) -> WorkflowRunStep:
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == workspace_id).with_for_update())
    if run is None:
        raise ValueError("run does not belong to workspace")
    # A publish node may still be unsupported/blocked after the explicit review
    # approval; the review record itself, rather than the aggregate run state,
    # is the publishing authorization boundary.
    if run.status not in {"completed", "blocked"}:
        raise ValueError("run has not been approved")
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == workspace_id,
                                         Asset.run_id == run_id, Asset.project_id == run.project_id,
                                         Asset.content_type == "video/mp4", Asset.bytes > 0))
    if asset is None:
        raise ValueError("asset is not a generated MP4 for this run and workspace")
    # A final render (Phase 8) or a generated clip; both are MP4s from a completed step of this run.
    video_step = db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.id == asset.step_id,
                                                       WorkflowRunStep.run_id == run_id,
                                                       WorkflowRunStep.node_type.in_(("video", "render")),
                                                       WorkflowRunStep.status == "completed"))
    if video_step is None:
        raise ValueError("asset is not linked to a completed video or render step")
    if video_step.node_type == "video" and db.scalar(select(WorkflowRunStep.id).where(
            WorkflowRunStep.run_id == run_id, WorkflowRunStep.node_type == "render",
            WorkflowRunStep.status == "completed")):
        raise ValueError("the run has a final render; publish it instead of a scene clip")
    reviews = db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id,
                                                        WorkflowRunStep.node_type == "review",
                                                        WorkflowRunStep.status == "completed")
                         .order_by(WorkflowRunStep.position.desc())).all()
    for review in reviews:
        try:
            approval = json.loads(review.output or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(approval, dict) and isinstance(approval.get("approved_by"), str) and approval["approved_by"]:
            return review
    raise ValueError("run has no approved review step")


def queue_publication(
    db: Session, *, workspace_id: str, run_id: str, asset_id: str,
    channel: str, title: str, description: str, tags: list[str] | None = None,
    privacy_status: str = "private",
) -> Publication:
    """Create once per run and channel, with an immutable queued job.

    The caller commits both rows together. A repeated request must provide the
    exact same asset and metadata; it cannot mutate a queued or finished upload.
    """
    if not isinstance(channel, str) or not _CHANNEL_RE.fullmatch(channel):
        raise ValueError("invalid publication channel")
    try:
        title, description, tags, privacy_status = validate_metadata(title, description, tags, privacy_status)
    except MetadataError as exc:
        raise ValueError(f"invalid publication {exc.field}") from exc
    tags_json = json.dumps(tags, ensure_ascii=False)
    review = _approved_review(db, workspace_id=workspace_id, run_id=run_id, asset_id=asset_id)
    connection_generation = None
    if channel == "youtube":
        connection = db.get(google_oauth.YouTubeConnection, workspace_id)
        if connection is None:
            raise ValueError("YouTube connection is required")
        connection_generation = google_oauth.connection_generation(connection)
    now = datetime.now(timezone.utc)
    values = dict(id=str(uuid4()), workspace_id=workspace_id, run_id=run_id,
                  asset_id=asset_id, channel=channel, title=title, description=description,
                  tags=tags_json, privacy_status=privacy_status,
                  state="queued", created_at=now, updated_at=now)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(Publication).values(**values).on_conflict_do_nothing(
            index_elements=["workspace_id", "run_id", "channel"])
    elif dialect == "sqlite":
        statement = sqlite_insert(Publication).values(**values).on_conflict_do_nothing(
            index_elements=["workspace_id", "run_id", "channel"])
    else:
        raise NotImplementedError(f"Publication queueing is unsupported on {dialect}")
    db.execute(statement)
    publication = db.scalar(select(Publication).where(Publication.workspace_id == workspace_id,
                                                      Publication.run_id == run_id,
                                                      Publication.channel == channel))
    if ((publication.asset_id, publication.title, publication.description, publication_tags(publication),
         publication.privacy_status) != (asset_id, title, description, tags, privacy_status)):
        raise ValueError("publication already exists with different input")
    payload = {"publication_id": publication.id, "channel": channel, "asset_id": asset_id}
    if connection_generation is not None:
        payload["connection_generation"] = connection_generation
    if publication.job_id is not None:
        existing_job = db.get(WorkflowJob, publication.job_id)
        if existing_job is not None and existing_job.payload == payload:
            return publication
        raise ValueError("publication already exists for a different connection")
    job = jobs.enqueue_job(
        db, workspace_id=workspace_id, run_id=run_id, step_id=review.id,
        logical_key=f"publish:{channel}:{run_id}",
        payload=payload,
    )
    if publication.job_id not in (None, job.id):
        raise ValueError("publication already belongs to another job")
    publication.job_id = job.id
    db.flush()
    return publication


def can_retry_publication(publication: Publication) -> bool:
    """Whether a terminal publication has no known or uncertain media submission."""
    return (publication.state in {"failed", "needs_attention"}
            and publication.upload_session_ciphertext is None and publication.remote_id is None
            and publication.last_error != "submission_without_saved_session"
            and not (publication.last_error or "").startswith("upload:"))


def retry_publication(db: Session, *, workspace_id: str, publication_id: str,
                      metadata: Mapping[str, Any] | None = None) -> Publication:
    """Explicitly retry only a terminal publication that never uploaded media.

    Only a new upload job is queued: the run, its steps and its media are reused
    as they are. ``metadata`` (title, description, tags, privacy_status) may
    correct the values that made the previous attempt fail.
    """
    publication = db.scalar(select(Publication).where(Publication.id == publication_id,
        Publication.workspace_id == workspace_id, Publication.channel == "youtube").with_for_update())
    if publication is None:
        raise ValueError("publication does not belong to workspace")
    connection = db.get(google_oauth.YouTubeConnection, workspace_id)
    if connection is None:
        raise ValueError("YouTube connection is required")
    generation = google_oauth.connection_generation(connection)
    old_job = db.get(WorkflowJob, publication.job_id) if publication.job_id else None
    if old_job is None or old_job.workspace_id != workspace_id or old_job.run_id != publication.run_id:
        raise ValueError("publication job is unavailable")
    if (publication.state == "queued" and ":retry:" in old_job.logical_key
            and old_job.state == "queued"
            and old_job.payload.get("connection_generation") == generation):
        return publication
    if publication.state not in {"failed", "needs_attention"} or old_job.state != "failed":
        raise ValueError("publication is not terminal and retryable")
    if not can_retry_publication(publication):
        raise ValueError("publication has an uncertain upload outcome")
    review = _approved_review(db, workspace_id=workspace_id, run_id=publication.run_id,
                              asset_id=publication.asset_id)
    if metadata:
        title, description, tags, privacy_status = validate_metadata(
            metadata.get("title", publication.title), metadata.get("description", publication.description),
            metadata.get("tags", publication_tags(publication)),
            metadata.get("privacy_status", publication.privacy_status))
        publication.title, publication.description = title, description
        publication.tags, publication.privacy_status = json.dumps(tags, ensure_ascii=False), privacy_status
    payload = {"publication_id": publication.id, "channel": "youtube",
               "asset_id": publication.asset_id, "connection_generation": generation}
    new_job = jobs.enqueue_job(db, workspace_id=workspace_id, run_id=publication.run_id,
        step_id=review.id, logical_key=f"publish:youtube:{publication.run_id}:retry:{old_job.id}",
        payload=payload)
    publication.job_id = new_job.id
    publication.state = "queued"
    publication.last_error = None
    publication.finished_at = None
    publication.updated_at = datetime.now(timezone.utc)
    db.flush()
    return publication


def _leased_publication(
    db: Session, *, publication_id: str, job_id: str, lease_token: str, now: datetime,
) -> tuple[Publication, WorkflowJob] | None:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
    if (job is None or job.state != "leased" or job.lease_token != lease_token
            or job.lease_expires_at is None or _utc(job.lease_expires_at) <= _utc(now)):
        return None
    publication = db.get(Publication, publication_id)
    expected_payload = ({"publication_id": publication.id, "channel": publication.channel,
                         "asset_id": publication.asset_id} if publication is not None else {})
    payload = job.payload
    if publication is not None and publication.channel == "youtube" and "connection_generation" in payload:
        if not isinstance(payload["connection_generation"], str) or not payload["connection_generation"]:
            return None
        expected_payload["connection_generation"] = payload["connection_generation"]
    if (publication is None or publication.job_id != job_id
            or publication.workspace_id != job.workspace_id or publication.run_id != job.run_id
            or payload != expected_payload):
        return None
    return publication, job


def mark_uploading(
    db: Session, *, publication_id: str, job_id: str, lease_token: str, now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    pair = _leased_publication(db, publication_id=publication_id, job_id=job_id,
                               lease_token=lease_token, now=now)
    if pair is None or pair[0].state not in {"queued", "uploading"}:
        return False
    publication = pair[0]
    publication.state, publication.updated_at = "uploading", now
    return True


def _cipher(encryption_key: str) -> Fernet:
    try:
        return Fernet(encryption_key.encode("ascii"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid publication encryption key") from exc


def save_upload_session(
    db: Session, *, publication_id: str, job_id: str, lease_token: str,
    session: Mapping[str, Any], encryption_key: str, now: datetime | None = None,
) -> bool:
    """Encrypt the resumable upload session before the next network call."""
    if not isinstance(session, Mapping):
        raise ValueError("upload session must be a JSON object")
    try:
        encoded = json.dumps(session, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if not isinstance(json.loads(encoded), dict):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("upload session must be a JSON object") from exc
    encrypted = _cipher(encryption_key).encrypt(encoded.encode("utf-8")).decode("ascii")
    now = now or datetime.now(timezone.utc)
    pair = _leased_publication(db, publication_id=publication_id, job_id=job_id,
                               lease_token=lease_token, now=now)
    if pair is None or pair[0].state not in {"queued", "uploading"}:
        return False
    publication = pair[0]
    publication.upload_session_ciphertext = encrypted
    publication.state, publication.updated_at = "uploading", now
    return True


def load_upload_session(publication: Publication, *, encryption_key: str) -> dict[str, Any] | None:
    if publication.upload_session_ciphertext is None:
        return None
    try:
        plain = _cipher(encryption_key).decrypt(publication.upload_session_ciphertext.encode("ascii"))
        value = json.loads(plain)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (InvalidToken, UnicodeError, TypeError, ValueError) as exc:
        raise ValueError("stored publication upload session cannot be decrypted") from exc


def finish_publication(
    db: Session, *, publication_id: str, job_id: str, lease_token: str,
    remote_id: str, now: datetime | None = None, upload_status: str | None = None,
    privacy_status: str | None = None,
) -> bool:
    if not isinstance(remote_id, str) or not remote_id or len(remote_id) > 255:
        raise ValueError("invalid remote publication ID")
    now = now or datetime.now(timezone.utc)
    pair = _leased_publication(db, publication_id=publication_id, job_id=job_id,
                               lease_token=lease_token, now=now)
    if pair is None:
        publication = db.get(Publication, publication_id)
        job = db.get(WorkflowJob, job_id)
        return bool(publication and job and publication.job_id == job_id and
                    job.state == "succeeded" and job.lease_token == lease_token and
                    publication.state == "succeeded" and publication.remote_id == remote_id)
    publication, _ = pair
    if publication.state not in {"queued", "uploading"}:
        return False
    if not jobs.complete_job(db, job_id=job_id, lease_token=lease_token, now=now):
        return False
    publication.state, publication.remote_id = "succeeded", remote_id
    publication.last_error, publication.upload_session_ciphertext = None, None
    # What YouTube reported: e.g. "uploaded" (still processing) and the visibility it actually applied.
    if isinstance(upload_status, str) and re.fullmatch(r"[a-z_]{1,32}", upload_status):
        publication.remote_status = upload_status
    if privacy_status in PRIVACY_STATUSES:
        publication.remote_privacy = privacy_status
    publication.updated_at, publication.finished_at = now, now
    return True


def fail_publication(
    db: Session, *, publication_id: str, job_id: str, lease_token: str,
    error: str, retry_delay_seconds: int | None = None,
    needs_attention: bool = False, now: datetime | None = None,
) -> bool:
    if not isinstance(error, str) or not error:
        raise ValueError("publication error is required")
    if needs_attention and retry_delay_seconds is not None:
        raise ValueError("needs_attention cannot be retried automatically")
    now = now or datetime.now(timezone.utc)
    pair = _leased_publication(db, publication_id=publication_id, job_id=job_id,
                               lease_token=lease_token, now=now)
    if pair is None:
        return False
    publication, _ = pair
    if publication.state not in {"queued", "uploading"}:
        return False
    if not jobs.fail_job(db, job_id=job_id, lease_token=lease_token, error=error,
                         retry_delay_seconds=retry_delay_seconds, now=now):
        return False
    publication.state = ("queued" if retry_delay_seconds is not None else
                         "needs_attention" if needs_attention else "failed")
    publication.last_error, publication.updated_at = error[:1000], now
    publication.finished_at = None if retry_delay_seconds is not None else now
    return True
