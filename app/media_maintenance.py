"""Conservative cleanup of media files ReelForge left behind, plus a storage usage report.

Run ``python -m app.media_maintenance`` to preview. Only ``--apply`` deletes.
Everything must be at least 24 hours old, and links or junctions are never
followed. Three kinds of leftovers are handled:

* ``.part`` downloads: the exact UUID4 ``<id>.part`` names written by the media
  workers, directly inside existing workspace directories (always checked);
* worker temp folders: ``<media>/.render-tmp/<job>``, ``.source-tmp/<job>`` and
  ``.publish-tmp/<job>.mp4`` left by a crashed render, transcription or upload
  (always checked; a folder containing any link is skipped whole);
* orphan files: UUID4-named files in a workspace directory with no asset row
  (only with ``--orphans``, which reads the asset table).

``--usage`` prints stored bytes per workspace from the asset table.
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


_UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_WORKSPACE_RE = re.compile(rf"{_UUID4}\Z")
_PART_RE = re.compile(rf"{_UUID4}\.part\Z")
_ASSET_RE = re.compile(rf"{_UUID4}\Z")
_TEMP_ENTRY_RE = re.compile(rf"{_UUID4}(?:\.mp4)?\Z")
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
    """Stored bytes and file count per workspace, from the asset table (largest first)."""
    from sqlalchemy import func, select
    from app.models import Asset, Workspace

    rows = db.execute(select(Workspace.id, Workspace.name, func.count(Asset.id), func.coalesce(func.sum(Asset.bytes), 0))
                      .outerjoin(Asset, Asset.workspace_id == Workspace.id).group_by(Workspace.id, Workspace.name))
    usage = [{"workspace_id": workspace_id, "name": name, "files": files, "bytes": int(total)}
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
        path = Path(value)
        root = path if path.is_absolute() else ROOT / path
        workspace_ids = list(db.scalars(select(Workspace.id)))
    return root, workspace_ids


def _print_report(label: str, mode: str, root: Path, report: CleanupReport) -> None:
    print(f"{mode}: {len(report.candidates)} {label} under {root}")
    for path in report.candidates:
        action = "deleted" if path in report.deleted else "skipped" if path in report.skipped else "would delete"
        print(f"{action}: {path}")
    print(f"deleted: {len(report.deleted)}; skipped: {len(report.skipped)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview or remove media ReelForge left behind")
    parser.add_argument("--apply", action="store_true", help="delete eligible files (default: dry-run)")
    parser.add_argument("--older-than-hours", type=int, default=24,
                        help="minimum age; must be at least 24 hours (default: 24)")
    parser.add_argument("--orphans", action="store_true",
                        help="also remove files with no asset row (reads the asset table)")
    parser.add_argument("--usage", action="store_true", help="print stored bytes per workspace, then exit")
    args = parser.parse_args(argv)
    if args.older_than_hours < 24:
        parser.error("--older-than-hours must be at least 24")
    if args.usage:
        from app.db import Session
        with Session() as db:
            for item in storage_usage(db):
                print(f"{item['workspace_id']}  {item['bytes']:>14,d} bytes  {item['files']:>6d} files  {item['name']}")
        return 0
    root, workspace_ids = configured_media_context()
    mode = "apply" if args.apply else "dry-run"
    report = cleanup_stale_parts(root, workspace_ids, apply=args.apply,
                                 minimum_age_hours=args.older_than_hours)
    _print_report("stale ReelForge partial(s)", mode, root, report)
    if root.exists():
        _print_report("worker temp folder(s)", mode, root,
                      cleanup_temp_folders(root, apply=args.apply, minimum_age_hours=args.older_than_hours))
    if args.orphans:
        _print_report("orphan media file(s)", mode, root,
                      cleanup_orphans(root, workspace_ids, configured_asset_check(), apply=args.apply,
                                      minimum_age_hours=args.older_than_hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
