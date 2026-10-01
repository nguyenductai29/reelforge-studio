"""Where workspace media lives, for code that runs inside node handlers.

Kept for existing imports: paths, quotas and retention now live in ``app.storage`` (Phase 17),
which also needs no import of the API module (that checks the configured database at import time).
"""
from app.storage import DEFAULT_STORAGE_DIR, asset_path, media_root, stored_bytes  # noqa: F401
