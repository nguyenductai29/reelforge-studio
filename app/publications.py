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

Phase 12 adds TikTok and Facebook (``validate_channel_metadata``): one
publication per run and channel, each with its own independent upload job.
TikTok uploads go to the creator's inbox as drafts (visibility ``private``;
the caption is kept for the creator, since the inbox API takes none). Facebook
Reels are ``public`` (published on the Page) or ``private`` (a draft).

Phase 13 adds scheduling: a publication with ``scheduled_for`` in the future is
stored as ``scheduled`` without a job; the scheduler worker
(``app/scheduler_worker.py``) calls ``dispatch_due`` to queue its upload when
the time comes. Scheduled or still-queued publications can be rescheduled or
cancelled until a worker starts uploading. All times are UTC.
"""

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import jobs, notifications
from app.models import Asset, Base, WorkflowJob, WorkflowRun, WorkflowRunStep
from app.publishers import channel_oauth, google_oauth
from app.publishers.youtube import MAX_TAGS_LENGTH, PRIVACY_STATUSES, tags_length


_CHANNEL_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
CHANNELS = ("youtube", "tiktok", "facebook")
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_BYTES = 5000
MAX_TAG_CHARS = 100
MAX_TIKTOK_CAPTION = 2200
MAX_FACEBOOK_DESCRIPTION = 5000
MAX_SOCIAL_TAGS = 30
MAX_SCHEDULE_AHEAD = timedelta(days=365)
# Visibility each channel accepts: TikTok inbox drafts are private until the creator posts them;
# Facebook "public" publishes the Reel and "private" keeps it as a draft.
CHANNEL_PRIVACY = {"youtube": PRIVACY_STATUSES, "tiktok": ("private",), "facebook": ("public", "private")}
CANCELLABLE_STATES = frozenset({"scheduled", "queued"})
_ANGLES = re.compile(r"[<>]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


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


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def validate_channel_metadata(channel: str, title: Any, description: Any, tags: Any = None,
                              privacy_status: Any = "private") -> tuple[str, str, list[str], str]:
    """Metadata checked against one channel's rules, or ``MetadataError``.

    YouTube keeps ``validate_metadata``. TikTok: a caption (``description``) of at
    most 2,200 characters and visibility ``private`` (an inbox draft). Facebook: a
    description of at most 5,000 characters and visibility ``public`` or
    ``private`` (draft). Every channel needs a one-line title of at most 100
    characters (the YouTube title, and the label of the upload elsewhere) and at
    most 30 tags for TikTok and Facebook, used as hashtags.
    """
    if channel == "youtube":
        return validate_metadata(title, description, tags, privacy_status)
    if channel not in CHANNELS:
        raise MetadataError("invalid_channel", "channel", "Unsupported channel")
    if not isinstance(title, str) or not title.strip():
        raise MetadataError("invalid_title", "title", "A title is required")
    title = title.strip()
    if len(title) > MAX_TITLE_CHARS or "\n" in title or "\r" in title or _CONTROL.search(title):
        raise MetadataError("invalid_title", "title", "The title must be one line of at most 100 characters")
    if not isinstance(description, str) or _CONTROL.search(description) or "\r" in description:
        raise MetadataError("invalid_description", "description", "The description must be plain text")
    tags = normalize_tags(tags)
    if len(tags) > MAX_SOCIAL_TAGS or any(len(tag) > MAX_TAG_CHARS or " " in tag or "," in tag for tag in tags):
        raise MetadataError("invalid_tags", "tags", "Use at most 30 hashtags, each one word without commas")
    limit = MAX_TIKTOK_CAPTION if channel == "tiktok" else MAX_FACEBOOK_DESCRIPTION
    if _utf16_length(social_text(description, tags)) > limit:
        raise MetadataError("invalid_description", "description",
                            f"The caption with its hashtags must be at most {limit} characters")
    if privacy_status not in CHANNEL_PRIVACY[channel]:
        raise MetadataError("invalid_privacy", "privacy_status",
                            "TikTok uploads are private drafts" if channel == "tiktok"
                            else "Facebook Reels are public or private drafts")
    return title, description, tags, privacy_status


def social_text(description: str, tags: list[str]) -> str:
    """The caption as posted: the description, then the tags as hashtags."""
    hashtags = " ".join(f"#{tag}" for tag in tags)
    return "\n\n".join(part for part in (description.strip(), hashtags) if part)


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


def fit_social(channel: str, title: Any, description: Any, tags: Any = None) -> dict[str, Any]:
    """TikTok or Facebook metadata made to fit that channel, never raising: for prefilling, not validating.

    Tags become one-word hashtags and keep at most half of the channel's limit
    (the last ones are dropped); the text is then cut until the caption with
    its hashtags fits.
    """
    fitted = fit_metadata(title, "", [])
    clean = []
    for tag in tags if isinstance(tags, (list, tuple)) else []:
        tag = re.sub(r"[\s,#<>]+", "", tag)[:MAX_TAG_CHARS] if isinstance(tag, str) else ""
        if tag and tag.lower() not in {item.lower() for item in clean}:
            clean.append(tag)
    clean = clean[:MAX_SOCIAL_TAGS]
    text = _CONTROL.sub("", description).replace("\r", "").strip() if isinstance(description, str) else ""
    limit = MAX_TIKTOK_CAPTION if channel == "tiktok" else MAX_FACEBOOK_DESCRIPTION
    while clean and _utf16_length(social_text("", clean)) > limit // 2:
        clean.pop()
    while text and _utf16_length(social_text(text, clean)) > limit:
        text = text[:-max(1, _utf16_length(social_text(text, clean)) - limit)].rstrip()
    return {"title": fitted["title"], "description": text, "tags": clean, "privacy_status": "private"}


def platform_metadata(youtube: Mapping[str, Any], *, tiktok_caption: Any = None,
                      facebook_description: Any = None) -> dict[str, dict[str, Any]]:
    """Prefilled metadata per channel: YouTube's as given, TikTok and Facebook derived from it.

    TikTok gets a short caption (its own when given, else the title); Facebook
    gets its own description when given, else YouTube's. Both default to
    ``private`` (a TikTok inbox draft, a Facebook draft Reel).
    """
    title, tags = youtube.get("title") or "", youtube.get("tags") or []
    caption = tiktok_caption if isinstance(tiktok_caption, str) and tiktok_caption.strip() else title
    body = facebook_description if isinstance(facebook_description, str) and facebook_description.strip() \
        else youtube.get("description") or ""
    return {"youtube": dict(youtube), "tiktok": fit_social("tiktok", title, caption, tags),
            "facebook": fit_social("facebook", title, body, tags)}


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
            "state IN ('scheduled', 'queued', 'uploading', 'succeeded', 'failed', 'needs_attention', 'cancelled')",
            name="ck_publications_state",
        ),
        CheckConstraint("privacy_status IN ('private', 'unlisted', 'public')", name="ck_publications_privacy"),
        UniqueConstraint("workspace_id", "run_id", "channel", name="uq_publications_run_channel"),
        UniqueConstraint("job_id", name="uq_publications_job_id"),
        Index("ix_publications_workspace_state", "workspace_id", "state", "updated_at"),
        # Migration 0014: the scheduler's scan of due publications.
        Index("ix_publications_due", "state", "scheduled_for"),
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
    # Migration 0014: when a scheduled publication should be sent, and when it went live (UTC).
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def connection_generation(db: Session, channel: str, workspace_id: str) -> str:
    """The channel connection's consent marker, or ``ValueError`` when the channel cannot publish."""
    if channel == "youtube":
        connection = db.get(google_oauth.YouTubeConnection, workspace_id)
        if connection is None:
            raise ValueError("YouTube connection is required")
        return google_oauth.connection_generation(connection)
    if channel in ("tiktok", "facebook"):
        connection = db.get(channel_oauth.ChannelConnection, (workspace_id, channel))
        if connection is None or not connection.access_token_ciphertext or (
                channel == "facebook" and not connection.account_id):
            raise ValueError(f"{channel.capitalize()} connection is required")
        return channel_oauth.connection_generation(connection)
    raise ValueError("invalid publication channel")


def _job_payload(publication: "Publication", generation: str | None) -> dict[str, Any]:
    payload = {"publication_id": publication.id, "channel": publication.channel, "asset_id": publication.asset_id}
    if generation is not None:
        payload["connection_generation"] = generation
    return payload


def _enqueue_upload(db: Session, publication: "Publication", review: WorkflowRunStep, generation: str) -> WorkflowJob:
    """Queue a fresh upload job; a key already used by an earlier job of this run and channel gets a new suffix."""
    key = f"publish:{publication.channel}:{publication.run_id}"
    existing = db.scalar(select(WorkflowJob).where(WorkflowJob.logical_key == key))
    payload = _job_payload(publication, generation)
    if existing is not None and (existing.id != publication.job_id or existing.payload != payload
                                 or existing.state not in ("queued", "leased")):
        key = f"{key}:retry:{uuid4()}"
    job = jobs.enqueue_job(db, workspace_id=publication.workspace_id, run_id=publication.run_id,
                           step_id=review.id, logical_key=key, payload=payload)
    publication.job_id = job.id
    return job


def schedule_time(value: datetime | None, now: datetime | None = None) -> datetime | None:
    """A requested publishing time in UTC; ``None`` (or a time already passed) means now."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise MetadataError("invalid_schedule", "scheduled_for", "The schedule must be a date and time")
    now = now or datetime.now(timezone.utc)
    value = _utc(value)
    if value > now + MAX_SCHEDULE_AHEAD:
        raise MetadataError("invalid_schedule", "scheduled_for", "Schedule at most one year ahead")
    return value if value > now + timedelta(seconds=30) else None


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
    privacy_status: str = "private", scheduled_for: datetime | None = None,
) -> Publication:
    """Create once per run and channel, with an immutable queued job, or scheduled without one.

    The caller commits both rows together. A repeated request must provide the
    exact same asset and metadata; it cannot mutate a queued or finished upload.
    A cancelled publication of the run and channel is replaced by the new request.
    """
    if not isinstance(channel, str) or not _CHANNEL_RE.fullmatch(channel) or channel not in CHANNELS:
        raise ValueError("invalid publication channel")
    try:
        title, description, tags, privacy_status = validate_channel_metadata(channel, title, description, tags,
                                                                             privacy_status)
        scheduled_for = schedule_time(scheduled_for)
    except MetadataError as exc:
        raise ValueError(f"invalid publication {exc.field}") from exc
    tags_json = json.dumps(tags, ensure_ascii=False)
    review = _approved_review(db, workspace_id=workspace_id, run_id=run_id, asset_id=asset_id)
    connection_generation_value = connection_generation(db, channel, workspace_id)
    now = datetime.now(timezone.utc)
    state = "scheduled" if scheduled_for else "queued"
    cancelled = db.scalar(select(Publication).where(Publication.workspace_id == workspace_id,
                                                    Publication.run_id == run_id, Publication.channel == channel,
                                                    Publication.state == "cancelled").with_for_update())
    if cancelled is not None:
        cancelled.asset_id, cancelled.title, cancelled.description = asset_id, title, description
        cancelled.tags, cancelled.privacy_status, cancelled.state = tags_json, privacy_status, state
        cancelled.scheduled_for, cancelled.last_error, cancelled.finished_at = scheduled_for, None, None
        cancelled.remote_id = cancelled.remote_status = cancelled.remote_privacy = None
        cancelled.upload_session_ciphertext, cancelled.updated_at = None, now
        if state == "queued":
            _enqueue_upload(db, cancelled, review, connection_generation_value)
        db.flush()
        if state == "scheduled":
            notifications.publication_changed(db, cancelled, "scheduled", reference=scheduled_for.isoformat())
        return cancelled
    values = dict(id=str(uuid4()), workspace_id=workspace_id, run_id=run_id,
                  asset_id=asset_id, channel=channel, title=title, description=description,
                  tags=tags_json, privacy_status=privacy_status, scheduled_for=scheduled_for,
                  state=state, created_at=now, updated_at=now)
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
    if publication.state == "scheduled":
        notifications.publication_changed(db, publication, "scheduled",
                                          reference=publication.scheduled_for.isoformat())
        return publication
    if publication.job_id is None and publication.state != "queued":
        raise ValueError("publication is not queued; retry it instead")
    payload = {"publication_id": publication.id, "channel": channel, "asset_id": asset_id,
               "connection_generation": connection_generation_value}
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


def _apply_metadata(publication: Publication, metadata: Mapping[str, Any] | None) -> None:
    if not metadata:
        return
    title, description, tags, privacy_status = validate_channel_metadata(
        publication.channel, metadata.get("title", publication.title),
        metadata.get("description", publication.description),
        metadata.get("tags", publication_tags(publication)),
        metadata.get("privacy_status", publication.privacy_status))
    publication.title, publication.description = title, description
    publication.tags, publication.privacy_status = json.dumps(tags, ensure_ascii=False), privacy_status


def retry_publication(db: Session, *, workspace_id: str, publication_id: str,
                      metadata: Mapping[str, Any] | None = None, channel: str | None = "youtube") -> Publication:
    """Explicitly retry only a terminal publication that never uploaded media.

    Only a new upload job is queued: the run, its steps and its media are reused
    as they are. ``metadata`` (title, description, tags, privacy_status) may
    correct the values that made the previous attempt fail. ``channel`` limits
    the retry to one channel (the YouTube routes); ``None`` accepts any.
    """
    query = select(Publication).where(Publication.id == publication_id, Publication.workspace_id == workspace_id)
    if channel is not None:
        query = query.where(Publication.channel == channel)
    publication = db.scalar(query.with_for_update())
    if publication is None:
        raise ValueError("publication does not belong to workspace")
    generation = connection_generation(db, publication.channel, workspace_id)
    old_job = db.get(WorkflowJob, publication.job_id) if publication.job_id else None
    # A scheduled publication the scheduler could not queue (disconnected channel) never had a job.
    unqueued = (old_job is None and publication.state == "failed"
                and (publication.last_error or "").startswith("schedule:"))
    if not unqueued and (old_job is None or old_job.workspace_id != workspace_id
                         or old_job.run_id != publication.run_id):
        raise ValueError("publication job is unavailable")
    if unqueued:
        review = _approved_review(db, workspace_id=workspace_id, run_id=publication.run_id,
                                  asset_id=publication.asset_id)
        _apply_metadata(publication, metadata)
        _enqueue_upload(db, publication, review, generation)
        publication.state, publication.last_error, publication.finished_at = "queued", None, None
        publication.updated_at = datetime.now(timezone.utc)
        db.flush()
        return publication
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
    _apply_metadata(publication, metadata)
    payload = {"publication_id": publication.id, "channel": publication.channel,
               "asset_id": publication.asset_id, "connection_generation": generation}
    new_job = jobs.enqueue_job(db, workspace_id=workspace_id, run_id=publication.run_id,
        step_id=review.id, logical_key=f"publish:{publication.channel}:{publication.run_id}:retry:{old_job.id}",
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
    if publication is not None and publication.channel in CHANNELS and "connection_generation" in payload:
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
    privacy_status: str | None = None, published: bool = True,
) -> bool:
    """Record a finished upload. ``published`` is false when the platform holds it for the
    creator (a TikTok inbox draft): ``published_at`` then stays empty."""
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
    publication.published_at = now if published else None
    notifications.publication_changed(db, publication, "succeeded", reference=job_id)
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
    if publication.state in ("failed", "needs_attention"):
        notifications.publication_changed(db, publication, publication.state, reference=job_id)
    return True


def continue_upload(db: Session, *, publication_id: str, job_id: str, lease_token: str, delay_seconds: int,
                    now: datetime | None = None) -> bool:
    """Hand a multi-step upload back to the queue after saving its progress (next chunk, next poll)."""
    now = now or datetime.now(timezone.utc)
    pair = _leased_publication(db, publication_id=publication_id, job_id=job_id, lease_token=lease_token, now=now)
    if pair is None or pair[0].state not in {"queued", "uploading"}:
        return False
    return jobs.fail_job(db, job_id=job_id, lease_token=lease_token, error="upload_in_progress",
                         retry_delay_seconds=max(0, delay_seconds), now=now)


def dispatch_due(db: Session, *, now: datetime | None = None, limit: int = 20) -> list[tuple[str, str]]:
    """Queue the uploads of scheduled publications whose time has come; returns ``(id, outcome)`` pairs.

    Rows are locked (``SKIP LOCKED`` on PostgreSQL) so several schedulers never
    dispatch one publication twice, and the state is checked again under the lock,
    so a restart after a crash simply continues. A publication whose run is no
    longer approved, or whose channel is disconnected, fails with a clear error
    instead of uploading. The caller commits.
    """
    now = now or datetime.now(timezone.utc)
    query = (select(Publication).where(Publication.state == "scheduled", Publication.scheduled_for <= now)
             .order_by(Publication.scheduled_for, Publication.id).limit(limit))
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    outcomes = []
    for publication in db.scalars(query).all():
        if publication.state != "scheduled":
            continue
        try:
            review = _approved_review(db, workspace_id=publication.workspace_id, run_id=publication.run_id,
                                      asset_id=publication.asset_id)
            generation = connection_generation(db, publication.channel, publication.workspace_id)
        except ValueError as exc:
            reason = "connection_required" if "connection" in str(exc) else "not_approved"
            publication.state, publication.last_error = "failed", f"schedule:{reason}"
            publication.updated_at = publication.finished_at = now
            notifications.publication_changed(db, publication, "failed", reference="schedule")
            outcomes.append((publication.id, publication.last_error))
            continue
        _enqueue_upload(db, publication, review, generation)
        publication.state, publication.updated_at = "queued", now
        outcomes.append((publication.id, "queued"))
    db.flush()
    return outcomes


def _unstarted_job(db: Session, publication: Publication) -> WorkflowJob | None:
    """The publication's job, if it is still waiting in the queue (no worker has claimed it)."""
    return db.get(WorkflowJob, publication.job_id) if publication.job_id else None


def _withdraw_job(db: Session, publication: Publication, error: str, now: datetime) -> None:
    """Take a queued publication's job out of the queue, or ``ValueError`` once a worker started it."""
    if publication.state == "scheduled":
        return
    job = _unstarted_job(db, publication)
    if job is None or publication.upload_session_ciphertext is not None or publication.remote_id is not None:
        raise ValueError("upload_started")
    changed = db.execute(update(WorkflowJob).where(WorkflowJob.id == job.id, WorkflowJob.state == "queued")
                         .values(state="failed", last_error=error, finished_at=now, updated_at=now)
                         .execution_options(synchronize_session="fetch"))
    if changed.rowcount != 1:
        raise ValueError("upload_started")


def cancel_publication(db: Session, *, workspace_id: str, publication_id: str,
                       now: datetime | None = None) -> Publication:
    """Cancel a scheduled publication, or a queued one whose upload has not started; the caller commits."""
    now = now or datetime.now(timezone.utc)
    publication = db.scalar(select(Publication).where(Publication.id == publication_id,
                                                      Publication.workspace_id == workspace_id).with_for_update())
    if publication is None:
        raise LookupError("publication not found")
    if publication.state not in CANCELLABLE_STATES:
        raise ValueError("upload_started")
    _withdraw_job(db, publication, "cancelled", now)
    publication.state, publication.last_error = "cancelled", None
    publication.updated_at = publication.finished_at = now
    return publication


def reschedule_publication(db: Session, *, workspace_id: str, publication_id: str, scheduled_for: datetime | None,
                           now: datetime | None = None) -> Publication:
    """Move a scheduled (or still-queued) publication to another time; ``None`` or a past time means now."""
    now = now or datetime.now(timezone.utc)
    publication = db.scalar(select(Publication).where(Publication.id == publication_id,
                                                      Publication.workspace_id == workspace_id).with_for_update())
    if publication is None:
        raise LookupError("publication not found")
    if publication.state not in CANCELLABLE_STATES:
        raise ValueError("upload_started")
    when = schedule_time(scheduled_for, now)
    if when is None and publication.state == "queued":
        return publication
    _withdraw_job(db, publication, "rescheduled", now)
    # Due now: the scheduler queues it on its next pass (within seconds).
    publication.state, publication.scheduled_for = "scheduled", when or now
    publication.updated_at, publication.last_error = now, None
    return publication
