"""Movie sources: temporary source movies for Movie Recap, Movie Review and Ending Explained (migration 0027).

A member adds a movie from a file in the operator's import folder, from a direct HTTPS media URL the studio is
allowed to use, or from the studio's Google Drive inbox. The movie worker (``app/movie_worker.py``) imports it
outside any request, checks it with ffprobe, stores it in the operator's Google Drive (``app/google_drive.py``)
and marks it ready. A movie workflow then downloads it once to local scratch space for its run. Once a review
has succeeded (plus a grace period), or when its retention runs out, the source is deleted from Drive and local
disk; its row stays, as history. The final video is an ordinary asset and is never touched.

Lifecycle (``status``)::

    importing ─→ uploading ─→ ready ⇄ processing ─→ completed
        └───────────┴─→ failed            │               │
    ready / completed / failed ─→ delete_scheduled ─→ deleting ─→ deleted

* A source is **in use** while a workflow run that refers to it (``movie_source_uses``) is running or waiting for
  review; such a source is never deleted, by a person or by the retention.
* **Deletion** is due when ``expires_at`` has passed, or, with ``delete_after_success``, ``delete_grace_hours``
  after a run using it completed. The scheduler worker marks due sources ``delete_scheduled``; the movie worker
  removes the Drive file (trash by default) and the scratch files, retrying failures with a back-off.
* **One import folder per studio**: server files come from ``<import root>/<workspace id>/`` only, so a studio never
  sees or imports a movie the operator placed for another one (the Drive inbox works the same way).
* **No path is stored as given**: a local import keeps its path relative to its folder, a URL import keeps
  its display form (scheme, host, path) and the full URL encrypted only until the import ends; scratch paths are
  never returned.

Use only movies you are authorized to use: nothing here downloads from streaming services or bypasses DRM or any
other protection; the operator and the user remain responsible for the source and for what they publish.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import unicodedata
import uuid
from urllib.parse import urlsplit

from sqlalchemy import exists, func, or_, select, update

from app import audit, notifications, secret_box, storage, system_config
from app.logs import log_event
from app.models import MovieSource, MovieSourceUse, Project, User, WorkflowRun

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("local", "url", "drive")
STATUSES = ("created", "importing", "uploading", "ready", "processing", "completed", "delete_scheduled", "deleting",
            "deleted", "failed")
USABLE = ("ready", "processing", "completed")
WORKING = ("importing", "uploading")
DELETABLE = ("created", "importing", "uploading", "ready", "processing", "completed", "failed")
GONE = ("delete_scheduled", "deleting", "deleted")
# Runs in these states still read the source: it must not disappear under them.
ACTIVE_RUN_STATUSES = ("running", "awaiting_review")
MEDIA_EXTENSIONS = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
                    ".webm": "video/webm"}
EXTEND_DAYS = (1, 3, 7)
URL_PURPOSE = "movie-source-url"
SESSION_PURPOSE = "movie-source-upload"
LEASE_SECONDS = 900
SCHEDULE_INTERVAL_SECONDS = 300
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_last_schedule = {"at": None}


class MovieSourceError(Exception):
    """A request or an import that cannot go on, with a stable ``code`` (never a path or a URL)."""

    def __init__(self, code: str, message: str, *, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Settings:
    enabled: bool
    retention_days: int
    max_retention_days: int
    delete_after_success: bool
    success_grace_hours: int
    max_source_bytes: int
    max_duration_seconds: int
    local_import_root: str
    scratch_root: str
    delete_local_temp: bool
    delete_scratch: bool
    frame_interval_seconds: int
    max_frames: int


def settings() -> Settings:
    get = system_config.get
    return Settings(enabled=bool(get("movie_sources.enabled")),
                    retention_days=int(get("movie_sources.retention_days") or 7),
                    max_retention_days=int(get("movie_sources.max_retention_days") or 30),
                    delete_after_success=bool(get("movie_sources.delete_after_success")),
                    success_grace_hours=int(get("movie_sources.success_grace_hours") or 0),
                    max_source_bytes=int(get("movie_sources.max_source_bytes") or 20 * 1024 ** 3),
                    max_duration_seconds=int(get("movie_sources.max_duration_seconds") or 4 * 3600),
                    local_import_root=str(get("movie_sources.local_import_root") or "").strip(),
                    scratch_root=str(get("movie_sources.scratch_root") or "").strip(),
                    delete_local_temp=bool(get("movie_sources.delete_local_temp")),
                    delete_scratch=bool(get("movie_sources.delete_scratch")),
                    frame_interval_seconds=int(get("movie_sources.frame_interval_seconds") or 10),
                    max_frames=int(get("movie_sources.max_frames") or 300))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value else None


# --- where files live ---------------------------------------------------------------------------------------

def scratch_root(db) -> Path:
    """Local working space: the setting, else ``<media root>/.movie-scratch``. Never shown to users."""
    configured = settings().scratch_root
    root = Path(configured).expanduser() if configured else storage.media_root(db) / ".movie-scratch"
    return root if root.is_absolute() else storage.ROOT / root


def _safe(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError("unsafe identifier")
    return value


def import_dir(root: Path, source_id: str) -> Path:
    """Where a source is fetched and checked before it goes to Drive."""
    return root / "imports" / _safe(source_id)


def cache_dir(root: Path, source_id: str) -> Path:
    """A checked local copy kept after the upload, when the operator turned off deleting the local temp."""
    return root / "cache" / _safe(source_id)


def work_dir(root: Path, source_id: str) -> Path:
    """The scratch copy every movie step of every run reads (downloaded from Drive once)."""
    return root / "movie-jobs" / _safe(source_id)


def run_dir(root: Path, source_id: str, run_id: str) -> Path:
    """One run's frames and other working files."""
    return work_dir(root, source_id) / _safe(run_id)


CONTAINER_EXTENSIONS = {"mp4": ".mp4", "mov": ".mov", "matroska": ".mkv", "webm": ".webm"}


def extension_for(source: MovieSource) -> str:
    return CONTAINER_EXTENSIONS.get(source.container or "", ".mp4")


# --- names, URLs and local paths ------------------------------------------------------------------------------

def sanitize_name(name: str | None, fallback: str = "movie") -> str:
    """A display name: no path, no control characters, at most 200 characters."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C").strip(" .")
    return (text or fallback)[:200]


def display_url(url: str) -> str:
    """The URL without its query, fragment or credentials (a signed URL's token never reaches the table)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return f"{parts.scheme}://{host}{parts.path or '/'}"[:500]


def encrypt_url(url: str) -> str:
    return secret_box.encrypt_json(URL_PURPOSE, {"url": url})


def decrypt_url(ciphertext: str) -> str:
    return secret_box.decrypt_json(URL_PURPOSE, ciphertext)["url"]


def import_root() -> Path | None:
    raw = settings().local_import_root
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else None


def workspace_import_root(workspace_id: str) -> Path | None:
    """A studio's import folder, ``<import root>/<workspace id>``: the only place its server files come from."""
    root = import_root()
    return root / _safe(workspace_id) if root is not None else None


def _relative_parts(relative: str) -> tuple[str, ...]:
    """The parts of a path relative to the import root, or ``invalid_path``."""
    if not isinstance(relative, str) or not relative.strip() or len(relative) > 1000 or "\x00" in relative:
        raise MovieSourceError("invalid_path", "Choose a file inside the import folder", status=422)
    text = relative.strip().replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise MovieSourceError("invalid_path", "Use a path relative to the import folder", status=422)
    parts = tuple(part for part in PurePosixPath(text).parts if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise MovieSourceError("invalid_path", "The path must stay inside the import folder", status=422)
    return parts


def resolve_local(relative: str, *, root: Path | None = None, must_be_file: bool = True) -> Path:
    """A file (or folder) under the import root: no ``..``, no absolute path, no link anywhere on the way.

    Each component is checked with ``lstat`` (a symbolic link is refused even if it points inside the root),
    then the real path must still be under the root's real path, and a file must be a regular file with a
    movie extension."""
    if root is None:
        raise MovieSourceError("import_root_missing", "No import folder is configured", status=409)
    try:
        base = root.resolve(strict=True)
    except OSError as exc:
        raise MovieSourceError("import_root_missing", "The import folder does not exist", status=409) from exc
    parts = _relative_parts(relative) if relative not in ("", ".") or must_be_file else ()
    current = base
    for part in parts:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise MovieSourceError("not_found", "No such file in the import folder", status=404) from exc
        if stat.S_ISLNK(info.st_mode):
            raise MovieSourceError("symlink_refused", "Links are not followed in the import folder", status=422)
    real = Path(os.path.realpath(current))
    if real != base and base not in real.parents:
        raise MovieSourceError("invalid_path", "The path must stay inside the import folder", status=422)
    info = os.stat(real)
    if must_be_file:
        if not stat.S_ISREG(info.st_mode):
            raise MovieSourceError("not_a_file", "Choose a regular file", status=422)
        if real.suffix.lower() not in MEDIA_EXTENSIONS:
            raise MovieSourceError("unsupported_type", "Choose an MP4, MKV, MOV or WebM movie", status=422)
    elif not stat.S_ISDIR(info.st_mode):
        raise MovieSourceError("not_a_folder", "Choose a folder", status=422)
    return real


def list_local(folder: str = "", *, root: Path | None, limit: int = 200) -> dict:
    """One folder under ``root`` (a studio's import folder): its sub-folders and its movie files (names and sizes;
    links left out)."""
    target = resolve_local(folder or ".", root=root, must_be_file=False)
    base = root.resolve()
    try:
        names = sorted(os.listdir(target))
    except OSError as exc:
        raise MovieSourceError("not_found", "The folder cannot be read", status=404) from exc
    entries = []
    for name in names:
        if name.startswith("."):
            continue
        path = target / name
        try:
            info = os.lstat(path)
        except OSError:
            continue
        relative = path.relative_to(base).as_posix()
        if stat.S_ISDIR(info.st_mode):
            entries.append({"type": "folder", "name": name, "path": relative})
        elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in MEDIA_EXTENSIONS:
            entries.append({"type": "file", "name": name, "path": relative, "bytes": info.st_size,
                            "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
        if len(entries) >= limit:
            break
    here = "" if target == base else target.relative_to(base).as_posix()
    parent = None if target == base else ("" if target.parent == base else target.parent.relative_to(base).as_posix())
    return {"folder": here, "parent": parent, "entries": entries, "truncated": len(entries) >= limit}


# --- reading and locking ----------------------------------------------------------------------------------------

def lock(db, source_id: str) -> MovieSource | None:
    """The row, locked until the transaction ends (a no-op UPDATE also locks on SQLite)."""
    db.execute(update(MovieSource).where(MovieSource.id == source_id).values(status=MovieSource.status))
    return db.get(MovieSource, source_id, populate_existing=True)


def for_workspace(db, workspace_id: str, source_id: str, *, locked: bool = False) -> MovieSource | None:
    if not isinstance(source_id, str) or not _SAFE_ID.fullmatch(source_id):
        return None
    source = lock(db, source_id) if locked else db.get(MovieSource, source_id)
    return source if source is not None and source.workspace_id == workspace_id else None


def active_runs(db, source_id: str) -> list[str]:
    return list(db.scalars(select(MovieSourceUse.run_id).join(WorkflowRun, WorkflowRun.id == MovieSourceUse.run_id)
                           .where(MovieSourceUse.movie_source_id == source_id,
                                  WorkflowRun.status.in_(ACTIVE_RUN_STATUSES))))


def in_use(db, source_id: str) -> bool:
    return bool(active_runs(db, source_id))


def _active_use_clause():
    return exists().where(MovieSourceUse.movie_source_id == MovieSource.id, WorkflowRun.id == MovieSourceUse.run_id,
                          WorkflowRun.status.in_(ACTIVE_RUN_STATUSES))


def deletion_due_at(source: MovieSource) -> datetime | None:
    """When the source becomes due for deletion: the earlier of its expiry and its success grace."""
    candidates = [aware(source.expires_at)] if source.expires_at else []
    if source.delete_after_success and source.success_at:
        candidates.append(aware(source.success_at) + timedelta(hours=max(0, source.delete_grace_hours or 0)))
    return min(candidates) if candidates else None


# --- creating ------------------------------------------------------------------------------------------------

def create(db, *, workspace_id: str, user: User, source_type: str, path: str | None = None, url: str | None = None,
           drive_file_id: str | None = None, name: str | None = None, project_id: str | None = None,
           now: datetime | None = None) -> MovieSource:
    """A new source, queued for the movie worker (``importing``). Checks that need no network happen here."""
    from app import google_drive, sources

    now = now or _now()
    current = settings()
    if not current.enabled:
        raise MovieSourceError("movie_sources_disabled", "Movie sources are not enabled on this server")
    if problem := google_drive.config().problem():
        raise MovieSourceError("drive_not_configured", f"Google Drive is not ready ({problem})")
    if source_type not in SOURCE_TYPES:
        raise MovieSourceError("invalid_source_type", "Choose a local file, a URL or a Drive file", status=422)
    if project_id is not None:
        project = db.get(Project, project_id) if isinstance(project_id, str) else None
        if project is None or project.workspace_id != workspace_id:
            raise MovieSourceError("invalid_project", "The project is not in this studio", status=422)
    source = MovieSource(id=str(uuid.uuid4()), workspace_id=workspace_id, project_id=project_id,
                         created_by_user_id=user.id, source_type=source_type, status="importing",
                         created_at=now, updated_at=now, next_attempt_at=now, attempt_count=0,
                         expires_at=now + timedelta(days=current.retention_days),
                         delete_after_success=current.delete_after_success,
                         delete_grace_hours=current.success_grace_hours)
    if source_type == "local":
        folder = workspace_import_root(workspace_id)
        file = resolve_local(path or "", root=folder)
        size = file.stat().st_size
        if size > current.max_source_bytes:
            raise MovieSourceError("too_large", "The movie is larger than the server allows", status=422)
        source.local_path = file.relative_to(folder.resolve()).as_posix()
        source.original_name = sanitize_name(name or file.name)
        source.bytes = size
    elif source_type == "url":
        try:
            sources.check_url((url or "").strip())
        except sources.SourceError as exc:
            raise MovieSourceError(exc.code, "Only public https:// media addresses can be imported",
                                   status=422) from exc
        url = url.strip()
        source.original_url = display_url(url)
        source.url_ciphertext = encrypt_url(url)
        source.original_name = sanitize_name(name or PurePosixPath(urlsplit(url).path).name, "movie")
    else:
        if not google_drive.valid_id(drive_file_id):
            raise MovieSourceError("invalid_drive_file", "Choose a file from the studio's Drive inbox", status=422)
        source.drive_import_id = drive_file_id
        source.original_name = sanitize_name(name, "movie")
    db.add(source)
    db.flush()
    audit.record(db, "movie_source.created", actor_id=user.id, workspace_id=workspace_id, target_type="movie_source",
                 target_id=source.id, details={"source_type": source_type})
    log_event(logger, "movie_source_created", workspace_id=workspace_id, movie_source_id=source.id,
              source_type=source_type)
    return source


# --- views -----------------------------------------------------------------------------------------------------

def public(db, source: MovieSource, *, admin: bool = False, uses: list | None = None) -> dict:
    """What the API returns: never a scratch path, a full URL, a Drive credential or (for members) a Drive ID."""
    project = db.get(Project, source.project_id) if source.project_id else None
    if uses is None:
        uses = db.execute(select(WorkflowRun.id, WorkflowRun.status, WorkflowRun.workflow_id, WorkflowRun.created_at)
                          .join(MovieSourceUse, MovieSourceUse.run_id == WorkflowRun.id)
                          .where(MovieSourceUse.movie_source_id == source.id)
                          .order_by(WorkflowRun.created_at.desc()).limit(10)).all()
    runs = [{"id": run_id, "status": status, "workflow_id": workflow_id, "created_at": _iso(created_at)}
            for run_id, status, workflow_id, created_at in uses]
    busy = any(run["status"] in ACTIVE_RUN_STATUSES for run in runs)
    due = deletion_due_at(source)
    view = {
        "id": source.id, "name": source.original_name, "source_type": source.source_type, "status": source.status,
        "project": {"id": project.id, "title": project.title} if project else None,
        "original_url": source.original_url if source.source_type == "url" else None,
        "local_path": source.local_path if source.source_type == "local" else None,
        "bytes": source.bytes, "duration_seconds": source.duration_seconds, "width": source.width,
        "height": source.height, "container": source.container, "content_type": source.content_type,
        "video_codec": source.video_codec, "audio_codec": source.audio_codec,
        "progress_bytes": source.progress_bytes if source.status in WORKING else None,
        "created_at": _iso(source.created_at), "ready_at": _iso(source.ready_at), "expires_at": _iso(source.expires_at),
        "success_at": _iso(source.success_at), "deleted_at": _iso(source.deleted_at),
        "delete_after_success": source.delete_after_success, "delete_grace_hours": source.delete_grace_hours,
        "deletion_due_at": _iso(due) if source.status not in GONE else None,
        "failure": {"stage": source.failure_stage, "code": source.failure_code,
                    "message": source.failure_message_safe} if source.failure_code else None,
        "stored_in_drive": bool(source.drive_file_id) and source.status not in ("deleted",),
        "in_use": busy, "runs": runs,
        "can_retry_upload": source.status == "failed" and source.failure_stage == "upload",
        "can_retry_import": source.status == "failed" and source.failure_stage in ("import", "upload"),
        "can_extend": source.status in DELETABLE,
        "can_use": source.status in USABLE,
    }
    if admin:
        view.update(workspace_id=source.workspace_id, drive_file_id=source.drive_file_id,
                    drive_folder_id=source.drive_folder_id, attempt_count=source.attempt_count,
                    next_attempt_at=_iso(source.next_attempt_at), checksum_sha256=source.checksum_sha256)
    return view


def page(db, workspace_id: str | None, *, q: str | None = None, status: str | None = None,
         source_type: str | None = None, limit: int = 20, offset: int = 0, admin: bool = False) -> dict:
    query = select(MovieSource)
    if workspace_id is not None:
        query = query.where(MovieSource.workspace_id == workspace_id)
    if q and q.strip():
        query = query.where(func.lower(MovieSource.original_name).contains(q.strip().lower(), autoescape=True))
    if status:
        statuses = ("importing", "uploading", "created") if status == "importing" else \
            ("delete_scheduled", "deleting") if status == "delete_scheduled" else (status,)
        query = query.where(MovieSource.status.in_(statuses))
    if source_type in SOURCE_TYPES:
        query = query.where(MovieSource.source_type == source_type)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(MovieSource.created_at.desc(), MovieSource.id).limit(limit).offset(offset)).all()
    return {"items": [public(db, row, admin=admin) for row in rows], "total": int(total), "limit": limit,
            "offset": offset}


# --- member actions -----------------------------------------------------------------------------------------------

def extend(db, source: MovieSource, *, days: int, user_id: str, now: datetime | None = None) -> MovieSource:
    """Keep the source longer, by 1, 3 or 7 days, never past now + the maximum retention."""
    now = now or _now()
    if days not in EXTEND_DAYS:
        raise MovieSourceError("invalid_days", "Extend by 1, 3 or 7 days", status=422)
    if source.status not in DELETABLE:
        raise MovieSourceError("source_expired", "This source is being deleted or was deleted")
    limit = now + timedelta(days=settings().max_retention_days)
    current = aware(source.expires_at) or now
    target = min(max(current, now) + timedelta(days=days), limit)
    if target - current < timedelta(hours=1):  # at the cap (which moves with the clock): nothing worth adding
        raise MovieSourceError("retention_limit", "The source is already kept as long as the server allows")
    source.expires_at, source.updated_at = target, now
    audit.record(db, "movie_source.retention_extended", actor_id=user_id, workspace_id=source.workspace_id,
                 target_type="movie_source", target_id=source.id, details={"days": days})
    return source


def request_delete(db, source: MovieSource, *, user_id: str | None, now: datetime | None = None,
                   reason: str = "requested") -> MovieSource:
    """Schedule the deletion (the movie worker removes the files). Refused while a run uses the source."""
    now = now or _now()
    if source.status in GONE:
        return source  # asking twice is harmless
    if source.lease_expires_at and aware(source.lease_expires_at) > now and source.status in WORKING:
        raise MovieSourceError("source_busy", "The source is being imported; try again when it is ready or failed")
    if in_use(db, source.id):
        raise MovieSourceError("source_in_use", "A workflow run still uses this source")
    source.status, source.delete_requested_at, source.updated_at = "delete_scheduled", now, now
    source.attempt_count, source.next_attempt_at, source.lease_token, source.lease_expires_at = 0, now, None, None
    source.failure_code = source.failure_stage = source.failure_message_safe = None
    audit.record(db, "movie_source.delete_requested", actor_id=user_id, workspace_id=source.workspace_id,
                 target_type="movie_source", target_id=source.id, details={"reason": reason})
    log_event(logger, "movie_source_delete_scheduled", workspace_id=source.workspace_id, movie_source_id=source.id,
              reason=reason)
    return source


def retry(db, source: MovieSource, *, stage: str, user_id: str, now: datetime | None = None) -> MovieSource:
    """Try a failed import again (``import``: fetch again; ``upload``: send the checked local copy again)."""
    now = now or _now()
    if source.status == "delete_scheduled" and source.failure_code:
        source.next_attempt_at, source.updated_at = now, now  # a deletion that keeps failing: try it now
        return source
    if source.status not in ("failed", "created") or stage not in ("import", "upload"):
        raise MovieSourceError("not_failed", "Only a failed import can be tried again")
    if stage == "upload" and (source.status != "failed" or source.failure_stage != "upload"):
        raise MovieSourceError("not_failed", "The upload was not what failed; try the import again")
    source.status = "uploading" if stage == "upload" else "importing"
    source.attempt_count, source.next_attempt_at, source.updated_at = 0, now, now
    source.failure_code = source.failure_stage = source.failure_message_safe = None
    source.lease_token = source.lease_expires_at = None
    audit.record(db, "movie_source.import_started", actor_id=user_id, workspace_id=source.workspace_id,
                 target_type="movie_source", target_id=source.id, details={"retry": stage})
    return source


# --- workflow runs ---------------------------------------------------------------------------------------------------

def begin_use(db, source: MovieSource, run: WorkflowRun, now: datetime | None = None) -> None:
    """Record that ``run`` uses the (locked) source; refused unless the source is usable and not expired."""
    now = now or _now()
    if source.status not in USABLE:
        code = "source_not_ready" if source.status in WORKING + ("created",) else \
            "source_failed" if source.status == "failed" else "source_expired"
        raise MovieSourceError(code, "This movie source cannot be used now")
    if source.expires_at and aware(source.expires_at) <= now:
        raise MovieSourceError("source_expired", "This movie source has expired")
    if not db.scalar(select(MovieSourceUse.id).where(MovieSourceUse.movie_source_id == source.id,
                                                       MovieSourceUse.run_id == run.id)):
        db.add(MovieSourceUse(id=str(uuid.uuid4()), movie_source_id=source.id, workspace_id=source.workspace_id,
                              run_id=run.id, created_at=now))
    if source.status != "processing":
        source.status = "processing"
    source.processing_started_at = source.processing_started_at or now
    source.updated_at = now


def _settle_after_runs(db, source: MovieSource, now: datetime) -> None:
    """Once no run is active any more: ``completed`` after a success, else ``ready`` again."""
    if source.status != "processing" or in_use(db, source.id):
        return
    source.status = "completed" if source.success_at else "ready"
    source.processing_finished_at, source.updated_at = now, now


def run_status_changed(db, run: WorkflowRun, previous: str, status: str) -> None:
    """Called by the executor when a run's status changes (same transaction)."""
    if previous == status or status in ACTIVE_RUN_STATUSES:
        return
    from sqlalchemy import inspect

    if not inspect(db.connection()).has_table("movie_source_uses"):
        return  # a database before migration 0027 (an upgrade in progress, a migration test)
    ids = list(db.scalars(select(MovieSourceUse.movie_source_id).where(MovieSourceUse.run_id == run.id)))
    if not ids:
        return
    from app.models import Asset

    now = _now()
    succeeded = status == "completed" and db.scalar(
        select(Asset.id).where(Asset.run_id == run.id, Asset.kind == storage.FINAL_RENDER, Asset.bytes > 0).limit(1))
    for source_id in ids:
        source = lock(db, source_id)
        if source is None:
            continue
        if succeeded and not source.success_at:
            source.success_at = now
            log_event(logger, "movie_source_success", workspace_id=source.workspace_id, movie_source_id=source.id,
                      run_id=run.id, delete_after_success=source.delete_after_success,
                      grace_hours=source.delete_grace_hours)
        _settle_after_runs(db, source, now)


def refresh_processing(db, now: datetime | None = None) -> int:
    """Sources still marked processing although no run uses them (a run ended outside the executor)."""
    now = now or _now()
    rows = db.scalars(select(MovieSource).where(MovieSource.status == "processing", ~_active_use_clause())).all()
    for source in rows:
        source = lock(db, source.id)
        _settle_after_runs(db, source, now)
    return len(rows)


# --- retention ------------------------------------------------------------------------------------------------------

def schedule_deletions(db, now: datetime | None = None, *, limit: int = 100) -> list[str]:
    """Mark every source that is due and not in use ``delete_scheduled``; returns their IDs."""
    now = now or _now()
    refresh_processing(db, now)
    candidates = db.scalars(select(MovieSource).where(
        MovieSource.status.in_(("ready", "completed", "failed")),
        or_(MovieSource.expires_at <= now, MovieSource.success_at.is_not(None)),
        ~_active_use_clause()).order_by(MovieSource.expires_at).limit(limit)).all()
    scheduled = []
    for candidate in candidates:
        due = deletion_due_at(candidate)
        if due is None or due > now:
            continue
        source = lock(db, candidate.id)
        if source.status not in ("ready", "completed", "failed") or in_use(db, source.id):
            continue
        reason = "expired" if source.expires_at and aware(source.expires_at) <= now else "after_success"
        request_delete(db, source, user_id=None, now=now, reason=reason)
        scheduled.append(source.id)
    return scheduled


def maybe_schedule(session_factory=None, *, force: bool = False) -> list[str] | None:
    """At most every ``SCHEDULE_INTERVAL_SECONDS`` (the scheduler worker calls this on every pass)."""
    moment = time.monotonic()
    if not force and _last_schedule["at"] is not None and moment - _last_schedule["at"] < SCHEDULE_INTERVAL_SECONDS:
        return None
    _last_schedule["at"] = moment
    if session_factory is None:
        from app.db import Session as session_factory
    from sqlalchemy import inspect

    with session_factory.begin() as db:
        if not inspect(db.connection()).has_table("movie_sources"):
            return None
        scheduled = schedule_deletions(db)
    if scheduled:
        log_event(logger, "movie_sources_scheduled_for_deletion", count=len(scheduled))
    return scheduled


# --- the movie worker's lease --------------------------------------------------------------------------------------

WORK_STATUSES = ("importing", "uploading", "delete_scheduled", "deleting")


def claim_work(db, *, worker_id: str, now: datetime | None = None, lease_seconds: int = LEASE_SECONDS,
               source_id: str | None = None) -> MovieSource | None:
    """Lease one source that needs the worker (import, upload or deletion), or None."""
    now = now or _now()
    due = [MovieSource.status.in_(WORK_STATUSES),
           or_(MovieSource.next_attempt_at.is_(None), MovieSource.next_attempt_at <= now),
           or_(MovieSource.lease_expires_at.is_(None), MovieSource.lease_expires_at <= now)]
    if source_id is not None:
        due.append(MovieSource.id == source_id)
    order = (MovieSource.next_attempt_at, MovieSource.created_at, MovieSource.id)
    if db.get_bind().dialect.name == "postgresql":
        source = db.scalars(select(MovieSource).where(*due).order_by(*order).limit(1)
                            .with_for_update(skip_locked=True)).first()
    else:
        candidate = select(MovieSource.id).where(*due).order_by(*order).limit(1).scalar_subquery()
        claimed = db.execute(update(MovieSource).where(MovieSource.id == candidate, *due)
                             .values(lease_token=str(uuid.uuid4())).returning(MovieSource.id)
                             .execution_options(synchronize_session=False)).scalar_one_or_none()
        source = db.get(MovieSource, claimed, populate_existing=True) if claimed else None
    if source is None:
        return None
    source.lease_token = str(uuid.uuid4())
    source.lease_expires_at = now + timedelta(seconds=lease_seconds)
    source.worker_id = worker_id[:128]
    source.attempt_count = (source.attempt_count or 0) + 1
    source.updated_at = now
    db.flush()
    return source


def live(db, source_id: str, token: str, *, lock_row: bool = True) -> MovieSource | None:
    """The source while ``token`` still holds its lease."""
    source = lock(db, source_id) if lock_row else db.get(MovieSource, source_id)
    if source is None or source.lease_token != token or not source.lease_expires_at \
            or aware(source.lease_expires_at) <= _now():
        return None
    return source


def renew(session_factory, source_id: str, token: str, *, progress: int | None = None,
          lease_seconds: int = LEASE_SECONDS) -> bool:
    """Extend the lease (and record the bytes moved so far); False when another worker took the source."""
    with session_factory.begin() as db:
        now = _now()
        result = db.execute(update(MovieSource).where(MovieSource.id == source_id, MovieSource.lease_token == token,
                                                      MovieSource.lease_expires_at > now)
                            .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now,
                                    **({"progress_bytes": progress} if progress is not None else {}))
                            .execution_options(synchronize_session=False))
        return bool(result.rowcount)


# --- notifications and summaries ------------------------------------------------------------------------------------

def notify(db, source: MovieSource, event: str) -> None:
    """``movie_source.ready`` or ``movie_source.failed``, to the member who added the source."""
    if not source.created_by_user_id:
        return
    params = {"name": source.original_name[:120], "code": source.failure_code}
    notifications.notify(db, [source.created_by_user_id], event, source.original_name[:200],
                         workspace_id=source.workspace_id, link=f"/media/movie-sources?source={source.id}",
                         params=params, dedupe=f"{event}:{source.id}:{source.attempt_count or 0}")


def summary(db, now: datetime | None = None) -> dict:
    """Admin → Operations: what the temporary Drive storage holds (counted from the table, never Drive's quota)."""
    now = now or _now()
    stored = (MovieSource.drive_file_id.is_not(None), MovieSource.status.notin_(("deleted",)))
    files = db.scalar(select(func.count()).select_from(MovieSource).where(*stored)) or 0
    total = db.scalar(select(func.coalesce(func.sum(MovieSource.bytes), 0)).where(*stored)) or 0
    oldest = db.scalar(select(func.min(MovieSource.ready_at)).where(*stored))
    expiring = db.scalar(select(func.count()).select_from(MovieSource).where(
        MovieSource.status.in_(USABLE), MovieSource.expires_at <= now + timedelta(hours=24))) or 0
    delete_failures = db.scalar(select(func.count()).select_from(MovieSource).where(
        MovieSource.status.in_(("delete_scheduled", "deleting")), MovieSource.failure_code.is_not(None))) or 0
    failed_day = db.scalar(select(func.count()).select_from(MovieSource).where(
        MovieSource.status == "failed", MovieSource.updated_at >= now - timedelta(hours=24))) or 0
    working = db.scalar(select(func.count()).select_from(MovieSource).where(MovieSource.status.in_(WORKING))) or 0
    from app import google_drive

    warning = int(system_config.get("movie_sources.drive.warning_bytes") or 0)
    return {"enabled": settings().enabled, "drive_problem": google_drive.config().problem(), "files": int(files),
            "bytes": int(total), "oldest_at": _iso(oldest), "expiring_soon": int(expiring),
            "delete_failures": int(delete_failures), "failed_last_day": int(failed_day), "importing": int(working),
            "warning_bytes": warning, "over_warning": bool(warning and total >= warning)}
