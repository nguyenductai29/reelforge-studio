"""Conservative cleanup for abandoned ReelForge video-download `.part` files.

Run ``python -m app.media_maintenance`` to preview. Only ``--apply`` deletes.
The scanner is intentionally non-recursive and accepts only the exact UUID4
filenames written by ``app.video_worker`` in existing workspace directories.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import stat
from typing import Iterable


_UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_WORKSPACE_RE = re.compile(rf"{_UUID4}\Z")
_PART_RE = re.compile(rf"{_UUID4}\.part\Z")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview or remove stale ReelForge video-download partials")
    parser.add_argument("--apply", action="store_true", help="delete eligible files (default: dry-run)")
    parser.add_argument("--older-than-hours", type=int, default=24,
                        help="minimum age; must be at least 24 hours (default: 24)")
    args = parser.parse_args(argv)
    if args.older_than_hours < 24:
        parser.error("--older-than-hours must be at least 24")
    root, workspace_ids = configured_media_context()
    report = cleanup_stale_parts(root, workspace_ids, apply=args.apply,
                                 minimum_age_hours=args.older_than_hours)
    mode = "apply" if args.apply else "dry-run"
    print(f"{mode}: {len(report.candidates)} stale ReelForge partial(s) under {root}")
    for path in report.candidates:
        action = "deleted" if path in report.deleted else "skipped" if path in report.skipped else "would delete"
        print(f"{action}: {path}")
    print(f"deleted: {len(report.deleted)}; skipped: {len(report.skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
