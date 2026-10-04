"""Migration 0014: channel connections, scheduling columns and states, heartbeats and clip lineage.

Runs on a disposable SQLite database; set ``REELFORGE_TEST_DATABASE_URL`` to an isolated
PostgreSQL test database (its name must contain "test") to run the same program there.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
config = Config("alembic.ini")
url = json.load(open("instance/bootstrap.json"))["database_url"]
engine = create_engine(url)
sqlite = engine.dialect.name == "sqlite"
if not sqlite:
    command.downgrade(config, "base")  # the PostgreSQL test database may hold an earlier run
command.upgrade(config, "0013_publication_metadata")
NOW = "2026-09-01 00:00:00"
with engine.begin() as connection:
    if sqlite:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
    else:
        connection.execute(text("SET session_replication_role = replica"))
    connection.execute(text("INSERT INTO assets (id, workspace_id, filename, content_type, bytes, created_at) VALUES "
                            "('a1', 'w1', 'clip.mp4', 'video/mp4', 12, :now)"), {"now": NOW})
    for pid, state in (("p1", "succeeded"), ("p2", "failed")):
        connection.execute(text(
            "INSERT INTO publications (id, workspace_id, run_id, asset_id, job_id, channel, title, description, state, "
            "remote_id, upload_session_ciphertext, last_error, privacy_status, tags, created_at, updated_at, finished_at) "
            "VALUES (:id, 'w1', :run, 'a1', NULL, 'youtube', 'Old', 'Desc', :state, NULL, NULL, NULL, 'private', '[]', "
            ":now, :now, :now)"), {"id": pid, "run": f"r-{pid}", "state": state, "now": NOW})

def rows(table):
    with engine.connect() as connection:
        return sorted((dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {table}"))),
                      key=lambda row: row["id"])

before = {"publications": rows("publications"), "assets": rows("assets")}
command.upgrade(config, "head")
inspector = inspect(engine)
for table in ("channel_connections", "channel_oauth_states", "worker_heartbeats"):
    assert inspector.has_table(table), table
assert {"scheduled_for", "published_at"} <= {c["name"] for c in inspector.get_columns("publications")}
assert "source_asset_id" in {c["name"] for c in inspector.get_columns("assets")}
for table, old in before.items():
    after = rows(table)
    assert [{key: row[key] for key in old[0]} for row in after] == old, (table, old, after)
assert [row["scheduled_for"] for row in rows("publications")] == [None, None]
with engine.begin() as connection:
    connection.execute(text("UPDATE publications SET state = 'scheduled', scheduled_for = :at WHERE id = 'p2'"),
                       {"at": "2026-10-02 08:00:00"})
    connection.execute(text("INSERT INTO worker_heartbeats (worker, host, pid, status, detail, started_at, last_seen_at) "
                            "VALUES ('render_worker', 'h', 1, 'running', NULL, :now, :now)"), {"now": NOW})
for bad in ("UPDATE publications SET state = 'posted' WHERE id = 'p1'",
            "INSERT INTO channel_connections (workspace_id, channel, scope, connected_at, updated_at) "
            "VALUES ('w1', 'myspace', '', '2026-09-01', '2026-09-01')"):
    try:
        with engine.begin() as connection:
            if sqlite:
                connection.execute(text("PRAGMA foreign_keys=OFF"))
            else:
                connection.execute(text("SET session_replication_role = replica"))
            connection.execute(text(bad))
        raise AssertionError(f"the database accepted: {bad}")
    except AssertionError:
        raise
    except Exception as exc:
        assert "check" in str(exc).lower() or "constraint" in str(exc).lower(), exc

# Downgrading keeps every row; states the old schema lacks become failed, with the reason.
command.downgrade(config, "0013_publication_metadata")
downgraded = {row["id"]: row for row in rows("publications")}
assert (downgraded["p2"]["state"], downgraded["p2"]["last_error"]) == ("failed", "downgraded_scheduled"), downgraded
assert downgraded["p1"] == before["publications"][0], downgraded
assert rows("assets") == before["assets"]
assert not inspect(engine).has_table("channel_connections")
command.upgrade(config, "head")
print("ok")
'''


def run(database_url: str, directory: str):
    target = Path(directory)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "instance").mkdir()
    (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": database_url}))
    return subprocess.run([sys.executable, "-c", PROGRAM], cwd=target, env={**os.environ, "PYTHONPATH": str(target)},
                          capture_output=True, text=True)


class ChannelsMigrationTest(unittest.TestCase):
    def test_sqlite_upgrade_and_downgrade_keep_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_postgresql_upgrade_and_downgrade_keep_existing_rows(self):
        url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
            self.skipTest("REELFORGE_TEST_DATABASE_URL must name a PostgreSQL database whose name contains 'test'")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        with tempfile.TemporaryDirectory() as directory:
            result = run(url, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
