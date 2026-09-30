"""An approved private clip is queued and uploaded to YouTube once."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class YouTubePublicationFlowTest(unittest.TestCase):
    def test_approved_video_uploads_once_with_resumable_session(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
import os
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
os.environ["FAL_KEY"] = "test-key"
os.environ["VIDEO_CREDITS_PER_CLIP"] = "10"
os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test-client.apps.googleusercontent.com"
os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-secret"
os.environ["GOOGLE_OAUTH_REDIRECT_URI"] = "http://localhost:3000/youtube/callback"
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app.models import CreditAccount, WorkflowJob
from app.providers.fal import Submission, JobStatus, VideoResult
from app.publishers.google_oauth import YouTubeConnection
from app.publishers.youtube import UPLOAD_SCOPE
from app import usage, video_worker, youtube_worker
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Rừng đêm"}).json()["id"]
workflow = client.post("/api/workflows", json={"name":"Clip"}).json()["id"]
graph = {"nodes": [{"id":"idea","type":"idea","x":0,"y":0},
                   {"id":"video","type":"video","x":100,"y":0},
                   {"id":"review","type":"review","x":200,"y":0},
                   {"id":"publish","type":"publish","x":300,"y":0}],
         "edges": [{"source":"idea","target":"video"},
                   {"source":"video","target":"review"},
                   {"source":"review","target":"publish"}]}
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
assert client.post("/api/ai-tools", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast"}).status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-video")
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project}).json()
class FakeFal:
    def submit(self, request): return Submission(model_id=request.model_id, request_id="request1", status_url="https://queue.fal.run/fal-ai/veo3.1/fast/requests/request1/status", response_url="https://queue.fal.run/fal-ai/veo3.1/fast/requests/request1")
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url="https://fal.media/files/test.mp4")
def download(url, target):
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=FakeFal(), download=download, poll_seconds=0)
assert video_worker.run_one(client=FakeFal(), download=download, poll_seconds=0)
video_run = client.get(f"/api/workflow-runs/{run['id']}").json()
asset_id = video_run["steps"][1]["output"]["asset_id"]
approved = client.post(f"/api/workflow-runs/{run['id']}/approve")
# The Publish step hands over to the Publishing page after approval, so the run completes.
assert approved.status_code == 200 and approved.json()["status"] == "completed", approved.text
now = datetime.now(timezone.utc)
cipher = Fernet(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"].encode())
with Session.begin() as db:
    db.add(YouTubeConnection(workspace_id=workspace, access_token_ciphertext=cipher.encrypt(b"oauth-access").decode(), refresh_token_ciphertext=cipher.encrypt(b"oauth-refresh").decode(), expires_at=now+timedelta(hours=1), scope=UPLOAD_SCOPE, connected_at=now, updated_at=now))
body = {"run_id":run["id"],"asset_id":asset_id,"title":"Rừng đêm","description":"Video thử"}
created = client.post("/api/youtube/publications", json=body)
assert created.status_code == 201, created.text
publication = created.json()
assert publication["state"] == "queued"
assert client.post(f"/api/workflow-runs/{run['id']}/retry").status_code == 409
again = client.post("/api/youtube/publications", json=body)
assert again.status_code == 200 and again.json()["id"] == publication["id"]
assert client.post("/api/youtube/publications", json={**body,"title":"Tên khác"}).status_code == 409
seen = []
def google_upload(request):
    seen.append(request)
    if request.method == "POST":
        return httpx.Response(200, headers={"Location":"https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=session1"})
    if request.headers["Content-Range"].startswith("bytes */"):
        return httpx.Response(308)
    return httpx.Response(200, json={"id":"abcdefghijk", "status":{"privacyStatus":"private","uploadStatus":"uploaded"}})
with httpx.Client(transport=httpx.MockTransport(google_upload)) as fake_google:
    assert youtube_worker.run_one(client=fake_google, poll_seconds=0)
    assert client.get(f"/api/youtube/publications/{publication['id']}").json()["state"] == "uploading"
    assert youtube_worker.run_one(client=fake_google, poll_seconds=0)
    assert not youtube_worker.run_one(client=fake_google, poll_seconds=0)
result = client.get(f"/api/youtube/publications/{publication['id']}")
assert result.status_code == 200 and result.json()["state"] == "succeeded"
assert result.json()["remote_id"] == "abcdefghijk"
assert len([request for request in seen if request.method == "POST"]) == 1
assert client.post("/api/register", json={"email":"b@example.com","password":"long-password-123","workspace_name":"B"}).status_code == 201
assert client.get(f"/api/youtube/publications/{publication['id']}").status_code == 404
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-5000:])
