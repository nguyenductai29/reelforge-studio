"""Movie sources on PostgreSQL (with REELFORGE_TEST_DATABASE_URL): two movie workers never lease the same source
(``FOR UPDATE SKIP LOCKED``), a lease holds until it expires, and a deletion requested while a run is starting on the
source waits for that run's row lock, then is refused."""
from datetime import datetime, timedelta, timezone
import os
import threading
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import movie_sources
from app.models import Base, MovieSource, Project, User, Workflow, WorkflowRun, Workspace


def database_url():
    raw_url = os.environ.get("REELFORGE_TEST_DATABASE_URL")
    if not raw_url:
        raise unittest.SkipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql") or "test" not in (url.database or "").lower():
        raise unittest.SkipTest("refusing to write to a database without 'test' in its name")
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


class PostgreSQLMovieSourceTest(unittest.TestCase):
    def setUp(self):
        url = database_url()
        self.schema = f"movie_test_{uuid4().hex}"
        self.admin_engine = create_engine(url)
        with self.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
        self.engine = create_engine(url, connect_args={"options": f"-csearch_path={self.schema}"})
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self.start = datetime.now(timezone.utc)
        with self.Session.begin() as db:
            db.add(User(id="u", email="u@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="w", name="Test", owner_id="u"))
            db.flush()
            db.add(Project(id="p", workspace_id="w", title="Movie"))
            db.add(Workflow(id="f", workspace_id="w", name="Review"))
            db.flush()
            db.add(WorkflowRun(id="r", workspace_id="w", workflow_id="f", project_id="p", graph_snapshot="{}",
                               status="running"))

    def tearDown(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.admin_engine.dispose()

    def add_source(self, source_id: str, status: str, created: datetime) -> None:
        with self.Session.begin() as db:
            db.add(MovieSource(id=source_id, workspace_id="w", created_by_user_id="u", source_type="local",
                               original_name=f"{source_id}.mp4", status=status, created_at=created,
                               updated_at=created, expires_at=created + timedelta(days=7), delete_after_success=True,
                               delete_grace_hours=24, attempt_count=0))

    def test_two_workers_never_lease_the_same_source(self):
        self.add_source("first", "importing", self.start - timedelta(minutes=2))
        self.add_source("second", "delete_scheduled", self.start - timedelta(minutes=1))
        self.add_source("idle", "ready", self.start - timedelta(minutes=3))  # nothing for a worker to do
        with self.Session.begin() as one:
            claimed = movie_sources.claim_work(one, worker_id="worker-a")
            self.assertEqual(claimed.id, "first")
            stale_token = claimed.lease_token
            with self.Session.begin() as two:
                two.execute(text("SET LOCAL statement_timeout = '2s'"))  # a wait on the locked row would fail here
                self.assertEqual(movie_sources.claim_work(two, worker_id="worker-b").id, "second")
                with self.Session.begin() as three:
                    three.execute(text("SET LOCAL statement_timeout = '2s'"))
                    self.assertIsNone(movie_sources.claim_work(three, worker_id="worker-c"))
        # Committed leases hold until they expire; then another worker takes over.
        with self.Session.begin() as db:
            self.assertIsNone(movie_sources.claim_work(db, worker_id="worker-c",
                                                       now=self.start + timedelta(minutes=5)))
        later = self.start + timedelta(seconds=movie_sources.LEASE_SECONDS + 60)
        with self.Session.begin() as db:
            again = movie_sources.claim_work(db, worker_id="worker-c", now=later)
            self.assertEqual((again.id, again.worker_id, again.attempt_count), ("first", "worker-c", 2))
            token = again.lease_token
        self.assertFalse(movie_sources.renew(self.Session, "first", stale_token))  # worker-a lost it
        self.assertTrue(movie_sources.renew(self.Session, "first", token, progress=1024))
        with self.Session.begin() as db:
            self.assertIsNone(movie_sources.live(db, "first", stale_token))
            self.assertEqual(movie_sources.live(db, "first", token).progress_bytes, 1024)

    def test_deletion_waits_for_a_starting_run_then_is_refused(self):
        self.add_source("movie", "ready", self.start - timedelta(hours=1))
        outcome: dict = {}

        def delete_now():
            try:
                with self.Session.begin() as db:
                    db.execute(text("SET LOCAL lock_timeout = '20s'"))
                    source = movie_sources.lock(db, "movie")
                    movie_sources.request_delete(db, source, user_id="u")
                    outcome["status"] = source.status
            except movie_sources.MovieSourceError as error:
                outcome["code"] = error.code

        with self.Session.begin() as run_start:
            # The Movie Source step of run "r": the source locked, the use recorded, not committed yet.
            source = movie_sources.lock(run_start, "movie")
            movie_sources.begin_use(run_start, source, run_start.get(WorkflowRun, "r"))
            deleter = threading.Thread(target=delete_now)
            deleter.start()
            deleter.join(1.5)
            self.assertTrue(deleter.is_alive(), "the deletion must wait for the row lock")
        deleter.join(30)
        self.assertFalse(deleter.is_alive())
        self.assertEqual(outcome, {"code": "source_in_use"})
        with self.Session.begin() as db:
            self.assertEqual(db.get(MovieSource, "movie").status, "processing")
            db.get(WorkflowRun, "r").status = "completed"
        # Once the run is over, the same request is accepted.
        with self.Session.begin() as db:
            source = movie_sources.lock(db, "movie")
            self.assertEqual(movie_sources.request_delete(db, source, user_id="u").status, "delete_scheduled")


if __name__ == "__main__":
    unittest.main()
