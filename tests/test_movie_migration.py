"""Migration 0027_movie_sources in both directions, on SQLite and (with REELFORGE_TEST_DATABASE_URL) PostgreSQL:
the two tables, their constraints and indexes, ``assets.movie_source_id``; existing rows are kept both ways."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

MIGRATION = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
config = Config("alembic.ini")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
command.upgrade(config, "0026_change_production_origin")
NOW = "2026-10-04 10:00:00"
with engine.begin() as c:
    c.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) VALUES ('u1', 'a@b.c', 'x', true, true)"))
    c.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) VALUES ('w1', 'Studio', 'u1', 'trial', :now)"),
              {"now": NOW})
    c.execute(text("INSERT INTO projects (id, workspace_id, title, topic, status, created_at) "
                   "VALUES ('p1', 'w1', 'Film', '', 'draft', :now)"), {"now": NOW})
    c.execute(text("INSERT INTO workflows (id, workspace_id, name, definition) VALUES ('f1', 'w1', 'Review', '{}')"))
    c.execute(text("INSERT INTO workflow_runs (id, workspace_id, workflow_id, project_id, graph_snapshot, status, created_at) "
                   "VALUES ('r1', 'w1', 'f1', 'p1', '{}', 'running', :now)"), {"now": NOW})
    c.execute(text("INSERT INTO assets (id, workspace_id, filename, content_type, bytes, created_at) "
                   "VALUES ('a1', 'w1', 'final.mp4', 'video/mp4', 10, :now)"), {"now": NOW})
command.upgrade(config, "0027_movie_sources")
inspector = inspect(engine)
assert {"movie_sources", "movie_source_uses"} <= set(inspector.get_table_names())
assert "movie_source_id" in {column["name"] for column in inspector.get_columns("assets")}
indexes = {index["name"] for index in inspector.get_indexes("movie_sources")}
assert {"ix_movie_sources_workspace", "ix_movie_sources_work", "ix_movie_sources_expires", "ix_movie_sources_drive_file",
        "ix_movie_sources_project"} <= indexes, indexes
assert "ix_movie_source_uses_run" in {index["name"] for index in inspector.get_indexes("movie_source_uses")}
assert "ix_assets_movie_source_id" in {index["name"] for index in inspector.get_indexes("assets")}

ROW = ("INSERT INTO movie_sources (id, workspace_id, project_id, created_by_user_id, source_type, original_name, status, "
       "created_at, updated_at, delete_after_success, delete_grace_hours, attempt_count) "
       "VALUES (:id, 'w1', 'p1', 'u1', :type, 'film.mp4', :status, :now, :now, true, 24, 0)")
with engine.begin() as c:
    c.execute(text(ROW), {"id": "m1", "type": "local", "status": "ready", "now": NOW})
    c.execute(text("INSERT INTO movie_source_uses (id, movie_source_id, workspace_id, run_id, created_at) "
                   "VALUES ('use1', 'm1', 'w1', 'r1', :now)"), {"now": NOW})
    c.execute(text("UPDATE assets SET movie_source_id = 'm1' WHERE id = 'a1'"))
for values in ({"id": "m2", "type": "local", "status": "maybe"}, {"id": "m3", "type": "ftp", "status": "ready"}):
    try:
        with engine.begin() as c:
            c.execute(text(ROW), {**values, "now": NOW})
    except IntegrityError:
        continue
    raise AssertionError(f"accepted {values}")
try:
    with engine.begin() as c:
        c.execute(text("INSERT INTO movie_source_uses (id, movie_source_id, workspace_id, run_id, created_at) "
                       "VALUES ('use2', 'm1', 'w1', 'r1', :now)"), {"now": NOW})
except IntegrityError:
    pass
else:
    raise AssertionError("one run used the same source twice")

command.downgrade(config, "0026_change_production_origin")
inspector = inspect(engine)
assert not {"movie_sources", "movie_source_uses"} & set(inspector.get_table_names())
assert "movie_source_id" not in {column["name"] for column in inspector.get_columns("assets")}
with engine.connect() as c:
    assert c.execute(text("SELECT filename FROM assets WHERE id = 'a1'")).scalar() == "final.mp4"
command.upgrade(config, "head")
with engine.connect() as c:
    assert c.execute(text("SELECT movie_source_id FROM assets WHERE id = 'a1'")).scalar() is None
    assert c.execute(text("SELECT count(*) FROM movie_sources")).scalar() == 0
print("ok")
'''


def run_migration(database_url: str, directory: str) -> subprocess.CompletedProcess:
    target = Path(directory)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "instance").mkdir()
    (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": database_url}))
    return subprocess.run([sys.executable, "-c", MIGRATION], cwd=target, env={**os.environ, "PYTHONPATH": str(target)},
                          capture_output=True, text=True)


def postgresql_url() -> str:
    url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
        return ""
    return "postgresql+psycopg://" + url[len("postgresql://"):] if url.startswith("postgresql://") else url


class MovieMigrationTest(unittest.TestCase):
    def test_sqlite_up_and_down(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_postgresql_up_and_down(self):
        url = postgresql_url()
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(url, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_revision_id_fits_postgresql(self):
        self.assertLessEqual(len("0027_movie_sources"), 32)
        self.assertTrue((ROOT / "migrations" / "versions" / "0027_movie_sources.py").is_file())


if __name__ == "__main__":
    unittest.main()
