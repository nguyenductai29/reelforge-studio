"""A configured video step queues a durable, isolated run with frozen input."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VideoWorkflowTest(unittest.TestCase):
    def test_video_run_queues_with_prompt_snapshot_and_credit_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            program = r'''
import os
os.environ["FAL_KEY"] = "test-key"
os.environ["RUNWARE_API_KEY"] = "test-key"
os.environ["VIDEO_CREDITS_PER_CLIP"] = "10"
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app.models import WorkflowJob, CreditAccount
from app import usage
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
dashboard = client.get("/api/dashboard").json()
workspace = dashboard["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Cảnh rừng ban đêm"}).json()["id"]
workflow = client.post("/api/workflows", json={"name":"Clip"}).json()["id"]
graph = {"nodes":[{"id":"idea","type":"idea","x":0,"y":0},{"id":"video","type":"video","x":100,"y":0},{"id":"review","type":"review","x":200,"y":0}], "edges":[{"source":"idea","target":"video"},{"source":"video","target":"review"}]}
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
assert client.post("/api/ai-tools", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast"}).status_code == 201
body = {"project_id": project, "prompt":"  Mưa trên mái nhà, khung dọc  "}
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
assert not readiness["runnable"]
assert next(step for step in readiness["steps"] if step["task"] == "video")["status"] == "insufficient_credits"
assert client.post(f"/api/workflows/{workflow}/runs", json=body).status_code == 402
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-video")
assert client.get(f"/api/workflows/{workflow}/readiness").json()["runnable"]
response = client.post(f"/api/workflows/{workflow}/runs", json=body)
assert response.status_code == 201, response.text
run = response.json()
assert run["status"] == "running", run
assert [step["status"] for step in run["steps"]] == ["completed", "queued", "skipped"]
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
    assert job is not None
    assert job.payload["prompt"] == "Mưa trên mái nhà, khung dọc"
    assert job.payload["model_id"] == "fal-ai/veo3.1/fast"
    assert db.get(CreditAccount, workspace).balance == 0
runware_tool = client.post("/api/ai-tools", json={"task":"video","provider":"runware","model":"bytedance:seedance@2.5"})
assert runware_tool.status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-second-video")
second = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":runware_tool.json()["id"]})
assert second.status_code == 201, second.text
with Session() as db:
    selected = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == second.json()["id"]))
    assert selected.payload["provider"] == "runware"
    assert selected.payload["model_id"] == "bytedance:seedance@2.5"
assert client.post("/api/register", json={"email":"b@example.com","password":"long-password-123","workspace_name":"B"}).status_code == 201
assert client.get(f"/api/workflow-runs/{run['id']}").status_code == 404
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
