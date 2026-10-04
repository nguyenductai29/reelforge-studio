"""Conservative cleanup of stale video-download partials."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.media_maintenance import cleanup_stale_parts, configured_media_context, main
from app import media_maintenance
from app.models import Base, SystemSetting, User, Workspace


WORKSPACE = "11111111-1111-4111-8111-111111111111"
OTHER_WORKSPACE = "22222222-2222-4222-8222-222222222222"
ASSET = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_ASSET = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


class MediaMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "media"
        self.root.mkdir()
        self.workspace = self.root / WORKSPACE
        self.workspace.mkdir()

    def file(self, relative: str, *, age_hours: float = 25) -> Path:
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"partial")
        modified = (NOW - timedelta(hours=age_hours)).timestamp()
        os.utime(path, (modified, modified))
        return path

    def test_dry_run_by_default_then_apply_removes_only_old_reelforge_partials(self):
        old = self.file(f"{ASSET}.part")
        fresh = self.file(f"{OTHER_ASSET}.part", age_hours=23)
        boundary = self.file("cccccccc-cccc-4ccc-8ccc-cccccccccccc.part", age_hours=24)
        unrelated = self.file("notes.part")
        complete = self.file(OTHER_ASSET)
        nested = self.file(f"nested/{OTHER_ASSET}.part")
        unknown = self.root / OTHER_WORKSPACE
        unknown.mkdir()
        unknown_part = unknown / f"{OTHER_ASSET}.part"
        unknown_part.write_bytes(b"partial")
        os.utime(unknown_part, ((NOW - timedelta(days=3)).timestamp(),) * 2)

        preview = cleanup_stale_parts(self.root, [WORKSPACE], now=NOW)
        self.assertEqual(preview.candidates, (old,))
        self.assertEqual(preview.deleted, ())
        self.assertTrue(old.exists())

        applied = cleanup_stale_parts(self.root, [WORKSPACE], apply=True, now=NOW)
        self.assertEqual(applied.candidates, (old,))
        self.assertEqual(applied.deleted, (old,))
        self.assertFalse(old.exists())
        for path in (fresh, boundary, unrelated, complete, nested, unknown_part):
            self.assertTrue(path.exists(), path)

    def test_symlinked_file_and_workspace_are_never_followed(self):
        outside = Path(self.temp.name) / f"{ASSET}.part"
        outside.write_bytes(b"outside")
        os.utime(outside, ((NOW - timedelta(days=3)).timestamp(),) * 2)
        symlink_file = self.workspace / f"{ASSET}.part"
        symlink_workspace = self.root / OTHER_WORKSPACE
        try:
            symlink_file.symlink_to(outside)
            symlink_workspace.symlink_to(outside.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable on this host: {exc}")
        report = cleanup_stale_parts(self.root, [WORKSPACE, OTHER_WORKSPACE], apply=True, now=NOW)
        self.assertEqual(report.candidates, ())
        self.assertTrue(symlink_file.is_symlink())
        self.assertTrue(symlink_workspace.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_rejects_unsafe_workspace_identifier(self):
        with self.assertRaisesRegex(ValueError, "workspace"):
            cleanup_stale_parts(self.root, ["../outside"], apply=True, now=NOW)

    def test_missing_media_root_is_an_empty_dry_run_or_apply(self):
        missing = self.root / "not-created-yet"
        self.assertEqual(cleanup_stale_parts(missing, [WORKSPACE], now=NOW).candidates, ())
        self.assertEqual(cleanup_stale_parts(missing, [WORKSPACE], apply=True, now=NOW).deleted, ())
        self.assertFalse(missing.exists())

    def test_changed_file_is_skipped_between_scan_and_deletion(self):
        old = self.file(f"{ASSET}.part")
        original_scan = media_maintenance._scan

        def scan_then_refresh(root, workspace_ids, cutoff):
            candidates = original_scan(root, workspace_ids, cutoff)
            refreshed = NOW.timestamp()
            os.utime(old, (refreshed, refreshed))
            return candidates

        with patch("app.media_maintenance._scan", side_effect=scan_then_refresh):
            report = cleanup_stale_parts(self.root, [WORKSPACE], apply=True, now=NOW)
        self.assertEqual(report.candidates, (old,))
        self.assertEqual(report.deleted, ())
        self.assertEqual(report.skipped, (old,))
        self.assertTrue(old.exists())

    def test_does_not_require_python_312_path_is_junction(self):
        old = self.file(f"{ASSET}.part")
        with patch.object(Path, "is_junction", side_effect=AssertionError("Python 3.11 has no is_junction"), create=True):
            report = cleanup_stale_parts(self.root, [WORKSPACE], now=NOW)
        self.assertEqual(report.candidates, (old,))

    def test_rejects_linked_root(self):
        alias = Path(self.temp.name) / "media-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable on this host: {exc}")
        with self.assertRaisesRegex(ValueError, "link"):
            cleanup_stale_parts(alias, [WORKSPACE], apply=True, now=NOW)

    def test_cli_is_dry_run_until_apply_is_explicit(self):
        old = self.file(f"{ASSET}.part")
        stale_at = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
        os.utime(old, (stale_at, stale_at))
        output = StringIO()
        with patch("app.media_maintenance.configured_media_context", return_value=(self.root, [WORKSPACE])):
            with redirect_stdout(output):
                self.assertEqual(main([]), 0)
            self.assertTrue(old.exists())
            self.assertIn("dry-run", output.getvalue())
            output.seek(0)
            output.truncate()
            with redirect_stdout(output):
                self.assertEqual(main(["--apply"]), 0)
        self.assertFalse(old.exists())
        self.assertIn("deleted", output.getvalue())

    def test_configured_context_uses_database_media_setting_and_known_workspaces(self):
        engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'settings.db'}")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        Session = sessionmaker(engine)
        with Session.begin() as db:
            db.add(SystemSetting(key="storage_dir", value=json.dumps("custom/media")))
            db.add(User(id="user-1", email="maintenance@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id=WORKSPACE, owner_id="user-1", name="Studio"))
        fake_db = types.ModuleType("app.db")
        fake_db.ROOT = Path(self.temp.name)
        fake_db.Session = Session
        with patch.dict(sys.modules, {"app.db": fake_db}):
            root, ids = configured_media_context()
        self.assertEqual(root, Path(self.temp.name) / "custom" / "media")
        self.assertEqual(ids, [WORKSPACE])


if __name__ == "__main__":
    unittest.main()
