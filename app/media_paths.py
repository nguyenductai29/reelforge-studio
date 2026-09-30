"""Where workspace media lives and how much a workspace may store, for code that runs inside node handlers.

These mirror ``media_root`` and ``workspace_media_quota`` in ``app.main`` without
importing the API module, which checks the configured database at import time.
Handlers that write files synchronously (Subtitle) or check render inputs use them.
"""
import json
import os
from pathlib import Path

from sqlalchemy import func, select

from app.models import Asset, SystemSetting
from app.runtime_env import ROOT

DEFAULT_STORAGE_DIR = "instance/media"


def media_root(db) -> Path:
    row = db.get(SystemSetting, "storage_dir")
    path = Path(json.loads(row.value) if row else DEFAULT_STORAGE_DIR)
    return path if path.is_absolute() else ROOT / path


def workspace_media_quota() -> int:
    try:
        quota = int(os.environ.get("WORKSPACE_MEDIA_QUOTA_BYTES", str(1024 * 1024 * 1024)))
    except ValueError as exc:
        raise RuntimeError("WORKSPACE_MEDIA_QUOTA_BYTES must be a positive integer") from exc
    if quota <= 0:
        raise RuntimeError("WORKSPACE_MEDIA_QUOTA_BYTES must be a positive integer")
    return quota


def stored_bytes(db, workspace_id: str) -> int:
    return db.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.workspace_id == workspace_id))


def asset_path(db, workspace_id: str, asset_id: str) -> Path:
    return media_root(db) / workspace_id / asset_id
