"""Migration 0017: notifications, support tickets/messages and the verification checklist, from 0016 and back.

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
command.upgrade(config, "0016_storage_lifecycle")
NOW = "2026-09-01 00:00:00"
with engine.begin() as connection:
    connection.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) "
                            "VALUES ('u1', 'owner@example.com', 'x', true, true)"))
    connection.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) "
                            "VALUES ('w1', 'Studio', 'u1', 'trial', :now)"), {"now": NOW})

def rows(table, key):
    with engine.connect() as connection:
        return sorted((dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {table}"))),
                      key=lambda row: row[key])

before = {"users": rows("users", "id"), "workspaces": rows("workspaces", "id"), "plans": rows("plans", "code")}
command.upgrade(config, "head")
inspector = inspect(engine)
for table in ("notifications", "support_tickets", "support_messages", "verification_checks"):
    assert inspector.has_table(table), table
def same_columns(current, earlier):
    return [{column: row[column] for column in earlier[0]} for row in current] if earlier else current

for table, key in (("users", "id"), ("workspaces", "id"), ("plans", "code")):
    assert same_columns(rows(table, key), before[table]) == before[table], table
assert {"ix_notifications_user_created", "ix_notifications_user_read"} <= {
    i["name"] for i in inspector.get_indexes("notifications")}
with engine.begin() as connection:
    for title in ("one", "two"):
        connection.execute(text("INSERT INTO notifications (user_id, workspace_id, type, title, message, created_at) "
                                "VALUES ('u1', 'w1', 'run.completed', :title, '', :now)"), {"title": title, "now": NOW})
    connection.execute(text("INSERT INTO notifications (user_id, type, title, message, dedupe_key, created_at) "
                            "VALUES ('u1', 'support.new', 'x', '', 'key-1', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO support_tickets (id, workspace_id, created_by_user_id, subject, category, status, "
                            "priority, created_at, updated_at) VALUES ('t1', 'w1', 'u1', 'Help', 'bug', 'open', "
                            "'normal', :now, :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO support_messages (id, ticket_id, author_user_id, author_type, body, created_at) "
                            "VALUES ('m1', 't1', 'u1', 'user', 'It broke', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO verification_checks (key, verified_at, verified_by_user_id, note, updated_at) "
                            "VALUES ('ffmpeg_verified', :now, 'u1', 'ok', :now)"), {"now": NOW})
# Integer ids increase (the SSE event ids); a dedupe key is unique per user; enums are checked.
ids = [row["id"] for row in rows("notifications", "id")]
assert ids == sorted(ids) and len(set(ids)) == 3, ids
for bad in ("INSERT INTO notifications (user_id, type, title, message, dedupe_key, created_at) "
            "VALUES ('u1', 'support.new', 'y', '', 'key-1', '2026-09-01')",
            "INSERT INTO support_tickets (id, workspace_id, created_by_user_id, subject, category, status, priority, "
            "created_at, updated_at) VALUES ('t2', 'w1', 'u1', 'x', 'crm', 'open', 'normal', '2026-09-01', '2026-09-01')",
            "INSERT INTO support_messages (id, ticket_id, author_user_id, author_type, body, created_at) "
            "VALUES ('m2', 't1', 'u1', 'robot', 'x', '2026-09-01')"):
    try:
        with engine.begin() as connection:
            connection.execute(text(bad))
        raise AssertionError(f"the database accepted: {bad}")
    except AssertionError:
        raise
    except Exception as exc:
        assert any(word in str(exc).lower() for word in ("unique", "check", "constraint", "duplicate")), exc

command.downgrade(config, "0016_storage_lifecycle")
inspector = inspect(engine)
assert not any(inspector.has_table(t) for t in ("notifications", "support_tickets", "support_messages",
                                                "verification_checks"))
assert rows("users", "id") == before["users"] and rows("plans", "code") == before["plans"]
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


class Phase18MigrationTest(unittest.TestCase):
    def test_every_revision_id_fits_the_postgresql_version_column(self):
        # alembic_version.version_num is VARCHAR(32); SQLite ignores the length, PostgreSQL refuses a longer ID.
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config()
        config.set_main_option("script_location", str(ROOT / "migrations"))
        for script in ScriptDirectory.from_config(config).walk_revisions():
            with self.subTest(revision=script.revision):
                self.assertLessEqual(len(script.revision), 32)

    def test_sqlite_upgrade_from_0016_and_downgrade_keep_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn("ok", result.stdout)

    def test_postgresql_upgrade_from_0016_and_downgrade_keep_existing_rows(self):
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
