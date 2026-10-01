"""The worker resumes a submitted job and stores a private clip exactly once."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VideoWorkerTest(unittest.TestCase):
    def test_submit_poll_store_and_settle(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            program = r'''
import os
from datetime import datetime, timedelta, timezone
os.environ["FAL_KEY"] = "test-key"
os.environ["RUNWARE_API_KEY"] = "test-key"
os.environ["REPLICATE_API_TOKEN"] = "test-key"
os.environ["RUNWAYML_API_SECRET"] = "test-key"
os.environ["RUNWAY_OUTPUT_HOSTS"] = "dnznrvs05pmza.cloudfront.net"
os.environ["VIDEO_CREDITS_PER_CLIP"] = "10"
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func
command.upgrade(Config("alembic.ini"), "head")
from app.main import app, video_provider_config_issue
from app.db import Session
from app.models import Asset, CreditAccount, UsageEvent, WorkflowJob, WorkflowRun, WorkflowRunStep
from app.providers.fal import Submission, JobStatus, VideoResult, ProviderError
from app import usage, video_worker
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Rừng đêm"}).json()["id"]
workflow = client.post("/api/workflows", json={"name":"Clip"}).json()["id"]
fal_tool = client.post("/api/ai-tools", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast"})
assert fal_tool.status_code == 201
fal_tool_id = fal_tool.json()["id"]
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-video")
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project}).json()
assert run["status"] == "running", run
class AmbiguousClient:
    def submit(self, request): raise TimeoutError("response lost")
assert video_worker.run_one(client=AmbiguousClient(), poll_seconds=0)
ambiguous = client.get(f"/api/workflow-runs/{run['id']}").json()
assert ambiguous["status"] == "needs_attention", ambiguous
assert ambiguous["steps"][1]["status"] == "needs_attention", ambiguous
assert "credit" in ambiguous["steps"][1]["detail"].lower(), ambiguous
with Session() as db: assert db.get(CreditAccount, workspace).balance == 0
assert client.post(f"/api/workflow-runs/{run['id']}/retry").status_code == 409
assert not video_worker.run_one(client=AmbiguousClient(), poll_seconds=0)
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-interrupted-video")
interrupted = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project}).json()
with Session.begin() as db:
    interrupted_job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == interrupted["id"]))
    interrupted_step = db.get(WorkflowRunStep, interrupted_job.step_id)
    interrupted_job.state = "leased"
    interrupted_job.lease_token = "old-worker-lease"
    interrupted_job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    interrupted_step.status = "submitting"
class MustNotResubmit:
    def submit(self, request): raise AssertionError("interrupted submit must not be repeated")
assert video_worker.run_one(client=MustNotResubmit(), poll_seconds=0)
assert client.get(f"/api/workflow-runs/{interrupted['id']}").json()["status"] == "needs_attention"
assert client.post(f"/api/workflow-runs/{interrupted['id']}/retry").status_code == 409
with Session() as db: assert db.get(CreditAccount, workspace).balance == 0
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-rejected-video")
rejected = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project}).json()
class RejectedClient:
    def submit(self, request): raise ProviderError("invalid_request", "Provider rejected input", http_status=400)
assert video_worker.run_one(client=RejectedClient(), poll_seconds=0)
assert client.get(f"/api/workflow-runs/{rejected['id']}").json()["status"] == "failed"
with Session() as db: assert db.get(CreditAccount, workspace).balance == 10
assert client.patch(f"/api/projects/{project}", json={"topic":"Đã sửa chủ đề"}).status_code == 200
assert client.put("/api/settings/workspace", json={"default_language":"vi","video_orientation":"horizontal","approval_required":True}).status_code == 200
assert client.put(f"/api/ai-tools/{fal_tool_id}", json={"task":"video","provider":"runware","model":"bytedance:seedance@2.5","is_enabled":True}).status_code == 200
retried = client.post(f"/api/workflow-runs/{rejected['id']}/retry")
assert retried.status_code == 201, retried.text
run = retried.json()
assert run["status"] == "running"
class FakeClient:
    submitted = 0
    def submit(self, request):
        self.submitted += 1
        assert request.prompt == "Rừng đêm"
        assert request.model_id == "fal-ai/veo3.1/fast"
        assert request.aspect_ratio == "9:16"
        return Submission(model_id=request.model_id, request_id="request1", status_url="https://queue.fal.run/fal-ai/veo3.1/fast/requests/request1/status", response_url="https://queue.fal.run/fal-ai/veo3.1/fast/requests/request1")
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url="https://fal.media/files/test.mp4", content_type="video/mp4")
fake = FakeClient()
def download(url, target):
    assert url == "https://fal.media/files/test.mp4"
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=fake, download=download, poll_seconds=0)
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "running"
assert video_worker.run_one(client=fake, download=download, poll_seconds=0)
updated = client.get(f"/api/workflow-runs/{run['id']}").json()
assert updated["status"] == "awaiting_review", updated
assert updated["steps"][1]["status"] == "completed"
asset_id = updated["steps"][1]["output"]["asset_id"]
assert client.get(f"/api/assets/{asset_id}").status_code == 200
assert next(a for a in client.get("/api/dashboard").json()["assets"] if a["id"] == asset_id)["project_id"] == project
assert fake.submitted == 1
assert not video_worker.run_one(client=fake, download=download, poll_seconds=0)
approved = client.post(f"/api/workflow-runs/{run['id']}/approve")
assert approved.status_code == 200, approved.text
assert approved.json()["status"] == "completed"
assert approved.json()["steps"][2]["status"] == "completed"
assert client.post(f"/api/workflow-runs/{run['id']}/approve").status_code == 409
assert client.put(f"/api/ai-tools/{fal_tool_id}", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast","is_enabled":False}).status_code == 200
runware_tool = client.post("/api/ai-tools", json={"task":"video","provider":"runware","model":"bytedance:seedance@2.5"})
assert runware_tool.status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-runware")
runware_run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project})
assert runware_run.status_code == 201, runware_run.text
runware_run = runware_run.json()
assert runware_run["status"] == "running"
class FakeRunware:
    def submit(self, request):
        assert request.model_id == "bytedance:seedance@2.5"
        from app.providers.runware import Submission
        return Submission(model_id=request.model_id, request_id="86d241d1-6d39-43c3-b0bc-aaaf94b8ba40")
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url="https://vm.runware.ai/video/test.mp4")
def download_runware(url, target):
    assert url == "https://vm.runware.ai/video/test.mp4"
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=FakeRunware(), download=download_runware, poll_seconds=0)
assert video_worker.run_one(client=FakeRunware(), download=download_runware, poll_seconds=0)
runware_result = client.get(f"/api/workflow-runs/{runware_run['id']}").json()
assert runware_result["status"] == "awaiting_review", runware_result
runware_asset = runware_result["steps"][1]["output"]["asset_id"]
assert client.get(f"/api/assets/{runware_asset}").status_code == 200
assert client.put(f"/api/ai-tools/{runware_tool.json()['id']}", json={"task":"video","provider":"runware","model":"bytedance:seedance@2.5","is_enabled":False}).status_code == 200
replicate_tool = client.post("/api/ai-tools", json={"task":"video","provider":"replicate","model":"google/veo-3.1-fast"})
assert replicate_tool.status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-replicate")
replicate_run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project})
assert replicate_run.status_code == 201, replicate_run.text
replicate_run = replicate_run.json()
assert replicate_run["status"] == "running", replicate_run
class FakeReplicate:
    def submit(self, request):
        assert request.model_id == "google/veo-3.1-fast"
        from app.providers.replicate import Submission
        return Submission(model_id=request.model_id, request_id="abcde12345",
            status_url="https://api.replicate.com/v1/predictions/abcde12345",
            response_url="https://api.replicate.com/v1/predictions/abcde12345")
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url="https://replicate.delivery/output.mp4")
def download_replicate(url, target):
    assert url == "https://replicate.delivery/output.mp4"
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=FakeReplicate(), download=download_replicate, poll_seconds=0)
assert video_worker.run_one(client=FakeReplicate(), download=download_replicate, poll_seconds=0)
replicate_result = client.get(f"/api/workflow-runs/{replicate_run['id']}").json()
assert replicate_result["status"] == "awaiting_review", replicate_result
replicate_asset = replicate_result["steps"][1]["output"]["asset_id"]
assert client.put(f"/api/ai-tools/{replicate_tool.json()['id']}", json={"task":"video","provider":"replicate","model":"google/veo-3.1-fast","is_enabled":False}).status_code == 200
os.environ.pop("RUNWAY_OUTPUT_HOSTS")
assert video_provider_config_issue("runway")[0] == "invalid_config"
os.environ["RUNWAY_OUTPUT_HOSTS"] = "dnznrvs05pmza.cloudfront.net"
runway_tool = client.post("/api/ai-tools", json={"task":"video","provider":"runway","model":"gen4.5"})
assert runway_tool.status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-runway")
runway_run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project, "tool_id":runway_tool.json()["id"]})
assert runway_run.status_code == 201, runway_run.text
runway_run = runway_run.json()
assert runway_run["status"] == "running", runway_run
class FakeRunway:
    def submit(self, request):
        assert request.model_id == "gen4.5"
        assert request.duration == "8s" and request.resolution == "720p"
        assert request.generate_audio is False
        from app.providers.runway import Submission
        task_id = "d2e3d1f4-1b3c-4b5c-8d46-1c1d7ee86892"
        task_url = f"https://api.dev.runwayml.com/v1/tasks/{task_id}"
        return Submission(model_id=request.model_id, request_id=task_id,
                          status_url=task_url, response_url=task_url)
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url="https://dnznrvs05pmza.cloudfront.net/output.mp4?_jwt=signed")
def download_runway(url, target):
    assert url == "https://dnznrvs05pmza.cloudfront.net/output.mp4?_jwt=signed"
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=FakeRunway(), download=download_runway, poll_seconds=0)
assert video_worker.run_one(client=FakeRunway(), download=download_runway, poll_seconds=0)
runway_result = client.get(f"/api/workflow-runs/{runway_run['id']}").json()
assert runway_result["status"] == "awaiting_review", runway_result
runway_asset = runway_result["steps"][1]["output"]["asset_id"]
with Session() as db:
    asset = db.get(Asset, asset_id)
    assert (asset.workspace_id, asset.project_id, asset.run_id, asset.provider) == (workspace, project, run["id"], "fal")
    assert db.get(Asset, runware_asset).provider == "runware"
    assert db.get(Asset, replicate_asset).provider == "replicate"
    assert db.get(Asset, runway_asset).provider == "runway"
    assert db.scalar(select(func.count()).select_from(UsageEvent)) == 4
    assert sorted(db.scalars(select(UsageEvent.tool))) == ["fal/video", "replicate/video", "runware/video", "runway/video"]
    assert db.get(CreditAccount, workspace).balance == 0
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-expiring-video")
stalled = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":runway_tool.json()["id"]})
assert stalled.status_code == 201 and stalled.json()["status"] == "running", stalled.text
assert video_worker.run_one(client=FakeRunway(), poll_seconds=0)
with Session.begin() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == stalled.json()["id"]))
    job.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
os.environ["VIDEO_JOB_MAX_AGE_SECONDS"] = "60"
class MustNotPoll:
    def status(self, submission): raise AssertionError("expired job must not poll provider")
assert video_worker.run_one(client=MustNotPoll(), poll_seconds=0)
assert client.get(f"/api/workflow-runs/{stalled.json()['id']}").json()["status"] == "needs_attention"
assert client.post(f"/api/workflow-runs/{stalled.json()['id']}/retry").status_code == 409
with Session() as db:
    assert db.get(CreditAccount, workspace).balance == 0
    assert db.scalar(select(func.count()).select_from(UsageEvent)) == 4
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-invalid-video")
invalid = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project,"tool_id":runway_tool.json()["id"]})
assert invalid.status_code == 201 and invalid.json()["status"] == "running", invalid.text
assert video_worker.run_one(client=FakeRunway(), poll_seconds=0)
def truncated_download(url, target):
    target.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return target.stat().st_size
assert video_worker.run_one(client=FakeRunway(), download=truncated_download, poll_seconds=0)
assert client.get(f"/api/workflow-runs/{invalid.json()['id']}").json()["status"] == "needs_attention"
assert client.post(f"/api/workflow-runs/{invalid.json()['id']}/retry").status_code == 409
with Session() as db:
    assert db.get(CreditAccount, workspace).balance == 0
    assert db.scalar(select(func.count()).select_from(UsageEvent)) == 4
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
