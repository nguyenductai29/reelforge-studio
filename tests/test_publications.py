"""Approved-video publication records, idempotent queueing, and lease fencing."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

from app.jobs import claim_due_jobs
from app.models import Asset, Base, Project, User, Workflow, WorkflowJob, WorkflowRun, WorkflowRunStep, Workspace
from app.publications import (
    Publication,
    fail_publication,
    finish_publication,
    load_upload_session,
    mark_uploading,
    queue_publication,
    retry_publication,
    save_upload_session,
)
from app.publishers.google_oauth import YouTubeConnection
from app.publishers.youtube import UPLOAD_SCOPE


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'publications.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.key = Fernet.generate_key().decode("ascii")
        self.connected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.Session.begin() as db:
            db.add_all([
                User(id="user-1", email="one@example.com", password_hash="hash"),
                User(id="user-2", email="two@example.com", password_hash="hash"),
            ])
            db.flush()
            db.add_all([
                Workspace(id="space-1", name="One", owner_id="user-1"),
                Workspace(id="space-2", name="Two", owner_id="user-2"),
            ])
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Movie"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.flush()
            db.add(WorkflowRun(id="run-1", workspace_id="space-1", workflow_id="workflow-1",
                               project_id="project-1", graph_snapshot="{}", status="completed"))
            db.flush()
            db.add_all([
                WorkflowRunStep(id="video-step", run_id="run-1", node_id="video", node_type="video",
                                position=0, status="completed", detail=""),
                WorkflowRunStep(id="review-step", run_id="run-1", node_id="review", node_type="review",
                                position=1, status="completed", detail="",
                                output=json.dumps({"approved_by": "user-1", "approved_at": "2026-01-01T00:00:00Z"})),
            ])
            db.flush()
            db.add(Asset(id="asset-1", workspace_id="space-1", project_id="project-1", run_id="run-1",
                         step_id="video-step", filename="movie.mp4", content_type="video/mp4", bytes=100))
            cipher = Fernet(self.key.encode())
            db.add(YouTubeConnection(workspace_id="space-1",
                access_token_ciphertext=cipher.encrypt(b"access").decode(),
                refresh_token_ciphertext=cipher.encrypt(b"refresh").decode(),
                expires_at=self.connected_at + timedelta(hours=1), scope=UPLOAD_SCOPE,
                connected_at=self.connected_at, updated_at=self.connected_at))

    def queue(self):
        with self.Session.begin() as db:
            publication = queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                            channel="youtube", title="My movie", description="AI-generated")
            return publication.id, publication.job_id

    def claim(self, *, now):
        with self.Session.begin() as db:
            return claim_due_jobs(db, worker_id="publisher", now=now)[0].lease_token

    def test_idempotent_queue_attaches_frozen_job_to_approved_review(self):
        publication_id, job_id = self.queue()
        with self.Session.begin() as db:
            repeated = queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                         channel="youtube", title="My movie", description="AI-generated")
            self.assertEqual(repeated.id, publication_id)
            with self.assertRaisesRegex(ValueError, "different input"):
                queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                  channel="youtube", title="Changed", description="AI-generated")
        with self.Session() as db:
            publication = db.get(Publication, publication_id)
            job = db.get(WorkflowJob, job_id)
            self.assertEqual(publication.state, "queued")
            self.assertEqual(job.step_id, "review-step")
            self.assertEqual(job.logical_key, "publish:youtube:run-1")
            self.assertEqual(job.payload, {"publication_id": publication_id, "channel": "youtube", "asset_id": "asset-1",
                                           "connection_generation": "2026-01-01T00:00:00.000000+00:00"})
            self.assertEqual(len(db.scalars(select(Publication)).all()), 1)
            self.assertEqual(len(db.scalars(select(WorkflowJob)).all()), 1)

    def test_rejects_cross_workspace_unapproved_or_unlinked_asset(self):
        with self.Session.begin() as db:
            with self.assertRaisesRegex(ValueError, "workspace"):
                queue_publication(db, workspace_id="space-2", run_id="run-1", asset_id="asset-1",
                                  channel="youtube", title="Movie", description="")
            db.get(WorkflowRunStep, "review-step").status = "awaiting_review"
        with self.Session.begin() as db:
            with self.assertRaisesRegex(ValueError, "approved"):
                queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                  channel="youtube", title="Movie", description="")
            db.get(WorkflowRunStep, "review-step").status = "completed"
            db.get(Asset, "asset-1").run_id = None
        with self.Session.begin() as db:
            with self.assertRaisesRegex(ValueError, "asset"):
                queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                  channel="youtube", title="Movie", description="")
        with self.Session() as db:
            self.assertEqual(db.scalars(select(Publication)).all(), [])

    def test_approved_review_can_publish_even_if_later_graph_step_is_blocked(self):
        with self.Session.begin() as db:
            db.get(WorkflowRun, "run-1").status = "blocked"
        publication_id, _ = self.queue()
        with self.Session() as db:
            self.assertEqual(db.get(Publication, publication_id).state, "queued")

    def test_encrypted_upload_session_survives_retry_and_stale_lease_cannot_finish(self):
        publication_id, job_id = self.queue()
        start = datetime.now(timezone.utc) + timedelta(seconds=1)
        token1 = self.claim(now=start)
        session = {"url": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=secret",
                   "file_size": 100}
        with self.Session.begin() as db:
            self.assertTrue(mark_uploading(db, publication_id=publication_id, job_id=job_id,
                                           lease_token=token1, now=start))
            self.assertTrue(save_upload_session(db, publication_id=publication_id, job_id=job_id,
                                                lease_token=token1, session=session, encryption_key=self.key, now=start))
            self.assertTrue(fail_publication(db, publication_id=publication_id, job_id=job_id,
                                             lease_token=token1, error="temporary", retry_delay_seconds=0,
                                             now=start + timedelta(seconds=1)))
        with self.Session.begin() as db:
            token2 = claim_due_jobs(db, worker_id="publisher-2", now=start + timedelta(seconds=2))[0].lease_token
        with self.Session.begin() as db:
            publication = db.get(Publication, publication_id)
            self.assertNotIn("upload_id=secret", publication.upload_session_ciphertext)
            self.assertEqual(load_upload_session(publication, encryption_key=self.key), session)
            with self.assertRaisesRegex(ValueError, "cannot be decrypted"):
                load_upload_session(publication, encryption_key=Fernet.generate_key().decode("ascii"))
            self.assertFalse(finish_publication(db, publication_id=publication_id, job_id=job_id,
                                                lease_token=token1, remote_id="old-video", now=start + timedelta(seconds=2)))
            self.assertTrue(finish_publication(db, publication_id=publication_id, job_id=job_id,
                                               lease_token=token2, remote_id="new-video", now=start + timedelta(seconds=2)))
            self.assertTrue(finish_publication(db, publication_id=publication_id, job_id=job_id,
                                               lease_token=token2, remote_id="new-video", now=start + timedelta(seconds=2)))
            self.assertFalse(fail_publication(db, publication_id=publication_id, job_id=job_id,
                                              lease_token=token1, error="too late", now=start + timedelta(seconds=2)))
        with self.Session() as db:
            publication = db.get(Publication, publication_id)
            self.assertEqual((publication.state, publication.remote_id, publication.last_error),
                             ("succeeded", "new-video", None))
            self.assertEqual(db.get(WorkflowJob, job_id).state, "succeeded")

    def test_ambiguous_upload_requires_attention_without_requeue(self):
        publication_id, job_id = self.queue()
        start = datetime.now(timezone.utc) + timedelta(seconds=1)
        token = self.claim(now=start)
        with self.Session.begin() as db:
            self.assertTrue(fail_publication(db, publication_id=publication_id, job_id=job_id,
                                             lease_token=token, error="upload response lost", needs_attention=True,
                                             now=start))
        with self.Session() as db:
            publication = db.get(Publication, publication_id)
            self.assertEqual(publication.state, "needs_attention")
            self.assertEqual(db.get(WorkflowJob, job_id).state, "failed")

    def test_manual_retry_rebinds_only_sessionless_publication_to_current_connection(self):
        publication_id, old_job_id = self.queue()
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        token = self.claim(now=now)
        with self.Session.begin() as db:
            self.assertTrue(fail_publication(db, publication_id=publication_id, job_id=old_job_id,
                lease_token=token, error="connection_changed", needs_attention=True, now=now))
            db.get(YouTubeConnection, "space-1").connected_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        with self.Session.begin() as db:
            publication = retry_publication(db, workspace_id="space-1", publication_id=publication_id)
            new_job_id = publication.job_id
            self.assertNotEqual(new_job_id, old_job_id)
            self.assertEqual(publication.state, "queued")
        with self.Session.begin() as db:
            repeated = retry_publication(db, workspace_id="space-1", publication_id=publication_id)
            self.assertEqual(repeated.job_id, new_job_id)
            same_create = queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                            channel="youtube", title="My movie", description="AI-generated")
            self.assertEqual(same_create.job_id, new_job_id)
            with self.assertRaisesRegex(ValueError, "different input"):
                queue_publication(db, workspace_id="space-1", run_id="run-1", asset_id="asset-1",
                                  channel="youtube", title="Different", description="AI-generated")
        with self.Session() as db:
            job = db.get(WorkflowJob, new_job_id)
            self.assertEqual(job.state, "queued")
            self.assertEqual(job.payload["connection_generation"], "2026-01-02T00:00:00.000000+00:00")
            self.assertEqual(db.get(WorkflowJob, old_job_id).state, "failed")

    def test_manual_retry_rejects_ambiguous_upload_without_saved_session(self):
        publication_id, job_id = self.queue()
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        token = self.claim(now=now)
        with self.Session.begin() as db:
            self.assertTrue(fail_publication(db, publication_id=publication_id, job_id=job_id,
                lease_token=token, error="upload:invalid_response", needs_attention=True, now=now))
        with self.Session.begin() as db:
            with self.assertRaisesRegex(ValueError, "uncertain"):
                retry_publication(db, workspace_id="space-1", publication_id=publication_id)


class PublicationMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade_schema(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(root / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(root / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({
                "database_url": f"sqlite:///{target / 'instance' / 'migration.db'}"
            }))
            program = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.db import engine
from app.models import Base
config = Config('alembic.ini')
command.upgrade(config, '0009_youtube_connections')
assert not inspect(engine).has_table('publications')
command.upgrade(config, 'head')
columns = {column['name'] for column in inspect(engine).get_columns('publications')}
assert {'id','workspace_id','run_id','asset_id','job_id','channel','state','upload_session_ciphertext','remote_id','last_error','created_at','updated_at','finished_at'} <= columns, columns
assert 'publications' in Base.metadata.tables
command.downgrade(config, '0009_youtube_connections')
assert not inspect(engine).has_table('publications')
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
