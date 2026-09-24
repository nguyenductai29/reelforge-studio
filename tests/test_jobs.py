"""Durable workflow job queue behavior with an isolated SQLite database."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.jobs import claim_due_jobs, complete_job, enqueue_job, fail_job
from app.models import Base, Project, User, Workflow, WorkflowJob, WorkflowRun, WorkflowRunStep, Workspace


class WorkflowJobQueueTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        database = Path(self.directory.name) / "jobs.db"
        self.engine = create_engine(f"sqlite:///{database}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="jobs@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Job Test", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Movie"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.flush()
            db.add(WorkflowRun(id="run-1", workspace_id="space-1", workflow_id="workflow-1",
                               project_id="project-1", graph_snapshot="{}", status="running"))
            db.flush()
            db.add(WorkflowRunStep(id="step-1", run_id="run-1", node_id="video", node_type="video",
                                   position=0, status="pending", detail=""))

    def test_enqueue_is_idempotent_and_snapshots_json_payload(self):
        payload = {"prompt": "A", "options": {"seconds": 5}}
        with self.Session.begin() as db:
            first = enqueue_job(db, workspace_id="space-1", run_id="run-1", step_id="step-1",
                                logical_key="run-1:video", payload=payload)
            first_id = first.id
            payload["options"]["seconds"] = 9
            self.assertEqual(first.payload, {"prompt": "A", "options": {"seconds": 5}})
            first.payload["options"]["seconds"] = 12
            self.assertEqual(first.payload["options"]["seconds"], 5)
            repeated = enqueue_job(db, workspace_id="space-1", run_id="run-1", step_id="step-1",
                                   logical_key="run-1:video", payload={"options": {"seconds": 5}, "prompt": "A"})
            self.assertEqual(repeated.id, first_id)
        with self.Session() as db:
            jobs = db.scalars(select(WorkflowJob)).all()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].payload, {"prompt": "A", "options": {"seconds": 5}})
            self.assertEqual(jobs[0].state, "queued")
            self.assertEqual(jobs[0].attempt_count, 0)
            self.assertLessEqual(jobs[0].available_at.replace(tzinfo=timezone.utc), datetime.now(timezone.utc))

    def test_logical_key_cannot_be_reused_for_different_input(self):
        self.enqueue()
        with self.Session.begin() as db:
            with self.assertRaisesRegex(ValueError, "different job input"):
                enqueue_job(db, workspace_id="space-1", run_id="run-1", step_id="step-1",
                            logical_key="run-1:video", payload={"prompt": "changed"})
            self.assertEqual(db.scalar(select(WorkflowJob.id).where(WorkflowJob.logical_key == "run-1:video")),
                             db.scalar(select(WorkflowJob.id)))

    def test_enqueue_rejects_step_from_another_workspace(self):
        with self.Session.begin() as db:
            db.add(User(id="user-2", email="other@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-2", name="Other", owner_id="user-2"))
        with self.Session.begin() as db:
            with self.assertRaisesRegex(ValueError, "step does not belong"):
                enqueue_job(db, workspace_id="space-2", run_id="run-1", step_id="step-1",
                            logical_key="wrong-space", payload={"prompt": "A"})
        with self.Session() as db:
            self.assertEqual(db.scalars(select(WorkflowJob)).all(), [])

    def enqueue(self, *, logical_key="run-1:video", available_at=None):
        with self.Session.begin() as db:
            job = enqueue_job(db, workspace_id="space-1", run_id="run-1", step_id="step-1",
                              logical_key=logical_key, payload={"prompt": "A"}, available_at=available_at)
            return job.id

    def test_due_job_is_reclaimed_after_lease_expires(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        job_id = self.enqueue(available_at=start)
        with self.Session.begin() as db:
            self.assertEqual(claim_due_jobs(db, worker_id="worker-a", now=start - timedelta(seconds=1)), [])
            first = claim_due_jobs(db, worker_id="worker-a", lease_seconds=30, now=start)
            self.assertEqual([job.id for job in first], [job_id])
            first_token = first[0].lease_token
            self.assertEqual(first[0].attempt_count, 1)
            self.assertEqual(first[0].state, "leased")
        with self.Session.begin() as db:
            self.assertEqual(claim_due_jobs(db, worker_id="worker-b", now=start + timedelta(seconds=29)), [])
        with self.Session.begin() as db:
            reclaimed = claim_due_jobs(db, worker_id="worker-b", lease_seconds=30,
                                       now=start + timedelta(seconds=30))
            self.assertEqual(len(reclaimed), 1)
            self.assertEqual(reclaimed[0].id, job_id)
            self.assertEqual(reclaimed[0].attempt_count, 2)
            self.assertEqual(reclaimed[0].worker_id, "worker-b")
            self.assertNotEqual(reclaimed[0].lease_token, first_token)

    def test_worker_claims_only_its_job_kind(self):
        video_id = self.enqueue(logical_key="video:run-1:step-1")
        publish_id = self.enqueue(logical_key="publish:run-1:youtube")
        with self.Session.begin() as db:
            video = claim_due_jobs(db, worker_id="video-worker", logical_key_prefix="video:")
            self.assertEqual([job.id for job in video], [video_id])
        with self.Session.begin() as db:
            publish = claim_due_jobs(db, worker_id="publish-worker", logical_key_prefix="publish:")
            self.assertEqual([job.id for job in publish], [publish_id])

    def test_complete_requires_current_lease_and_is_idempotent(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        job_id = self.enqueue(available_at=start)
        with self.Session.begin() as db:
            token = claim_due_jobs(db, worker_id="worker-a", now=start)[0].lease_token
        with self.Session.begin() as db:
            self.assertFalse(complete_job(db, job_id=job_id, lease_token="wrong", now=start))
            self.assertTrue(complete_job(db, job_id=job_id, lease_token=token, now=start))
            self.assertTrue(complete_job(db, job_id=job_id, lease_token=token, now=start))
            self.assertFalse(fail_job(db, job_id=job_id, lease_token=token, error="late", now=start))
        with self.Session.begin() as db:
            self.assertEqual(claim_due_jobs(db, worker_id="worker-b", now=start + timedelta(days=1)), [])
            job = db.get(WorkflowJob, job_id)
            self.assertEqual(job.state, "succeeded")
            self.assertEqual(job.finished_at.replace(tzinfo=timezone.utc), start)

    def test_completion_with_loaded_job_uses_database_lease_condition(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.enqueue(available_at=start)
        with self.Session.begin() as db:
            job = claim_due_jobs(db, worker_id="worker-a", now=start)[0]
            self.assertTrue(complete_job(db, job_id=job.id, lease_token=job.lease_token,
                                         now=start + timedelta(seconds=1)))
            db.refresh(job)
            self.assertEqual(job.state, "succeeded")

    def test_failure_can_requeue_then_terminally_fail_with_fencing(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        job_id = self.enqueue(available_at=start)
        with self.Session.begin() as db:
            old_token = claim_due_jobs(db, worker_id="worker-a", lease_seconds=10, now=start)[0].lease_token
        with self.Session.begin() as db:
            self.assertTrue(fail_job(db, job_id=job_id, lease_token=old_token, error="timeout",
                                     retry_delay_seconds=20, now=start + timedelta(seconds=2)))
            self.assertTrue(fail_job(db, job_id=job_id, lease_token=old_token, error="timeout",
                                     retry_delay_seconds=20, now=start + timedelta(seconds=3)))
            job = db.get(WorkflowJob, job_id)
            self.assertEqual(job.state, "queued")
            self.assertEqual(job.last_error, "timeout")
            self.assertEqual(job.attempt_count, 1)
            self.assertEqual(job.available_at.replace(tzinfo=timezone.utc), start + timedelta(seconds=22))
        with self.Session.begin() as db:
            self.assertEqual(claim_due_jobs(db, worker_id="worker-b", now=start + timedelta(seconds=21)), [])
            new_token = claim_due_jobs(db, worker_id="worker-b", now=start + timedelta(seconds=22))[0].lease_token
            self.assertNotEqual(new_token, old_token)
            self.assertFalse(complete_job(db, job_id=job_id, lease_token=old_token,
                                          now=start + timedelta(seconds=22)))
            self.assertTrue(fail_job(db, job_id=job_id, lease_token=new_token, error="provider refused",
                                     now=start + timedelta(seconds=23)))
            self.assertTrue(fail_job(db, job_id=job_id, lease_token=new_token, error="provider refused",
                                     now=start + timedelta(seconds=23)))
        with self.Session.begin() as db:
            self.assertEqual(claim_due_jobs(db, worker_id="worker-c", now=start + timedelta(days=1)), [])
            job = db.get(WorkflowJob, job_id)
            self.assertEqual(job.state, "failed")
            self.assertEqual(job.attempt_count, 2)
            self.assertEqual(job.last_error, "provider refused")

    def test_retry_with_loaded_job_uses_database_lease_condition(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.enqueue(available_at=start)
        with self.Session.begin() as db:
            job = claim_due_jobs(db, worker_id="worker-a", now=start)[0]
            self.assertTrue(fail_job(db, job_id=job.id, lease_token=job.lease_token,
                                     error="poll_pending", retry_delay_seconds=0,
                                     now=start + timedelta(seconds=1)))
            db.refresh(job)
            self.assertEqual(job.state, "queued")


class JobMigrationTest(unittest.TestCase):
    def test_upgrade_preserves_existing_asset_and_adds_job_schema(self):
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
from sqlalchemy import event, inspect, text
from app.db import engine
@event.listens_for(engine, "connect")
def enable_fks(connection, record): connection.execute("PRAGMA foreign_keys=ON")
config = Config("alembic.ini")
command.upgrade(config, "0006_workflow_runs")
with engine.begin() as connection:
    connection.execute(text("INSERT INTO users (id,email,password_hash,is_admin,is_active) VALUES ('u','u@example.com','hash',0,1)"))
    connection.execute(text("INSERT INTO workspaces (id,name,owner_id,plan,created_at) VALUES ('w','Test','u','trial','2026-01-01')"))
    connection.execute(text("INSERT INTO assets (id,workspace_id,filename,content_type,bytes,created_at) VALUES ('a','w','old.mp4','video/mp4',8,'2026-01-01')"))
command.upgrade(config, "head")
assert inspect(engine).has_table("workflow_jobs")
with engine.connect() as connection:
    row = connection.execute(text("SELECT project_id,run_id,step_id,provider,model FROM assets WHERE id='a'")).one()
    assert row == (None,None,None,None,None), row
command.downgrade(config, "0006_workflow_runs")
with engine.connect() as connection:
    assert connection.execute(text("SELECT filename FROM assets WHERE id='a'")).scalar_one() == "old.mp4"
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)},
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


class PostgreSQLJobClaimTest(unittest.TestCase):
    def test_locked_job_is_skipped_by_another_worker(self):
        raw_url = os.environ.get("REELFORGE_TEST_DATABASE_URL")
        if not raw_url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        url = make_url(raw_url)
        if not url.drivername.startswith("postgresql") or "test" not in (url.database or "").lower():
            self.skipTest("refusing to write to a database without 'test' in its name")
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        schema = f"queue_test_{uuid4().hex}"
        admin_engine = create_engine(url)
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        Session = sessionmaker(engine, expire_on_commit=False)
        try:
            Base.metadata.create_all(engine)
            with Session.begin() as db:
                db.add(User(id="u", email="u@example.com", password_hash="hash"))
                db.flush()
                db.add(Workspace(id="w", name="Test", owner_id="u"))
                db.flush()
                db.add(Project(id="p", workspace_id="w", title="Movie"))
                db.add(Workflow(id="f", workspace_id="w", name="Flow"))
                db.flush()
                db.add(WorkflowRun(id="r", workspace_id="w", workflow_id="f", project_id="p",
                                   graph_snapshot="{}", status="running"))
                db.flush()
                db.add(WorkflowRunStep(id="s", run_id="r", node_id="video", node_type="video",
                                       position=0, status="pending", detail=""))
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            with Session.begin() as db:
                enqueue_job(db, workspace_id="w", run_id="r", step_id="s", logical_key="r:video",
                            payload={"prompt": "A"}, available_at=start)
            with Session.begin() as first:
                claimed = claim_due_jobs(first, worker_id="worker-a", now=start)
                self.assertEqual(len(claimed), 1)
                with Session.begin() as second:
                    second.execute(text("SET LOCAL statement_timeout = '2s'"))
                    self.assertEqual(claim_due_jobs(second, worker_id="worker-b", now=start), [])
            with Session.begin() as second:
                self.assertEqual(claim_due_jobs(second, worker_id="worker-b", now=start), [])
        finally:
            engine.dispose()
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            admin_engine.dispose()


if __name__ == "__main__":
    unittest.main()
