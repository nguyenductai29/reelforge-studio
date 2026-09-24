"""Transient Google OAuth failures must not strand an approved publication."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import youtube_worker
from app.models import Asset, Base, Project, User, Workflow, WorkflowJob, WorkflowRun, WorkflowRunStep, Workspace
from app.publications import Publication
from app.publishers.google_oauth import YouTubeConnection
from app.publishers.youtube import UPLOAD_SCOPE


class YouTubeWorkerRetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'worker.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.key = Fernet.generate_key().decode()
        now = datetime.now(timezone.utc)
        cipher = Fernet(self.key.encode())
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="owner@example.com", password_hash="hash"))
            db.add(Workspace(id="workspace-1", name="Studio", owner_id="user-1"))
            db.add(Project(id="project-1", workspace_id="workspace-1", title="Movie"))
            db.add(Workflow(id="workflow-1", workspace_id="workspace-1", name="Flow"))
            db.add(WorkflowRun(id="run-1", workspace_id="workspace-1", project_id="project-1",
                               workflow_id="workflow-1", graph_snapshot="{}", status="blocked"))
            db.add(WorkflowRunStep(id="review-1", run_id="run-1", node_id="review",
                                   node_type="review", position=1, status="completed", detail=""))
            db.add(Asset(id="asset-1", workspace_id="workspace-1", filename="movie.mp4",
                         content_type="video/mp4", bytes=12))
            db.add(WorkflowJob(id="job-1", workspace_id="workspace-1", run_id="run-1",
                step_id="review-1", logical_key="publish:youtube:run-1",
                payload_json=json.dumps({"publication_id": "publication-1", "channel": "youtube", "asset_id": "asset-1",
                                         "connection_generation": now.isoformat(timespec="microseconds")}),
                state="queued", available_at=now, attempt_count=0, created_at=now, updated_at=now))
            db.add(Publication(id="publication-1", workspace_id="workspace-1", run_id="run-1",
                asset_id="asset-1", job_id="job-1", channel="youtube", title="Movie", description="",
                state="queued", created_at=now, updated_at=now))
            db.add(YouTubeConnection(workspace_id="workspace-1",
                access_token_ciphertext=cipher.encrypt(b"old-access").decode(),
                refresh_token_ciphertext=cipher.encrypt(b"refresh-secret").decode(),
                expires_at=now - timedelta(seconds=1), scope=UPLOAD_SCOPE,
                connected_at=now, updated_at=now))
        media = self.root / "workspace-1"
        media.mkdir()
        (media / "asset-1").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    def test_google_refresh_503_requeues_without_upload_or_manual_attention(self):
        requests = []

        def google(request):
            requests.append(request)
            return httpx.Response(503, json={"error": "temporarily_unavailable"})

        environment = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
            "REELFORGE_TOKEN_ENCRYPTION_KEY": self.key,
        }
        with patch.dict(os.environ, environment), patch.object(youtube_worker, "Session", self.Session), \
             patch.object(youtube_worker, "media_root", return_value=self.root), \
             httpx.Client(transport=httpx.MockTransport(google)) as client:
            self.assertTrue(youtube_worker.run_one(client=client, poll_seconds=0))

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "oauth2.googleapis.com")
        with self.Session() as db:
            publication = db.get(Publication, "publication-1")
            job = db.get(WorkflowJob, "job-1")
            self.assertEqual(publication.state, "queued")
            self.assertEqual(job.state, "queued")
            self.assertEqual(publication.last_error, "oauth_or_session:google_token_error")
            self.assertIsNone(publication.upload_session_ciphertext)

    def test_transient_refresh_uses_exponential_backoff(self):
        with self.Session.begin() as db:
            db.get(WorkflowJob, "job-1").attempt_count = 1
        environment = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
            "REELFORGE_TOKEN_ENCRYPTION_KEY": self.key,
        }
        started = datetime.now(timezone.utc)
        with patch.dict(os.environ, environment), patch.object(youtube_worker, "Session", self.Session), \
             patch.object(youtube_worker, "media_root", return_value=self.root), \
             httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            self.assertTrue(youtube_worker.run_one(client=client, poll_seconds=0))
        with self.Session() as db:
            job = db.get(WorkflowJob, "job-1")
            self.assertEqual(job.state, "queued")
            self.assertGreaterEqual(job.available_at.replace(tzinfo=timezone.utc), started + timedelta(seconds=10))

    def test_transient_refresh_stops_after_retry_limit_before_any_upload_session(self):
        with self.Session.begin() as db:
            db.get(WorkflowJob, "job-1").attempt_count = 5
        environment = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
            "REELFORGE_TOKEN_ENCRYPTION_KEY": self.key,
        }
        with patch.dict(os.environ, environment), patch.object(youtube_worker, "Session", self.Session), \
             patch.object(youtube_worker, "media_root", return_value=self.root), \
             httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            self.assertTrue(youtube_worker.run_one(client=client, poll_seconds=0))
        with self.Session() as db:
            job = db.get(WorkflowJob, "job-1")
            publication = db.get(Publication, "publication-1")
            self.assertEqual((job.state, job.attempt_count), ("failed", 6))
            self.assertEqual(publication.state, "failed")
            self.assertIsNone(publication.upload_session_ciphertext)

    def test_session_start_503_requeues_before_any_media_transfer(self):
        with self.Session.begin() as db:
            db.get(YouTubeConnection, "workspace-1").expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        requests = []

        def google(request):
            requests.append(request)
            return httpx.Response(503)

        environment = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
            "REELFORGE_TOKEN_ENCRYPTION_KEY": self.key,
        }
        with patch.dict(os.environ, environment), patch.object(youtube_worker, "Session", self.Session), \
             patch.object(youtube_worker, "media_root", return_value=self.root), \
             httpx.Client(transport=httpx.MockTransport(google)) as client:
            self.assertTrue(youtube_worker.run_one(client=client, poll_seconds=0))

        self.assertEqual([request.method for request in requests], ["POST"])
        with self.Session() as db:
            publication = db.get(Publication, "publication-1")
            job = db.get(WorkflowJob, "job-1")
            self.assertEqual(publication.state, "queued")
            self.assertEqual(job.state, "queued")
            self.assertEqual(publication.last_error, "start:youtube_unavailable")
            self.assertIsNone(publication.upload_session_ciphertext)

    def test_unexpected_public_visibility_requires_operator_attention(self):
        with self.Session.begin() as db:
            db.get(YouTubeConnection, "workspace-1").expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
            publication = db.get(Publication, "publication-1")
            session_data = {"url": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=abc123",
                            "file_size": 12}
            publication.upload_session_ciphertext = Fernet(self.key.encode()).encrypt(
                json.dumps(session_data).encode()).decode()
            publication.state = "uploading"
        requests = []

        def google(request):
            requests.append(request)
            return httpx.Response(201, json={"id": "abcdefghijk", "status": {"privacyStatus": "public", "uploadStatus": "uploaded"}})

        environment = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
            "REELFORGE_TOKEN_ENCRYPTION_KEY": self.key,
        }
        with patch.dict(os.environ, environment), patch.object(youtube_worker, "Session", self.Session), \
             patch.object(youtube_worker, "media_root", return_value=self.root), \
             httpx.Client(transport=httpx.MockTransport(google)) as client:
            self.assertTrue(youtube_worker.run_one(client=client, poll_seconds=0))

        self.assertEqual([request.headers["Content-Range"] for request in requests], ["bytes */12"])
        with self.Session() as db:
            publication = db.get(Publication, "publication-1")
            self.assertEqual(publication.state, "needs_attention")
            self.assertEqual(publication.last_error, "upload:unexpected_visibility")
            self.assertEqual(publication.remote_id, "abcdefghijk")
            self.assertEqual(db.get(WorkflowJob, "job-1").state, "failed")

    def test_queued_channel_a_cannot_upload_with_reconnected_channel_b_token(self):
        cipher = Fernet(self.key.encode())
        with self.Session.begin() as db:
            connection = db.get(YouTubeConnection, "workspace-1")
            connection.connected_at = connection.connected_at + timedelta(seconds=1)
            connection.access_token_ciphertext = cipher.encrypt(b"channel-b-access").decode()
            connection.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        requests = []

        def google(request):
            requests.append(request)
            return httpx.Response(200, headers={
                "Location": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=wrong-channel"
            })

        environment = {
            "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
            "REELFORGE_TOKEN_ENCRYPTION_KEY": self.key,
        }
        with patch.dict(os.environ, environment), patch.object(youtube_worker, "Session", self.Session), \
             patch.object(youtube_worker, "media_root", return_value=self.root), \
             httpx.Client(transport=httpx.MockTransport(google)) as client:
            self.assertTrue(youtube_worker.run_one(client=client, poll_seconds=0))

        self.assertEqual(requests, [])
        with self.Session() as db:
            publication = db.get(Publication, "publication-1")
            self.assertEqual(publication.state, "needs_attention")
            self.assertEqual(publication.last_error, "connection_changed")
            self.assertEqual(db.get(WorkflowJob, "job-1").state, "failed")


if __name__ == "__main__":
    unittest.main()
