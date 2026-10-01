"""Daily storage maintenance: files ReelForge left behind, intermediate media past retention, and a usage report.

Run ``python -m app.media_maintenance`` (or ``--dry-run``) to preview. Only ``--apply``
deletes. Links or junctions are never followed, and nothing younger than 24 hours is
touched. Ages come from the retention policy (``app/storage.py``, ``REELFORGE_RETENTION_*``):
``.part`` files after 1 day, worker temp folders and orphans after 3 days, unless
``--older-than-hours`` sets one age for all three. File leftovers:

* ``.part`` downloads: the exact UUID4 ``<id>.part`` names written by the media
  workers, directly inside existing workspace directories (always checked);
* worker temp folders: ``<media>/.render-tmp/<job>``, ``.source-tmp/<job>`` and
  ``.publish-tmp/<job>.mp4`` left by a crashed render, transcription or upload
  (always checked; a folder containing any link is skipped whole);
* orphan files: UUID4-named files in a workspace directory with no asset row
  (only with ``--orphans``, which reads the asset table).

With ``--intermediates`` (reads and writes the asset table): intermediate media past
``REELFORGE_RETENTION_INTERMEDIATE_DAYS`` (default 30) whose run has a final render and that
no publication uses (``storage.expirable_query``). Each asset is re-checked under a row
lock, marked expired (its row stays with 0 bytes), then its file is removed; a file is
deleted only at ``<root>/<workspace_id>/<asset_id>``, as a regular file reached through no
link. Expired rows whose file is still present (an interrupted run) are swept again.
Final renders and uploaded sources are never removed.

``--usage`` prints stored bytes, quota and warning level per workspace.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Iterable
from app import system_config


_UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_WORKSPACE_RE = re.compile(rf"{_UUID4}\Z")
_PART_RE = re.compile(rf"{_UUID4}\.part\Z")
_ASSET_RE = re.compile(rf"{_UUID4}\Z")
_TEMP_ENTRY_RE = re.compile(rf"{_UUID4}(?:\.mp4)?\Z")
# Asset and workspace IDs as app.storage accepts them: no separator, no dot, nothing that can climb a path.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
TEMP_FOLDERS = (".render-tmp", ".source-tmp", ".publish-tmp")


@dataclass(frozen=True)
class CleanupReport:
    candidates: tuple[Path, ...]
    deleted: tuple[Path, ...]
    skipped: tuple[Path, ...]


@dataclass(frozen=True)
class _Candidate:
    path: Path
    workspace_id: str
    root_identity: tuple[int, int]
    workspace_identity: tuple[int, int]
    file_identity: tuple[int, int, int, int]


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    # Windows junctions are reparse points; Path.is_junction was only added in
    # Python 3.12, while ReelForge still supports Python 3.11.
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def _checked_root(path: Path) -> Path:
    root = Path(path).absolute()
    # A linked ancestor can silently redirect the configured root outside the
    # location the operator intended. Inspect lexical ancestors before resolve.
    for component in (root, *root.parents):
        if _is_link(component):
            raise ValueError("media root contains a symbolic link or junction")
    if root.exists() and not root.is_dir():
        raise ValueError("media root must be a directory")
    return root


def _workspace(root: Path, workspace_id: str) -> Path | None:
    if not isinstance(workspace_id, str) or not _WORKSPACE_RE.fullmatch(workspace_id):
        raise ValueError("unsafe workspace identifier")
    path = root / workspace_id
    if _is_link(path) or not path.is_dir():
        return None
    if path.resolve(strict=True).parent != root.resolve(strict=True):
        return None
    return path


def _part(path: Path, workspace: Path, cutoff: float):
    if not _PART_RE.fullmatch(path.name) or _is_link(path):
        return None
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff
                or path.resolve(strict=True).parent != workspace.resolve(strict=True)):
            return None
        return info
    except (FileNotFoundError, OSError):
        return None


def _scan(root: Path, workspace_ids: Iterable[str], cutoff: float) -> tuple[_Candidate, ...]:
    result = []
    root_id = _identity(root)
    for workspace_id in sorted(set(workspace_ids)):
        workspace = _workspace(root, workspace_id)
        if workspace is None:
            continue
        workspace_id_pair = _identity(workspace)
        for path in workspace.iterdir():
            info = _part(path, workspace, cutoff)
            if info is not None:
                result.append(_Candidate(
                    path=path, workspace_id=workspace_id, root_identity=root_id,
                    workspace_identity=workspace_id_pair,
                    file_identity=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                ))
    return tuple(sorted(result, key=lambda candidate: str(candidate.path)))


def _delete_if_unchanged(root: Path, candidate: _Candidate, cutoff: float) -> bool:
    try:
        if _checked_root(root) != root or _identity(root) != candidate.root_identity:
            return False
        workspace = _workspace(root, candidate.workspace_id)
        if workspace is None or _identity(workspace) != candidate.workspace_identity:
            return False
        info = _part(candidate.path, workspace, cutoff)
        if (info is None or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                != candidate.file_identity):
            return False
        candidate.path.unlink()
        return True
    except (FileNotFoundError, OSError, ValueError):
        return False


def cleanup_stale_parts(
    root: Path, workspace_ids: Iterable[str], *, apply: bool = False,
    now: datetime | None = None, minimum_age_hours: int = 24,
) -> CleanupReport:
    """List or delete old direct-child UUID4 `.part` files for known workspaces.

    Candidates are revalidated before deletion. Changes to a file or its
    containing directories after scanning cause it to be skipped.
    """
    if (not isinstance(minimum_age_hours, int) or isinstance(minimum_age_hours, bool)
            or minimum_age_hours < 24):
        raise ValueError("minimum_age_hours must be at least 24")
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    root = _checked_root(root)
    if not root.exists():
        return CleanupReport((), (), ())
    cutoff = (now.astimezone(timezone.utc) - timedelta(hours=minimum_age_hours)).timestamp()
    candidates = _scan(root, workspace_ids, cutoff)
    if not apply:
        return CleanupReport(tuple(item.path for item in candidates), (), ())
    deleted, skipped = [], []
    for item in candidates:
        if _delete_if_unchanged(root, item, cutoff):
            deleted.append(item.path)
        else:
            skipped.append(item.path)
    return CleanupReport(tuple(item.path for item in candidates), tuple(deleted), tuple(skipped))


def _tree_is_plain(path: Path) -> bool:
    """Whether a folder holds only regular files and folders (no link or junction anywhere)."""
    for current, folders, files in os.walk(path, followlinks=False):
        for name in (*folders, *files):
            child = Path(current) / name
            if _is_link(child) or not (child.is_dir() or child.is_file()):
                return False
    return True


def _newest(path: Path) -> float:
    newest = path.lstat().st_mtime
    if path.is_dir():
        for current, folders, files in os.walk(path, followlinks=False):
            for name in (*folders, *files):
                newest = max(newest, (Path(current) / name).lstat().st_mtime)
    return newest


def cleanup_temp_folders(root: Path, *, apply: bool = False, now: datetime | None = None,
                         minimum_age_hours: int = 24) -> CleanupReport:
    """List or delete worker temp entries (see the module docstring) untouched for ``minimum_age_hours``."""
    if (not isinstance(minimum_age_hours, int) or isinstance(minimum_age_hours, bool)
            or minimum_age_hours < 24):
        raise ValueError("minimum_age_hours must be at least 24")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    root = _checked_root(root)
    cutoff = (now.astimezone(timezone.utc) - timedelta(hours=minimum_age_hours)).timestamp()
    candidates, deleted, skipped = [], [], []
    for name in TEMP_FOLDERS:
        folder = root / name
        if not folder.is_dir() or _is_link(folder):
            continue
        for entry in sorted(folder.iterdir()):
            if not _TEMP_ENTRY_RE.fullmatch(entry.name) or _is_link(entry):
                continue
            try:
                if _newest(entry) >= cutoff:
                    continue
            except OSError:
                continue
            candidates.append(entry)
            if not apply:
                continue
            try:
                if entry.is_dir() and _tree_is_plain(entry) and _newest(entry) < cutoff:
                    shutil.rmtree(entry)
                    deleted.append(entry)
                elif entry.is_file() and not _is_link(entry) and _newest(entry) < cutoff:
                    entry.unlink()
                    deleted.append(entry)
                else:
                    skipped.append(entry)
            except OSError:
                skipped.append(entry)
    return CleanupReport(tuple(candidates), tuple(deleted), tuple(skipped))


def cleanup_orphans(root: Path, workspace_ids: Iterable[str], asset_exists, *, apply: bool = False,
                    now: datetime | None = None, minimum_age_hours: int = 24) -> CleanupReport:
    """List or delete UUID4-named files in workspace folders that no asset row refers to.

    ``asset_exists(workspace_id, asset_id)`` is asked when scanning and again just
    before deleting, so a file whose row appeared in between is kept.
    """
    if (not isinstance(minimum_age_hours, int) or isinstance(minimum_age_hours, bool)
            or minimum_age_hours < 24):
        raise ValueError("minimum_age_hours must be at least 24")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    root = _checked_root(root)
    if not root.exists():
        return CleanupReport((), (), ())
    cutoff = (now.astimezone(timezone.utc) - timedelta(hours=minimum_age_hours)).timestamp()
    candidates, deleted, skipped = [], [], []
    for workspace_id in sorted(set(workspace_ids)):
        workspace = _workspace(root, workspace_id)
        if workspace is None:
            continue
        for path in sorted(workspace.iterdir()):
            if not _ASSET_RE.fullmatch(path.name) or _is_link(path):
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff or asset_exists(workspace_id, path.name):
                continue
            candidates.append(path)
            if not apply:
                continue
            try:
                current = path.lstat()
                if ((current.st_ino, current.st_size, current.st_mtime_ns) == (info.st_ino, info.st_size,
                                                                               info.st_mtime_ns)
                        and not _is_link(path) and not asset_exists(workspace_id, path.name)):
                    path.unlink()
                    deleted.append(path)
                else:
                    skipped.append(path)
            except OSError:
                skipped.append(path)
    return CleanupReport(tuple(candidates), tuple(deleted), tuple(skipped))


def check_asset_file(root: Path, workspace_id: str, asset_id: str) -> str:
    """``present``, ``missing`` or ``unsafe`` for ``<root>/<workspace_id>/<asset_id>``.

    ``unsafe``: an ID that could name another path, a linked root, workspace folder or
    file, something other than a regular file, or a path that resolves outside the root.
    """
    if not (isinstance(workspace_id, str) and _SAFE_ID.fullmatch(workspace_id)
            and isinstance(asset_id, str) and _SAFE_ID.fullmatch(asset_id)):
        return "unsafe"
    try:
        root = _checked_root(root)
        workspace = root / workspace_id
        if _is_link(workspace):
            return "unsafe"
        if not workspace.is_dir():
            return "missing"
        if workspace.resolve(strict=True).parent != root.resolve(strict=True):
            return "unsafe"
        path = workspace / asset_id
        if _is_link(path):
            return "unsafe"
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "unsafe"
    if not stat.S_ISREG(info.st_mode) or path.resolve(strict=True).parent != workspace.resolve(strict=True):
        return "unsafe"
    return "present"


def remove_asset_file(root: Path, workspace_id: str, asset_id: str) -> str:
    """Delete one asset's file if ``check_asset_file`` finds it present; returns ``deleted``, ``missing`` or ``unsafe``."""
    state = check_asset_file(root, workspace_id, asset_id)
    if state != "present":
        return state
    try:
        (Path(root) / workspace_id / asset_id).unlink()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    return "deleted"


@dataclass(frozen=True)
class ExpiryReport:
    candidates: tuple[dict, ...]   # {"id", "workspace_id", "kind", "bytes", "created_at"}
    expired: tuple[str, ...]
    skipped: tuple[str, ...]       # changed since the scan (no longer eligible) or an unsafe path
    freed_bytes: int
    swept: tuple[str, ...]         # files of assets that had already expired


def expire_intermediates(*, apply: bool = False, now: datetime | None = None, policy=None,
                         session_factory=None) -> ExpiryReport:
    """List or expire intermediate media past retention (see the module docstring)."""
    from sqlalchemy import select
    from app import storage
    from app.models import Asset

    if session_factory is None:
        from app.db import Session as session_factory
    policy = policy or storage.RetentionPolicy.from_environment()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    if policy.intermediate_days == 0:
        return ExpiryReport((), (), (), 0, ())
    before = storage.cutoff(now, policy.intermediate_days)
    with session_factory() as db:
        root = storage.media_root(db)
        found = tuple({"id": asset.id, "workspace_id": asset.workspace_id, "kind": asset.kind, "bytes": asset.bytes,
                       "created_at": asset.created_at}
                      for asset in db.scalars(storage.expirable_query(created_before=before)
                                              .order_by(Asset.created_at, Asset.id)))
    if not apply:
        return ExpiryReport(found, (), (), sum(item["bytes"] for item in found), ())
    expired, skipped, freed = [], [], 0
    for item in found:
        if check_asset_file(root, item["workspace_id"], item["id"]) == "unsafe":
            skipped.append(item["id"])
            continue
        with session_factory.begin() as db:
            storage.lock_workspace(db, item["workspace_id"])
            asset = db.scalar(storage.expirable_query(created_before=before).where(Asset.id == item["id"])
                              .with_for_update())
            if asset is None:
                skipped.append(item["id"])
                continue
            freed += storage.expire(db, asset, reason="retention", now=now)
        remove_asset_file(root, item["workspace_id"], item["id"])
        expired.append(item["id"])
    with session_factory() as db:
        gone = list(db.execute(select(Asset.workspace_id, Asset.id).where(Asset.expired_at.is_not(None))))
    swept = tuple(asset_id for workspace_id, asset_id in gone
                  if asset_id not in expired and remove_asset_file(root, workspace_id, asset_id) == "deleted")
    # The admin readiness view shows when the daily job last ran.
    from app.readiness import record_maintenance
    record_maintenance(session_factory, expired=len(expired), freed_bytes=freed)
    return ExpiryReport(found, tuple(expired), tuple(skipped), freed, swept)


def configured_asset_check():
    """``asset_exists(workspace_id, asset_id)`` against the configured database."""
    from sqlalchemy import select
    from app.db import Session
    from app.models import Asset

    def asset_exists(workspace_id: str, asset_id: str) -> bool:
        with Session() as db:
            return db.scalar(select(Asset.id).where(Asset.id == asset_id, Asset.workspace_id == workspace_id)) \
                is not None
    return asset_exists


def storage_usage(db) -> list[dict]:
    """Stored bytes, live files, quota and warning level per workspace, from the asset table (largest first)."""
    from sqlalchemy import func, select
    from app import storage
    from app.models import Asset, Workspace

    rows = db.execute(select(Workspace.id, Workspace.name, func.count(Asset.id), func.coalesce(func.sum(Asset.bytes), 0))
                      .outerjoin(Asset, (Asset.workspace_id == Workspace.id) & (Asset.bytes > 0))
                      .group_by(Workspace.id, Workspace.name))
    quotas = storage.usage_by_workspace(db)
    usage = [{"workspace_id": workspace_id, "name": name, "files": files, "bytes": int(total),
              **{key: quotas[workspace_id][key] for key in ("quota_bytes", "percent", "level")}}
             for workspace_id, name, files, total in rows]
    return sorted(usage, key=lambda item: (-item["bytes"], item["name"]))


def usage_by_type(db, workspace_id: str) -> dict[str, int]:
    """Stored bytes of one workspace by media kind: video, audio, image, document."""
    from sqlalchemy import func, select
    from app.models import Asset

    totals = {"video": 0, "audio": 0, "image": 0, "document": 0}
    for content_type, total in db.execute(select(Asset.content_type, func.coalesce(func.sum(Asset.bytes), 0))
                                          .where(Asset.workspace_id == workspace_id).group_by(Asset.content_type)):
        kind = (content_type or "").split("/")[0]
        totals[kind if kind in ("video", "audio", "image") else "document"] += int(total)
    return totals


def configured_media_context() -> tuple[Path, list[str]]:
    """Read the configured storage directory and known workspace IDs from DB."""
    from sqlalchemy import select
    from app.db import ROOT, Session
    from app.models import SystemSetting, Workspace

    with Session() as db:
        setting = db.get(SystemSetting, "storage_dir")
        if setting is None:
            raise RuntimeError("storage_dir is unavailable; migrate and initialize the API first")
        value = json.loads(setting.value)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("configured storage_dir must be a nonempty path")
        # REELFORGE_STORAGE_ROOT wins over the stored setting, as everywhere else (app/storage.py).
        path = Path(system_config.env("REELFORGE_STORAGE_ROOT").strip() or value).expanduser()
        root = path if path.is_absolute() else ROOT / path
        workspace_ids = list(db.scalars(select(Workspace.id)))
    return root, workspace_ids


def _print_report(label: str, mode: str, root: Path, report: CleanupReport) -> None:
    print(f"{mode}: {len(report.candidates)} {label} under {root}")
    for path in report.candidates:
        action = "deleted" if path in report.deleted else "skipped" if path in report.skipped else "would delete"
        print(f"{action}: {path}")
    print(f"deleted: {len(report.deleted)}; skipped: {len(report.skipped)}")


def _print_expiry(mode: str, report: ExpiryReport) -> None:
    print(f"{mode}: {len(report.candidates)} intermediate asset(s) past retention, {report.freed_bytes:,d} bytes")
    for item in report.candidates:
        action = ("expired" if item["id"] in report.expired else "skipped" if item["id"] in report.skipped
                  else "would expire")
        print(f"{action}: {item['workspace_id']}/{item['id']}  {item['kind']}  {item['bytes']:,d} bytes  "
              f"created {item['created_at']}")
    print(f"expired: {len(report.expired)}; skipped: {len(report.skipped)}; "
          f"files of earlier expiries removed: {len(report.swept)}")


def main(argv: list[str] | None = None) -> int:
    from app.storage import RetentionPolicy

    parser = argparse.ArgumentParser(description="Preview or remove media past retention and files ReelForge left behind")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--apply", action="store_true", help="delete eligible files (default: dry-run)")
    mode_group.add_argument("--dry-run", action="store_true", help="only list what --apply would delete (default)")
    parser.add_argument("--older-than-hours", type=int, default=None,
                        help="one minimum age for partials, temp folders and orphans; at least 24 "
                             "(default: the retention policy, 1, 3 and 3 days)")
    parser.add_argument("--orphans", action="store_true",
                        help="also remove files with no asset row (reads the asset table)")
    parser.add_argument("--intermediates", action="store_true",
                        help="also expire intermediate media past REELFORGE_RETENTION_INTERMEDIATE_DAYS "
                             "(reads and writes the asset table)")
    parser.add_argument("--usage", action="store_true", help="print storage per workspace, then exit")
    args = parser.parse_args(argv)
    if args.older_than_hours is not None and args.older_than_hours < 24:
        parser.error("--older-than-hours must be at least 24")
    if args.usage:
        from app.db import Session
        with Session() as db:
            for item in storage_usage(db):
                print(f"{item['workspace_id']}  {item['bytes']:>14,d} / {item['quota_bytes']:>14,d} bytes  "
                      f"{item['percent']:>5.1f}% {item['level']:<8}  {item['files']:>6d} files  {item['name']}")
        return 0
    policy = RetentionPolicy.from_environment()
    hours = (lambda days: args.older_than_hours) if args.older_than_hours is not None else (lambda days: max(24, days * 24))
    root, workspace_ids = configured_media_context()
    mode = "apply" if args.apply else "dry-run"
    report = cleanup_stale_parts(root, workspace_ids, apply=args.apply,
                                 minimum_age_hours=hours(policy.partial_days))
    _print_report("stale ReelForge partial(s)", mode, root, report)
    if root.exists():
        _print_report("worker temp folder(s)", mode, root,
                      cleanup_temp_folders(root, apply=args.apply, minimum_age_hours=hours(policy.temp_days)))
    if args.orphans:
        _print_report("orphan media file(s)", mode, root,
                      cleanup_orphans(root, workspace_ids, configured_asset_check(), apply=args.apply,
                                      minimum_age_hours=hours(policy.orphan_days)))
    if args.intermediates:
        _print_expiry(mode, expire_intermediates(apply=args.apply, policy=policy))
    return 0


if __name__ == "__main__":
    from app.runtime_env import load_runtime_env

    load_runtime_env()  # optional legacy file; storage settings now come from Admin → System settings
    system_config.activate()
    raise SystemExit(main())
