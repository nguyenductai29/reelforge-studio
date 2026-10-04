"""Migration 0016: plan storage limits and asset kinds from lineage, from 0015 and back.

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
command.upgrade(config, "0015_admin_payments_profiles")
NOW = "2026-09-01 00:00:00"
STEPS = {"s-render": "render", "s-video": "video", "s-image": "image", "s-voice": "voice", "s-sub": "subtitle",
         "s-cut": "extract_clips", "s-idea": "idea"}
with engine.begin() as connection:
    connection.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) "
                            "VALUES ('u1', 'owner@example.com', 'x', false, true)"))
    connection.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) "
                            "VALUES ('w1', 'Studio', 'u1', 'trial', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO workflows (id, workspace_id, name, definition) VALUES ('f1', 'w1', 'F', '[]')"))
    connection.execute(text("INSERT INTO projects (id, workspace_id, title, topic, status, created_at) "
                            "VALUES ('p1', 'w1', 'P', '', 'draft', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO workflow_runs (id, workspace_id, workflow_id, project_id, graph_snapshot, status, "
                            "created_at) VALUES ('r1', 'w1', 'f1', 'p1', '{}', 'completed', :now)"), {"now": NOW})
    for position, (step_id, node_type) in enumerate(STEPS.items()):
        connection.execute(text("INSERT INTO workflow_run_steps (id, run_id, node_id, node_type, position, status, detail) "
                                "VALUES (:id, 'r1', :id, :type, :position, 'completed', '')"),
                           {"id": step_id, "type": node_type, "position": position})
    assets = [("upload", None, None)] + [(f"a-{step_id}", "r1", step_id) for step_id in STEPS] + [("orphan-run", "r1", None)]
    for asset_id, run_id, step_id in assets:
        connection.execute(text("INSERT INTO assets (id, workspace_id, filename, content_type, bytes, run_id, step_id, "
                                "created_at) VALUES (:id, 'w1', 'f.bin', 'video/mp4', 10, :run, :step, :now)"),
                           {"id": asset_id, "run": run_id, "step": step_id, "now": NOW})

def rows(table, key):
    with engine.connect() as connection:
        return sorted((dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {table}"))),
                      key=lambda row: row[key])

before = {"assets": rows("assets", "id"), "plans": rows("plans", "code")}
command.upgrade(config, "head")
inspector = inspect(engine)
assert {"kind", "expired_at", "expired_reason", "expired_bytes"} <= {c["name"] for c in inspector.get_columns("assets")}
assert "ix_assets_kind_created_at" in {i["name"] for i in inspector.get_indexes("assets")}
for table, key in (("assets", "id"), ("plans", "code")):
    after = rows(table, key)
    assert [{column: row[column] for column in before[table][0]} for row in after] == before[table], table
kinds = {row["id"]: row["kind"] for row in rows("assets", "id")}
assert kinds == {"upload": "source", "a-s-render": "final_render", "a-s-video": "scene_video",
                 "a-s-image": "generated_image", "a-s-voice": "voice", "a-s-sub": "subtitle",
                 "a-s-cut": "extracted_clip", "a-s-idea": "other", "orphan-run": "other"}, kinds
assert all(row["expired_at"] is None and row["bytes"] == 10 for row in rows("assets", "id"))
limits = {row["code"]: row["storage_limit_bytes"] for row in rows("plans", "code")}
assert limits == {"trial": 1024 ** 3, "standard": 10 * 1024 ** 3, "pro": 30 * 1024 ** 3}, limits

# Downgrading drops only the new columns; every row stays.
command.downgrade(config, "0015_admin_payments_profiles")
inspector = inspect(engine)
assert "kind" not in {c["name"] for c in inspector.get_columns("assets")}
assert "storage_limit_bytes" not in {c["name"] for c in inspector.get_columns("plans")}
assert rows("assets", "id") == before["assets"] and rows("plans", "code") == before["plans"]
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


class StorageMigrationTest(unittest.TestCase):
    def test_sqlite_upgrade_from_0015_labels_assets_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn("ok", result.stdout)

    def test_postgresql_upgrade_from_0015_labels_assets_and_keeps_rows(self):
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
