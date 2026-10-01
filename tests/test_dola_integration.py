"""Dola is an explicit opt-in, privately copied video provider."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DolaIntegrationTest(unittest.TestCase):
    def test_opt_in_readiness_queue_and_private_worker_result(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({
                "database_url": f"sqlite:///{target}/instance/test.db",
                "frontend_origin": "http://localhost:3000", "secure_cookies": False,
            }))
            program = r'''
import os
os.environ["DOLA_EXPERIMENTAL_ENABLED"] = "0"
os.environ["DOLA_API_KEY"] = "local-test-key"
os.environ["DOLA_BASE_URL"] = "http://127.0.0.1:8000"
os.environ["DOLA_MEDIA_BASE_URL"] = "http://127.0.0.1:8000"
os.environ["DOLA_MAX_JOB_AGE_SECONDS"] = "3600"
os.environ["VIDEO_CREDITS_PER_CLIP"] = "10"
from datetime import datetime, timedelta, timezone
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app.models import Asset, CreditAccount, UsageEvent, WorkflowJob
from app.providers.dola import JobStatus, Submission, VideoResult
from app import usage, video_worker
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"Clip", "topic":"Rừng sương"}).json()["id"]
workflow = client.post("/api/workflows", json={"name":"Dola clip"}).json()["id"]
graph = {"nodes":[{"id":"idea","type":"idea","x":0,"y":0},
                  {"id":"video","type":"video","x":100,"y":0},
                  {"id":"review","type":"review","x":200,"y":0}],
         "edges":[{"source":"idea","target":"video"},{"source":"video","target":"review"}]}
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
tool = client.post("/api/ai-tools", json={"task":"video","provider":"dola","model":"seedance-2.5"})
assert tool.status_code == 201, tool.text
tool_id = tool.json()["id"]
with Session.begin() as db: usage.post_credit(db, workspace, 20, "test", "fund-dola")
def readiness():
    response = client.get(f"/api/workflows/{workflow}/readiness?tool_id={tool_id}")
    assert response.status_code == 200, response.text
    return response.json()
assert not readiness()["runnable"]
assert next(s for s in readiness()["steps"] if s["task"] == "video")["status"] == "experimental_disabled"
disabled = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":tool_id})
assert disabled.status_code == 201 and disabled.json()["status"] == "blocked", disabled.text
with Session() as db:
    assert db.get(CreditAccount, workspace).balance == 20
    assert db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == disabled.json()["id"])) is None
os.environ["DOLA_EXPERIMENTAL_ENABLED"] = "1"
del os.environ["DOLA_API_KEY"]
assert next(s for s in readiness()["steps"] if s["task"] == "video")["status"] == "missing_key"
os.environ["DOLA_API_KEY"] = "local-test-key"
del os.environ["DOLA_BASE_URL"]
assert next(s for s in readiness()["steps"] if s["task"] == "video")["status"] == "missing_config"
os.environ["DOLA_BASE_URL"] = "http://127.0.0.1:8000"
os.environ["DOLA_MAX_JOB_AGE_SECONDS"] = "bad"
assert next(s for s in readiness()["steps"] if s["task"] == "video")["status"] == "invalid_config"
os.environ["DOLA_MAX_JOB_AGE_SECONDS"] = "3600"
assert readiness()["runnable"]
response = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":tool_id})
assert response.status_code == 201, response.text
run = response.json()
assert run["status"] == "running" and run["steps"][1]["status"] == "queued", run
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
    assert job is not None
    assert (job.payload["provider"], job.payload["model_id"], job.payload["duration"],
            job.payload["resolution"], job.payload["generate_audio"]) == (
            "dola", "seedance-2.5", "10s", "auto", None)
    assert db.get(CreditAccount, workspace).balance == 10
class FakeDola:
    submitted = 0
    def submit(self, request):
        self.submitted += 1
        assert (request.model_id, request.prompt, request.aspect_ratio,
                request.duration, request.resolution, request.generate_audio) == (
                "seedance-2.5", "Rừng sương", "9:16", "10s", "auto", None)
        task = "video_1234567890abcdef1234567890abcdef"
        url = f"http://127.0.0.1:8000/v1/videos/{task}"
        return Submission("seedance-2.5", task, url, url)
    def status(self, submission): return JobStatus("completed")
    def result(self, submission): return VideoResult("http://127.0.0.1:8000/videos/clip.mp4")
fake = FakeDola()
def download(url, target):
    assert url == "http://127.0.0.1:8000/videos/clip.mp4"
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=fake, download=download, poll_seconds=0)
in_flight = client.get(f"/api/workflow-runs/{run['id']}").json()
assert "submission" not in in_flight["steps"][1]["output"]
assert "http://127.0.0.1:8000/" not in str(in_flight)
assert video_worker.run_one(client=fake, download=download, poll_seconds=0)
updated = client.get(f"/api/workflow-runs/{run['id']}").json()
assert updated["status"] == "awaiting_review", updated
assert fake.submitted == 1
asset_id = updated["steps"][1]["output"]["asset_id"]
assert "video_url" not in str(updated) and "/videos/clip.mp4" not in str(updated)
assert client.get(f"/api/assets/{asset_id}").status_code == 200
with Session() as db:
    asset = db.get(Asset, asset_id)
    assert (asset.workspace_id, asset.provider, asset.model) == (workspace, "dola", "seedance-2.5")
    assert db.scalar(select(UsageEvent.tool).where(UsageEvent.reference == f"video:{asset.step_id}:single")) == "dola/video"
    assert db.get(CreditAccount, workspace).balance == 10
second = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":tool_id})
assert second.status_code == 201 and second.json()["status"] == "running", second.text
os.environ["DOLA_EXPERIMENTAL_ENABLED"] = "0"
assert video_worker.run_one(client=fake, download=download, poll_seconds=0)
assert client.get(f"/api/workflow-runs/{second.json()['id']}").json()["status"] == "failed"
with Session() as db: assert db.get(CreditAccount, workspace).balance == 10
assert fake.submitted == 1
os.environ["DOLA_EXPERIMENTAL_ENABLED"] = "1"
stalled = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":tool_id})
assert stalled.status_code == 201 and stalled.json()["status"] == "running", stalled.text
class StalledDola(FakeDola):
    polls = 0
    def status(self, submission):
        self.polls += 1
        return JobStatus("running")
stalled_client = StalledDola()
assert video_worker.run_one(client=stalled_client, download=download, poll_seconds=0)
assert video_worker.run_one(client=stalled_client, download=download, poll_seconds=0)
assert stalled_client.polls == 1
with Session.begin() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == stalled.json()["id"]))
    job.created_at = datetime.now(timezone.utc) - timedelta(seconds=3601)
assert video_worker.run_one(client=stalled_client, download=download, poll_seconds=0)
expired = client.get(f"/api/workflow-runs/{stalled.json()['id']}").json()
assert expired["status"] == "needs_attention", expired
assert stalled_client.submitted == 1 and stalled_client.polls == 1
assert "kiểm tra gateway" in expired["steps"][1]["detail"]
assert client.post(f"/api/workflow-runs/{stalled.json()['id']}/retry").status_code == 409
with Session() as db: assert db.get(CreditAccount, workspace).balance == 0
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)},
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-5000:])
