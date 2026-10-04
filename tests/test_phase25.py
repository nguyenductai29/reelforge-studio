"""Phase 25: backup, recovery and operations.

* retention: newest per day (14), ISO week (8) and month (6); the newest dump is never removed; nothing but
  ``reelforge-*.dump|sqlite3`` files is ever touched, and nothing at all after a failed run;
* a run writes a ``.partial`` file, verifies it, renames it, chmods 600 and records ``backup_runs``; a failed run
  removes its partial file, keeps the earlier backups, never shows the database password;
* the restore check refuses any scratch database that could be production; on PostgreSQL (when a test server and
  its pg_dump are available) it rehearses dump → restore → counts → decrypt with a copy of the key;
* the master key backup confirmation (a fingerprint, never the key) and the media checksum manifest;
* systemd units hardened, the backup service and timer, deploy.sh waiting for /health/ready.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from sqlalchemy.engine import make_url

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

from app import backup, restore_check

ROOT = Path(__file__).resolve().parents[1]


def stamped(directory: Path, moment: datetime) -> Path:
    path = directory / f"reelforge-{moment:%Y%m%d-%H%M%S}.dump"
    path.write_bytes(b"dump")
    return path


class RetentionTest(unittest.TestCase):
    def test_daily_weekly_monthly_and_the_newest(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            start = datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc)
            for day in range(300):
                stamped(folder, start + timedelta(days=day))
            stamped(folder, start + timedelta(days=299, hours=5))  # a second dump on the newest day
            (folder / "notes.txt").write_text("keep me")
            (folder / "reelforge-20260101-000000.dump.partial").write_bytes(b"half")
            files = backup.backups(folder)
            keep = backup.keep_set(files, backup.Retention(14, 8, 6))
            days = {stamp.date() for path, stamp in files if path in keep}
            self.assertIn(files[0][0], keep)  # the newest
            self.assertNotIn(files[1][0], keep)  # the older dump of the same day
            newest = files[0][1]
            self.assertEqual(len([d for d in days if newest.date() - d < timedelta(days=14)]), 14)
            weeks = {stamp.isocalendar()[:2] for path, stamp in files if path in keep}
            months = {(stamp.year, stamp.month) for path, stamp in files if path in keep}
            self.assertGreaterEqual(len(weeks), 8)
            self.assertEqual(len(months), 6)
            self.assertLessEqual(len(keep), 14 + 8 + 6)
            removed = backup.prune(folder, backup.Retention(14, 8, 6), dry_run=True)
            self.assertEqual(len(list(folder.glob("reelforge-*.dump"))), 301)  # dry run
            self.assertEqual(sorted(backup.prune(folder, backup.Retention(14, 8, 6))), sorted(removed))
            self.assertEqual(len(list(folder.glob("reelforge-*.dump"))), len(keep))
            self.assertTrue((folder / "notes.txt").exists())
            self.assertTrue((folder / "reelforge-20260101-000000.dump.partial").exists())

    def test_a_single_backup_is_always_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            only = stamped(Path(directory), datetime(2020, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(backup.prune(Path(directory), backup.Retention(1, 0, 0)), [])
            self.assertTrue(only.exists())


class SafetyTest(unittest.TestCase):
    def test_password_only_in_the_environment(self):
        url = make_url("postgresql+psycopg://studio:p%40ss-SECRET@db.example:5433/reelforge?sslmode=require")
        args, env = backup.libpq(url)
        self.assertEqual(args, ["--host", "db.example", "--port", "5433", "--username", "studio"])
        self.assertEqual(env["PGPASSWORD"], "p@ss-SECRET")
        self.assertEqual(env["PGSSLMODE"], "require")
        self.assertNotIn("SECRET", " ".join(args))
        self.assertEqual(backup._scrub("could not connect with p@ss-SECRET", url), "could not connect with [redacted]")

    def test_scratch_database_cannot_be_production(self):
        production = make_url("postgresql+psycopg://u@127.0.0.1:5432/reelforge_studio_db")
        problem = restore_check.scratch_problem
        self.assertIsNone(problem(make_url("postgresql+psycopg://u@127.0.0.1:5432/reelforge_restore_test"), production))
        self.assertIn("must contain", problem(make_url("postgresql+psycopg://u@127.0.0.1/other"), production))
        self.assertIn("production", problem(make_url("postgresql+psycopg://u@127.0.0.1/reelforge_test"),
                                            make_url("postgresql+psycopg://u@127.0.0.1/reelforge_test")))
        self.assertIn("PostgreSQL", problem(make_url("sqlite:///restore.db"), production))

    def test_archive_check_reports_a_missing_or_unreadable_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(restore_check.check(Path(directory) / "none.dump")["ok"])
            junk = Path(directory) / "junk.dump"
            junk.write_bytes(b"not an archive")
            report = restore_check.check(junk, pg_restore="pg_restore_missing_binary")
            self.assertFalse(report["ok"])
            self.assertIn("archive unreadable", report["error"])


OPERATIONS = r'''
import os, stat, tempfile
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url
from app import alerts, backup, media_manifest, readiness
from app.models import Asset, BackupRun
target = Path(tempfile.mkdtemp())

# A development (SQLite) backup: the whole path works locally.
first = backup.run(directory=target)
assert first["ok"] and first["file"].endswith(".sqlite3") and first["entries"] > 10, first
if os.name == "posix":
    assert stat.S_IMODE(os.stat(first["file"]).st_mode) == 0o600
# A failing run (no pg_dump here): recorded, its partial file removed, the earlier backup kept, no password shown.
failed = backup.run(directory=target, url=make_url("postgresql+psycopg://user:SECRET-PASS@127.0.0.1:1/x"),
                    pg_dump="pg_dump_missing_binary")
assert not failed["ok"] and "SECRET-PASS" not in failed["error"] and "not found" in failed["error"], failed
assert sorted(path.name for path in target.iterdir()) == [Path(first["file"]).name]
with Session() as db:
    status = backup.status(db)
    runs = db.scalars(select(BackupRun).order_by(BackupRun.id)).all()
assert [run.status for run in runs] == ["succeeded", "failed"] and runs[0].bytes and runs[0].filename
assert status["last_success"]["file"] == Path(first["file"]).name and status["last_failure"]["error"]
admin_view = client.get("/api/admin/backups").json()
assert admin_view["last_success"] and admin_view["runs"][0]["status"] == "failed"
sections = {s["key"]: s["checks"] for s in client.get("/api/admin/readiness").json()["sections"]}
assert sections["backups"][0]["status"] == "warning" and sections["backups"][0]["detail"] == "last_failed"
with Session.begin() as db:
    alerts.evaluate(db)
with Session() as db:
    assert "backup:failed" in {row["key"] for row in alerts.active(db)}

# The master key backup: a confirmation stores a fingerprint, never the key; a new key asks again.
security = {c["key"]: c for c in sections["security"]}
assert security["master_key_backup"]["detail"] == "not_confirmed"
r = client.put("/api/admin/master-key/backup-confirmation", json={"confirmed": True})
assert r.status_code == 200 and r.json()["check"]["status"] == "ok", r.text
with Session() as db:
    stored = db.get(main.SystemSetting, readiness.MASTER_KEY_BACKUP).value
assert os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] not in stored and len(json.loads(stored)["fingerprint"]) == 16
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
security = {c["key"]: c for c in readiness.security_checks(Session())}
assert security["master_key_backup"]["detail"] == "key_changed", security["master_key_backup"]
member = TestClient(app, headers={"Origin": "http://testserver"})
member.post("/api/register", json={"email": "m@example.com", "password": "member-password-1", "workspace_name": "M",
                                   "accept_terms": True})
assert member.put("/api/admin/master-key/backup-confirmation", json={"confirmed": True}).status_code == 403

# The media manifest: sizes and checksums of the files worth keeping.
uploaded = upload("clip.mp4", VALID_MP4, "video/mp4").json()["id"]
with Session() as db:
    kind = db.get(Asset, uploaded).kind
manifest = target / "media.jsonl"
with Session() as db:
    created = media_manifest.create(db, manifest, kinds=(kind,))
assert created["files"] == 1 and created["bytes"] == len(VALID_MP4), created
with Session() as db:
    root = storage.media_root(db)
assert media_manifest.verify(manifest, root)["ok"] == 1
(root / workspace / uploaded).write_bytes(VALID_MP4 + b"x")
assert media_manifest.verify(manifest, root)["problems"] == [{"asset_id": uploaded, "problem": "size"}]
(root / workspace / uploaded).unlink()
assert media_manifest.verify(manifest, root)["problems"] == [{"asset_id": uploaded, "problem": "missing"}]
print("ok")
'''


class OperationsTest(unittest.TestCase):
    def test_backup_runs_status_alerts_key_confirmation_and_manifest(self):
        result = run_program("from app import storage\n" + OPERATIONS)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


def pg_tools() -> tuple[str, str] | None:
    folder = os.environ.get("REELFORGE_TEST_PG_BIN", "")
    dump = shutil.which("pg_dump", path=folder or None) if folder else shutil.which("pg_dump")
    restore = shutil.which("pg_restore", path=folder or None) if folder else shutil.which("pg_restore")
    return (dump, restore) if dump and restore else None


REHEARSAL = r'''
import json, os, sys
from pathlib import Path
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
PG_DUMP, PG_RESTORE, SCRATCH = sys.argv[1], sys.argv[2], sys.argv[3]
config = Config("alembic.ini")
command.downgrade(config, "base")
command.upgrade(config, "head")
key = Path("instance/master.key")
key.write_text(Fernet.generate_key().decode())
os.environ["REELFORGE_MASTER_KEY_FILE"] = str(key.resolve())
from fastapi.testclient import TestClient
import app.main as main
from app import backup, restore_check, system_config
from app.db import Session
system_config.activate()
client = TestClient(main.app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email": "owner@example.com", "password": "long-password-123"}).status_code == 200
assert client.post("/api/projects", json={"title": "Kept", "topic": "x"}).status_code == 201
with Session.begin() as db:
    system_config.save(db, None, secrets={"ai.openai.api_key": ("replace", "sk-REHEARSAL-SENTINEL")}, section="ai")
folder = Path("instance/backups")
result = backup.run(directory=folder, pg_dump=PG_DUMP, pg_restore=PG_RESTORE)
assert result["ok"] and result["file"].endswith(".dump") and result["entries"] > 20, result
dump = Path(result["file"])
assert b"sk-REHEARSAL-SENTINEL" not in dump.read_bytes()  # secrets are ciphertext in the dump
archive = restore_check.check(dump, pg_restore=PG_RESTORE)
assert archive["ok"] and not archive["steps"]["archive"]["missing"], archive
from app.db import url as production
refused = restore_check.check(dump, scratch_url=str(production), pg_restore=PG_RESTORE)
assert not refused["ok"] and "production" in refused["error"] or "must contain" in refused["error"], refused
rehearsal = restore_check.check(dump, scratch_url=SCRATCH, key_file=key, pg_restore=PG_RESTORE)
assert rehearsal["ok"], rehearsal
restored = rehearsal["steps"]["restore"]
assert restored["counts"]["users"] == 1 and restored["counts"]["projects"] == 1, restored
assert restored["migrate_forward"] is False and restored["migration"] == restored["head"]
assert rehearsal["steps"]["decrypt"]["ok"] >= 1 and rehearsal["steps"]["decrypt"]["failed"] == 0, rehearsal
wrong = Path("instance/wrong.key")
wrong.write_text(Fernet.generate_key().decode())
mismatch = restore_check.check(dump, scratch_url=SCRATCH, key_file=wrong, pg_restore=PG_RESTORE)
assert not mismatch["ok"] and mismatch["steps"]["decrypt"]["failed"] >= 1, mismatch
assert "sk-REHEARSAL-SENTINEL" not in json.dumps(rehearsal) + json.dumps(mismatch)
print("ok")
'''


class PostgreSQLRehearsalTest(unittest.TestCase):
    def test_dump_restore_counts_and_decrypt(self):
        url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
        tools = pg_tools()
        if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower() or not tools:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL (and pg_dump on PATH or REELFORGE_TEST_PG_BIN)")
        url = "postgresql+psycopg://" + url[len("postgresql://"):] if url.startswith("postgresql://") else url
        main_url = make_url(url)
        scratch = main_url.set(database=f"{main_url.database}_restore")
        from sqlalchemy import create_engine, text
        admin = create_engine(main_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch.database}"'))
            connection.execute(text(f'CREATE DATABASE "{scratch.database}"'))
        admin.dispose()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({
                "database_url": url, "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            env = {key: value for key, value in os.environ.items() if not key.startswith(("REELFORGE_TOKEN", "OPENAI"))}
            result = subprocess.run([sys.executable, "-c", REHEARSAL, tools[0], tools[1],
                                     scratch.render_as_string(hide_password=False)], cwd=target,
                                    env={**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"},
                                    capture_output=True, text=True, encoding="utf-8", timeout=600)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])


class DeploymentTest(unittest.TestCase):
    def test_units_are_hardened_without_breaking_paths(self):
        units = sorted((ROOT / "deploy" / "systemd").glob("*.service"))
        self.assertGreaterEqual(len(units), 5)
        for unit in units:
            text = unit.read_text(encoding="utf-8")
            with self.subTest(unit=unit.name):
                for directive in ("NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=full",
                                  "ProtectKernelTunables=yes", "RestrictSUIDSGID=yes"):
                    self.assertIn(directive, text)
                # The app lives under /home and writes media under /srv: neither may become read-only.
                self.assertNotIn("ProtectHome", text)
                self.assertNotIn("ProtectSystem=strict", text)

    def test_backup_service_timer_and_scripts(self):
        service = (ROOT / "deploy" / "systemd" / "reelforge-backup.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.backup run", service)
        self.assertIn("Type=oneshot", service)
        self.assertIn("UMask=0077", service)
        timer = (ROOT / "deploy" / "systemd" / "reelforge-backup.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 02:30:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn('exec "$PYTHON" -m app.backup run "$@"',
                      (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8"))
        self.assertIn('exec "$PYTHON" -m app.restore_check "$@"',
                      (ROOT / "deploy" / "restore-check.sh").read_text(encoding="utf-8"))
        journald = (ROOT / "deploy" / "journald" / "reelforge.conf").read_text(encoding="utf-8")
        self.assertIn("SystemMaxUse=", journald)
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8000/health/ready", deploy)
        self.assertIn("reelforge-backup.timer", deploy)


if __name__ == "__main__":
    unittest.main()
