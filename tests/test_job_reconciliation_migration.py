"""Migration 0012 moves reconciliation decisions to one per job, keeping every existing decision."""
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
import os
os.environ.update({"FAL_KEY": "fake-key-only", "VIDEO_CREDITS_PER_CLIP": "10"})
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
config = Config("alembic.ini")
command.upgrade(config, "0011_credit_reconciliation")
from app.main import app
from app.db import Session, engine
from app.models import WorkflowJob
from app import usage, video_worker

client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email": "a@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title": "A", "topic": "Rừng"}).json()["id"]
workflow = client.post("/api/workflows", json={"name": "Clip"}).json()["id"]
assert client.post("/api/ai-tools", json={"task": "video", "provider": "fal", "model": "fal-ai/veo3.1/fast"}).status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 30, "test", "fund")

class Unknown:
    def submit(self, request): raise TimeoutError("lost response")
    def close(self): pass

decided = []
for decision in ("refund", "confirm-charge"):
    run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project}).json()["id"]
    assert video_worker.run_one(client=Unknown(), poll_seconds=0)
    with Session() as db:
        step = db.scalar(select(WorkflowJob.step_id).where(WorkflowJob.run_id == run))
    response = client.post(f"/api/admin/reconciliation/{step}/{decision}", json={"note": "before 0012"})
    assert response.status_code == 200, response.text
    decided.append(step)

def rows():
    with engine.connect() as connection:
        return sorted(tuple(row) for row in connection.execute(text(
            "SELECT step_id, job_id, reservation_id, decision, credits, reconciled_by, reconciled_at, note "
            "FROM credit_reconciliations")))

def shape():
    table = inspect(engine)
    indexes = {index["name"]: index["column_names"] for index in table.get_indexes("credit_reconciliations")}
    return table.get_pk_constraint("credit_reconciliations")["constrained_columns"], indexes

before = rows()
assert len(before) == 2 and shape()[0] == ["step_id"], (before, shape())
command.upgrade(config, "head")
assert rows() == before
primary, indexes = shape()
assert primary == ["job_id"], primary
assert indexes.get("ix_credit_reconciliations_step_id") == ["step_id"], indexes
assert indexes.get("ix_credit_reconciliations_reconciled_at") == ["reconciled_at"], indexes
# Decisions made before the upgrade are still final, and history lists them.
history = client.get("/api/admin/reconciliation?status=resolved").json()
assert sorted(item["step_id"] for item in history["items"]) == sorted(decided), history
assert client.post(f"/api/admin/reconciliation/{decided[0]}/confirm-charge", json={}).status_code == 409
command.downgrade(config, "0011_credit_reconciliation")
assert rows() == before and shape()[0] == ["step_id"], shape()
command.upgrade(config, "head")
assert rows() == before and shape()[0] == ["job_id"]
print("migration 0012 preserved decisions")
'''


class JobReconciliationMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_keep_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            env = {key: value for key, value in os.environ.items() if not key.startswith(("FAL_", "VIDEO_"))}
            result = subprocess.run([sys.executable, "-c", PROGRAM], cwd=target, env={**env, "PYTHONPATH": str(target)},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
