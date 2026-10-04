"""Regression tests for persistent login throttling and setup serialization."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import auth_security
from app.auth_security import (
    AuthAttempt,
    check_login_allowed,
    clear_login_failures,
    lock_initial_setup,
    record_login_failure,
)
from app.models import Base, SystemSetting, User


UTC = timezone.utc
START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class AuthSecurityTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "security.db"
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 5})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_five_failures_block_across_sessions_then_expire(self):
        identifier = "Owner@Example.com|127.0.0.1"
        for attempt in range(4):
            with self.Session.begin() as db:
                self.assertTrue(check_login_allowed(db, identifier, START + timedelta(minutes=attempt)))
                self.assertEqual(record_login_failure(db, identifier, START + timedelta(minutes=attempt)), attempt + 1)
        with self.Session.begin() as db:
            self.assertTrue(check_login_allowed(db, identifier, START + timedelta(minutes=4)))
            self.assertEqual(record_login_failure(db, identifier, START + timedelta(minutes=4)), 5)
        with self.Session.begin() as db:
            self.assertFalse(check_login_allowed(db, identifier, START + timedelta(minutes=5)))
        with self.Session.begin() as db:
            self.assertTrue(check_login_allowed(db, identifier, START + timedelta(minutes=19)))
            self.assertEqual(record_login_failure(db, identifier, START + timedelta(minutes=19)), 1)
        with self.Session() as db:
            row = db.scalar(select(AuthAttempt))
            self.assertEqual(row.failure_count, 1)
            self.assertNotIn("owner@example.com", row.identifier_hash)
            self.assertEqual(len(row.identifier_hash), 64)

    def test_success_clears_failed_attempts(self):
        identifier = "owner@example.com|127.0.0.1"
        with self.Session.begin() as db:
            record_login_failure(db, identifier, START)
        with self.Session.begin() as db:
            clear_login_failures(db, identifier)
        with self.Session.begin() as db:
            self.assertTrue(check_login_allowed(db, identifier, START + timedelta(minutes=1)))
            self.assertEqual(record_login_failure(db, identifier, START + timedelta(minutes=1)), 1)

    def test_window_resets_after_fifteen_minutes_without_lockout(self):
        identifier = "owner@example.com|127.0.0.1"
        with self.Session.begin() as db:
            self.assertEqual(record_login_failure(db, identifier, START), 1)
        with self.Session.begin() as db:
            self.assertEqual(record_login_failure(db, identifier, START + timedelta(minutes=16)), 1)

    def test_storage_rejects_negative_failure_count(self):
        with self.assertRaises(IntegrityError):
            with self.Session.begin() as db:
                db.add(AuthAttempt(identifier_hash="0" * 64, failure_count=-1,
                    window_started_at=START, updated_at=START))

    def test_cleanup_is_batched_and_preserves_recent_and_active_blocks(self):
        old = START - timedelta(days=2)
        with self.Session.begin() as db:
            for digit in "123":
                db.add(AuthAttempt(identifier_hash=digit * 64, failure_count=1,
                    window_started_at=old, updated_at=old))
            db.add(AuthAttempt(identifier_hash="4" * 64, failure_count=1,
                window_started_at=START, updated_at=START))
            db.add(AuthAttempt(identifier_hash="5" * 64, failure_count=5,
                window_started_at=old, updated_at=old,
                blocked_until=START + timedelta(minutes=5)))
        with self.Session.begin() as db:
            self.assertEqual(auth_security.prune_expired_login_attempts(db, START, batch_size=2), 2)
        with self.Session.begin() as db:
            self.assertEqual(auth_security.prune_expired_login_attempts(db, START, batch_size=2), 1)
        with self.Session() as db:
            self.assertEqual({row.identifier_hash for row in db.scalars(select(AuthAttempt))},
                {"4" * 64, "5" * 64})
        with self.Session.begin() as db:
            with self.assertRaises(ValueError):
                auth_security.prune_expired_login_attempts(db, START, batch_size=1000)

    def test_login_check_opportunistically_removes_expired_rows(self):
        old = START - timedelta(days=2)
        with self.Session.begin() as db:
            db.add(AuthAttempt(identifier_hash="6" * 64, failure_count=1,
                window_started_at=old, updated_at=old))
        with self.Session.begin() as db:
            self.assertTrue(check_login_allowed(db, "new@example.com|127.0.0.1", START))
        with self.Session() as db:
            self.assertIsNone(db.get(AuthAttempt, "6" * 64))

    def test_cleanup_does_not_delete_identifier_locked_by_current_login(self):
        identifier = "owner@example.com|127.0.0.1"
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        old = START - timedelta(days=2)
        with self.Session.begin() as db:
            db.add(AuthAttempt(identifier_hash=digest, failure_count=3,
                window_started_at=old, updated_at=old))
        with self.Session.begin() as db:
            self.assertTrue(check_login_allowed(db, identifier, START))
            self.assertEqual(db.get(AuthAttempt, digest).failure_count, 3)

    def test_setup_lock_serializes_two_transactions(self):
        with self.Session.begin() as db:
            db.add(SystemSetting(key="registration_enabled", value="true"))
        first_locked = threading.Event()
        allow_commit = threading.Event()
        second_started = threading.Event()

        def first_setup():
            with self.Session.begin() as db:
                lock_initial_setup(db)
                self.assertEqual(db.scalar(select(func.count()).select_from(User)), 0)
                db.add(User(id="first", email="first@example.com", password_hash="hash", is_admin=True))
                first_locked.set()
                if not allow_commit.wait(timeout=5):
                    raise TimeoutError("Test never released first setup transaction")

        def second_setup():
            second_started.set()
            with self.Session.begin() as db:
                lock_initial_setup(db)
                return db.scalar(select(func.count()).select_from(User))

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(first_setup)
            self.assertTrue(first_locked.wait(timeout=5))
            second = workers.submit(second_setup)
            self.assertTrue(second_started.wait(timeout=5))
            self.assertFalse(second.done())
            allow_commit.set()
            first.result(timeout=5)
            self.assertEqual(second.result(timeout=5), 1)

    def test_migration_creates_throttle_storage(self):
        root = Path(__file__).resolve().parents[1]
        target = Path(self.directory.name) / "migration"
        for folder in ("app", "migrations"):
            shutil.copytree(root / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(root / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        (target / "instance" / "bootstrap.json").write_text(json.dumps({
            "database_url": f"sqlite:///{target}/instance/test.db",
        }))
        program = """
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.db import engine
from app.models import Base
command.upgrade(Config('alembic.ini'), 'head')
assert inspect(engine).has_table('auth_login_attempts')
assert 'auth_login_attempts' in Base.metadata.tables
"""
        result = subprocess.run([sys.executable, "-c", program], cwd=target,
            env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])

    def test_login_route_persists_failures_and_clears_on_success(self):
        root = Path(__file__).resolve().parents[1]
        target = Path(self.directory.name) / "api"
        for folder in ("app", "migrations"):
            shutil.copytree(root / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(root / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        (target / "instance" / "bootstrap.json").write_text(json.dumps({
            "database_url": f"sqlite:///{target}/instance/test.db",
        }))
        program = """
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import Session
from app.auth_security import AuthAttempt
command.upgrade(Config('alembic.ini'), 'head')
from app.main import app
client = TestClient(app, headers={"Origin": "http://testserver"})
valid = {'email': 'owner@example.com', 'password': 'a-long-correct-password'}
invalid = {'email': valid['email'], 'password': 'wrong-password'}
assert client.post('/api/setup', json=valid).status_code == 200
assert client.post('/api/logout').status_code == 200
for _ in range(2):
    assert client.post('/api/login', json=invalid).status_code == 401
assert client.post('/api/login', json=valid).status_code == 200
with Session() as db:
    assert db.scalar(select(AuthAttempt)) is None
assert client.post('/api/logout').status_code == 200
for _ in range(5):
    assert client.post('/api/login', json=invalid).status_code == 401
assert client.post('/api/login', json=valid).status_code == 429
with Session() as db:
    attempt = db.scalar(select(AuthAttempt))
    assert attempt.failure_count == 5
    assert 'owner@example.com' not in attempt.identifier_hash
"""
        result = subprocess.run([sys.executable, "-c", program], cwd=target,
            env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
