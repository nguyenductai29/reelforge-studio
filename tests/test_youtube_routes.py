"""A workspace owner can connect YouTube without exposing OAuth tokens."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class YouTubeRoutesTest(unittest.TestCase):
    def test_connect_callback_and_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            program = r'''
import json
import os
from cryptography.fernet import Fernet
os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test-client.apps.googleusercontent.com"
os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-secret"
os.environ["GOOGLE_OAUTH_REDIRECT_URI"] = "http://localhost:3000/youtube/callback"
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
import httpx
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from datetime import datetime, timezone
from app.models import Asset, Project, Subscription, Workflow, WorkflowJob, WorkflowRun, WorkflowRunStep, Workspace
from app.publications import Publication
from app.publishers.google_oauth import YouTubeConnection, connection_generation
client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
assert client.get("/api/youtube/connection").json()["connected"] is False
start = client.get("/api/youtube/authorization")
assert start.status_code == 200, start.text
state = parse_qs(urlsplit(start.json()["url"]).query)["state"][0]
def token_exchange(request):
    assert request.url.host == "oauth2.googleapis.com"
    return httpx.Response(200, json={"access_token":"secret-access", "refresh_token":"secret-refresh", "expires_in":3600, "token_type":"Bearer", "scope":"https://www.googleapis.com/auth/youtube.upload"})
real_client = httpx.Client
with patch("app.main.google_http_client", side_effect=lambda: real_client(transport=httpx.MockTransport(token_exchange))):
    callback = client.post("/api/youtube/callback", json={"state":state,"code":"code"})
assert callback.status_code == 200, callback.text
connected = client.get("/api/youtube/connection")
assert connected.status_code == 200 and connected.json()["connected"] is True
assert "secret-access" not in connected.text and "secret-refresh" not in connected.text
now = datetime.now(timezone.utc)
with Session.begin() as db:
    owner_id = db.get(Workspace, workspace).owner_id
    db.add(Project(id="project-1", workspace_id=workspace, title="Movie"))
    db.add(Workflow(id="workflow-1", workspace_id=workspace, name="Flow"))
    db.flush()
    db.add(WorkflowRun(id="run-1", workspace_id=workspace, project_id="project-1",
        workflow_id="workflow-1", graph_snapshot="{}", status="completed"))
    db.flush()
    db.add(WorkflowRunStep(id="video-step", run_id="run-1", node_id="video", node_type="video",
        position=0, status="completed", detail=""))
    db.add(WorkflowRunStep(id="review-step", run_id="run-1", node_id="review", node_type="review",
        position=1, status="completed", detail="", output=json.dumps({"approved_by": owner_id})))
    db.flush()
    db.add(Asset(id="asset-1", workspace_id=workspace, project_id="project-1",
        run_id="run-1", step_id="video-step", filename="movie.mp4",
        content_type="video/mp4", bytes=12))
    db.add(WorkflowJob(id="job-1", workspace_id=workspace, run_id="run-1", step_id="review-step",
        logical_key="publish:youtube:run-1", payload_json=json.dumps({
            "publication_id":"publication-1", "channel":"youtube", "asset_id":"asset-1",
            "connection_generation":connection_generation(db.get(YouTubeConnection, workspace))}),
        state="failed", available_at=now, attempt_count=1, created_at=now, updated_at=now))
    db.add(Publication(id="publication-1", workspace_id=workspace, run_id="run-1",
        asset_id="asset-1", job_id="job-1", channel="youtube", title="Clip", description="",
        state="needs_attention", last_error="connection_changed", created_at=now, updated_at=now))
retryable = client.get("/api/youtube/publications/publication-1")
assert retryable.status_code == 200 and retryable.json()["can_retry"] is True, retryable.text
retry = client.post("/api/youtube/publications/publication-1/retry")
assert retry.status_code == 202 and retry.json()["state"] == "queued", retry.text
assert retry.json()["can_retry"] is False
assert client.post("/api/youtube/publications/publication-1/retry").status_code == 202
with Session.begin() as db:
    db.get(Subscription, workspace).status = "suspended"
blocked = client.post("/api/youtube/publications", json={"run_id":"run", "asset_id":"asset", "title":"Clip"})
assert blocked.status_code == 403 and "inactive" in blocked.text, blocked.text
blocked_retry = client.post("/api/youtube/publications/publication-1/retry")
assert blocked_retry.status_code == 403 and "inactive" in blocked_retry.text, blocked_retry.text
assert client.delete("/api/youtube/connection").status_code == 204
assert client.get("/api/youtube/connection").json()["connected"] is False
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
