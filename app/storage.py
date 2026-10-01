"""Where ReelForge keeps media, how much a workspace may store, and which media may expire (Phase 17).

**Root.** ``REELFORGE_STORAGE_ROOT``, else the ``storage_dir`` system setting, else
``instance/media``; a relative path is under the project folder. One root holds everything:

* ``<root>/<workspace_id>/<asset_id>``: every asset, named by IDs only (never by a project
  or file name, which users control). The project is a column of the asset, so attaching an
  upload to another project moves no file.
* ``<root>/<workspace_id>/<asset_id>.part``: a file still being written.
* ``<root>/.render-tmp/``, ``.source-tmp/``, ``.publish-tmp/``: worker scratch space.

**Quota.** The workspace plan's ``storage_limit_bytes`` (migration 0016; 1 GiB when a plan
has none). ``WORKSPACE_MEDIA_QUOTA_BYTES``, when set, caps every workspace (a disk-safety
ceiling) and is the limit of plans without one. Usage is the sum of ``assets.bytes``: an
expired or deleted asset keeps its row (for lineage, run history and publications) with
``bytes`` 0, ``expired_at``, ``expired_reason`` and ``expired_bytes``. Every check that
stores media takes the workspace row lock first (``lock_workspace``), so two uploads or
workers cannot both pass the same remaining room.

**Kinds and retention.** Each asset has a ``kind`` set where it is created (``KINDS``).
Only ``INTERMEDIATE_KINDS`` ever expire, and only once their run has a final render, which
is what used them; final renders and uploaded sources are never removed automatically.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import shutil

from sqlalchemy import exists, func, inspect, select, update
from sqlalchemy.orm import aliased

from app.models import Asset, Plan, Subscription, SystemSetting, Workspace
from app.runtime_env import ROOT

STORAGE_ROOT_ENV = "REELFORGE_STORAGE_ROOT"
DEFAULT_STORAGE_DIR = "instance/media"
QUOTA_ENV = "WORKSPACE_MEDIA_QUOTA_BYTES"
GIB = 1024 ** 3
# Asset and workspace IDs are UUIDs; anything else that could name a path (separators, dots) is refused.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

# What each asset is. Set where the asset is created; migration 0016 labelled older assets from their step.
SOURCE = "source"                    # a user's upload
GENERATED_IMAGE = "generated_image"  # Image step
SCENE_VIDEO = "scene_video"          # Video step clip
VOICE = "voice"                      # Voice step narration
SUBTITLE = "subtitle"                # Subtitle step file
EXTRACTED_CLIP = "extracted_clip"    # Extract Source Clips (Movie Recap)
FINAL_RENDER = "final_render"        # Render step output
OTHER = "other"                      # anything not classified: never expires
KINDS = (SOURCE, GENERATED_IMAGE, SCENE_VIDEO, VOICE, SUBTITLE, EXTRACTED_CLIP, FINAL_RENDER, OTHER)
NODE_KINDS = {"image": GENERATED_IMAGE, "video": SCENE_VIDEO, "voice": VOICE, "subtitle": SUBTITLE,
              "extract_clips": EXTRACTED_CLIP, "render": FINAL_RENDER}
INTERMEDIATE_KINDS = frozenset({GENERATED_IMAGE, SCENE_VIDEO, VOICE, EXTRACTED_CLIP})

# Warning levels, highest first: at "full" nothing new may be stored; reading and downloading still work.
LEVELS = ((100, "full"), (90, "critical"), (80, "warning"), (70, "notice"))
# Publications that may still read their video: deleting it would break the upload or a retry.
PUBLICATION_DONE = ("succeeded", "cancelled")


def safe_id(value) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError("unsafe storage identifier")
    return value


def resolve_root(setting_value: str | None = None) -> Path:
    raw = os.environ.get(STORAGE_ROOT_ENV, "").strip() or (setting_value or "").strip() or DEFAULT_STORAGE_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def media_root(db) -> Path:
    row = db.get(SystemSetting, "storage_dir")
    return resolve_root(json.loads(row.value) if row else None)


def file_in(root: Path, workspace_id: str, asset_id: str | None = None) -> Path:
    """``<root>/<workspace_id>[/<asset_id>]``, for code that already holds the root (a worker keeps it per job)."""
    folder = Path(root) / safe_id(workspace_id)
    return folder if asset_id is None else folder / safe_id(asset_id)


def workspace_dir(db, workspace_id: str) -> Path:
    return file_in(media_root(db), workspace_id)


def asset_path(db, workspace_id: str, asset_id: str) -> Path:
    return file_in(media_root(db), workspace_id, asset_id)


def kind_for_node(node_type: str | None) -> str:
    return NODE_KINDS.get(node_type or "", OTHER)


# --- quota -------------------------------------------------------------------------------------------------

def server_cap() -> int | None:
    """``WORKSPACE_MEDIA_QUOTA_BYTES`` when set: no workspace may store more, whatever its plan says."""
    raw = os.environ.get(QUOTA_ENV, "").strip()
    if not raw:
        return None
    try:
        quota = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{QUOTA_ENV} must be a positive integer") from exc
    if quota <= 0:
        raise RuntimeError(f"{QUOTA_ENV} must be a positive integer")
    return quota


def default_quota() -> int:
    """The limit of a plan without ``storage_limit_bytes``."""
    return server_cap() or GIB


_plan_limits = False


def _plan_limits_available(db) -> bool:
    """Whether ``plans.storage_limit_bytes`` exists. Code running against a database before migration 0016
    (a deployment not migrated yet, or the tests of older migrations) keeps the environment quota."""
    global _plan_limits
    if not _plan_limits:
        _plan_limits = "storage_limit_bytes" in {column["name"] for column in inspect(db.connection()).get_columns("plans")}
    return _plan_limits


def plan_limit(db, workspace_id: str) -> int | None:
    if not _plan_limits_available(db):
        return None
    return db.scalar(select(Plan.storage_limit_bytes).join(Subscription, Subscription.plan_code == Plan.code)
                     .where(Subscription.workspace_id == workspace_id))


def effective_quota(limit: int | None) -> int:
    """A plan limit (or none) as the quota in force: capped by ``WORKSPACE_MEDIA_QUOTA_BYTES`` when that is set."""
    cap = server_cap()
    if limit and limit > 0:
        return min(limit, cap) if cap else limit
    return default_quota()


def quota_bytes(db, workspace_id: str) -> int:
    return effective_quota(plan_limit(db, workspace_id))


def stored_bytes(db, workspace_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.workspace_id == workspace_id)))


def lock_workspace(db, workspace_id: str) -> None:
    """Serialize storage checks of one workspace until the transaction ends (a row lock on every database)."""
    db.execute(update(Workspace).where(Workspace.id == workspace_id).values(name=Workspace.name))


def has_room(db, workspace_id: str, adding: int) -> bool:
    return stored_bytes(db, workspace_id) + max(0, adding) <= quota_bytes(db, workspace_id)


def is_full(db, workspace_id: str) -> bool:
    return stored_bytes(db, workspace_id) >= quota_bytes(db, workspace_id)


def percent(used: int, quota: int) -> float:
    return round(used * 100 / quota, 1) if quota > 0 else 100.0


def level(used: int, quota: int) -> str:
    """``ok`` below 70 %, then ``notice`` (70), ``warning`` (80), ``critical`` (90) and ``full`` (100)."""
    share = used * 100 / quota if quota > 0 else 100
    return next((name for threshold, name in LEVELS if share >= threshold), "ok")


def _summary(used: int, quota: int) -> dict:
    return {"used_bytes": used, "quota_bytes": quota, "percent": percent(used, quota), "level": level(used, quota)}


def usage(db, workspace_id: str) -> dict:
    return _summary(stored_bytes(db, workspace_id), quota_bytes(db, workspace_id))


def usage_by_workspace(db, workspace_ids: list[str] | None = None) -> dict[str, dict]:
    """``usage`` of many workspaces in two grouped queries; every workspace when ``workspace_ids`` is None."""
    used_query = select(Asset.workspace_id, func.coalesce(func.sum(Asset.bytes), 0)).group_by(Asset.workspace_id)
    ids_query = select(Workspace.id)
    if workspace_ids is not None:
        used_query = used_query.where(Asset.workspace_id.in_(workspace_ids))
        ids_query = ids_query.where(Workspace.id.in_(workspace_ids))
    used = {workspace_id: int(total) for workspace_id, total in db.execute(used_query)}
    limits = {}
    if _plan_limits_available(db):
        limit_query = select(Subscription.workspace_id, Plan.storage_limit_bytes).join(Plan, Plan.code == Subscription.plan_code)
        if workspace_ids is not None:
            limit_query = limit_query.where(Subscription.workspace_id.in_(workspace_ids))
        limits = dict(db.execute(limit_query).all())
    return {workspace_id: _summary(used.get(workspace_id, 0), effective_quota(limits.get(workspace_id)))
            for workspace_id in db.scalars(ids_query)}


def level_counts(db) -> dict[str, int]:
    """How many workspaces are at each warning level (70, 80, 90 and 100 %)."""
    counts = {name: 0 for _, name in LEVELS}
    for item in usage_by_workspace(db).values():
        if item["level"] in counts:
            counts[item["level"]] += 1
    return counts


def disk_usage(db) -> dict | None:
    """Total, used and free bytes of the disk that holds the media root (never the path itself), or None."""
    root = media_root(db)
    probe = next((path for path in (root, *root.parents) if path.exists()), None)
    if probe is None:
        return None
    try:
        total, used, free = shutil.disk_usage(probe)
    except OSError:
        return None
    return {"total_bytes": total, "used_bytes": used, "free_bytes": free, "percent": percent(used, total)}


# --- retention ---------------------------------------------------------------------------------------------

def _days(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number of days") from exc
    if value < minimum or value > 3650:
        raise RuntimeError(f"{name} must be between {minimum} and 3650")
    return value


@dataclass(frozen=True)
class RetentionPolicy:
    """How long each kind of file is kept. Final renders and uploaded sources have no entry: they are kept."""

    partial_days: int = 1        # ``.part`` files of interrupted writes
    temp_days: int = 3           # worker scratch folders
    orphan_days: int = 3         # files without an asset row (only with ``--orphans``)
    intermediate_days: int = 30  # scene videos, voice, generated images, extracted clips; 0 keeps them

    @classmethod
    def from_environment(cls) -> "RetentionPolicy":
        return cls(partial_days=_days("REELFORGE_RETENTION_PARTIAL_DAYS", 1, 1),
                   temp_days=_days("REELFORGE_RETENTION_TEMP_DAYS", 3, 1),
                   orphan_days=_days("REELFORGE_RETENTION_ORPHAN_DAYS", 3, 1),
                   intermediate_days=_days("REELFORGE_RETENTION_INTERMEDIATE_DAYS", 30, 0))


def expirable_query(*, workspace_id: str | None = None, project_id: str | None = None,
                    created_before: datetime | None = None):
    """Intermediate assets that may expire: their run has a live final render (which used them), and no
    publication refers to them. Final renders, sources, subtitles and unclassified assets never match."""
    from app.publications import Publication

    final = aliased(Asset)
    query = select(Asset).where(
        Asset.kind.in_(INTERMEDIATE_KINDS), Asset.expired_at.is_(None), Asset.bytes > 0, Asset.run_id.is_not(None),
        exists().where(final.run_id == Asset.run_id, final.workspace_id == Asset.workspace_id,
                       final.kind == FINAL_RENDER, final.bytes > 0),
        ~exists().where(Publication.asset_id == Asset.id))
    if workspace_id is not None:
        query = query.where(Asset.workspace_id == workspace_id)
    if project_id is not None:
        query = query.where(Asset.project_id == project_id)
    if created_before is not None:
        query = query.where(Asset.created_at < created_before)
    return query


def deletion_blocker(db, asset: Asset) -> str | None:
    """Why a user may not delete this asset now, or None. A publication that has not finished still needs it."""
    from app.publications import Publication

    if db.scalar(select(Publication.id).where(Publication.asset_id == asset.id,
                                              Publication.state.not_in(PUBLICATION_DONE)).limit(1)):
        return "asset_in_use"
    return None


def expire(db, asset: Asset, *, reason: str, now: datetime) -> int:
    """Mark a locked asset as gone (its file is removed after the transaction commits); returns the freed bytes."""
    freed = asset.bytes or 0
    asset.expired_bytes = freed
    asset.bytes = 0
    asset.expired_at = now
    asset.expired_reason = reason
    return freed


def cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
