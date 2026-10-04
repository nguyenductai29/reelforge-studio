"""Migration 0013 adds publication privacy, tags and YouTube's reported result, keeping existing rows as they were."""
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
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
config = Config("alembic.ini")
command.upgrade(config, "0012_job_reconciliation")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
with engine.begin() as connection:
    connection.execute(text("PRAGMA foreign_keys=OFF"))
    connection.execute(text(
        "INSERT INTO publications (id, workspace_id, run_id, asset_id, job_id, channel, title, description, state, "
        "remote_id, upload_session_ciphertext, last_error, created_at, updated_at, finished_at) VALUES "
        "('p1', 'w1', 'r1', 'a1', NULL, 'youtube', 'Old title', 'Old description', 'succeeded', 'abcdefghijk', "
        "NULL, NULL, '2026-09-01 00:00:00', '2026-09-01 00:00:00', '2026-09-01 00:00:00')"))

def rows():
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(text("SELECT * FROM publications"))]

before = rows()
command.upgrade(config, "head")
after = rows()
assert len(after) == 1
assert {key: after[0][key] for key in before[0]} == before[0], (before, after)
assert (after[0]["privacy_status"], after[0]["tags"], after[0]["remote_status"], after[0]["remote_privacy"]) == \
       ("private", "[]", None, None), after
checks = [check["name"] for check in inspect(engine).get_check_constraints("publications")]
assert "ck_publications_privacy" in checks, checks
try:
    with engine.begin() as connection:
        connection.execute(text("UPDATE publications SET privacy_status = 'friends' WHERE id = 'p1'"))
    raise AssertionError("an unknown visibility must be rejected by the database")
except Exception as exc:
    assert "CHECK" in str(exc).upper() or "constraint" in str(exc).lower(), exc
command.downgrade(config, "0012_job_reconciliation")
assert rows() == before
command.upgrade(config, "head")
print("ok")
'''


class PublicationMetadataMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_keep_publications(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            result = subprocess.run([sys.executable, "-c", "import json\n" + PROGRAM], cwd=target,
                                    env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
