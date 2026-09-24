"""YouTube OAuth connection tests with local databases and fake Google HTTP."""

from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
import httpx
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, Membership, User, Workspace
from app.publishers.google_oauth import (
    ConnectionStatus,
    GoogleOAuthConfig,
    OAuthError,
    YouTubeConnection,
    YouTubeOAuthState,
    begin_authorization,
    complete_authorization,
    connection_status,
    get_access_token,
)
from app.publishers.youtube import UPLOAD_SCOPE


class GoogleOAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "oauth.db"
        self.engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.config = GoogleOAuthConfig(
            client_id="test-client.apps.googleusercontent.com",
            client_secret="test-secret",
            redirect_uri="https://studio.example.com/api/youtube/callback",
            encryption_key=Fernet.generate_key().decode("ascii"),
        )
        with self.Session.begin() as db:
            db.add_all([
                User(id="user-1", email="u1@example.com", password_hash="hash"),
                User(id="user-2", email="u2@example.com", password_hash="hash"),
                Workspace(id="workspace-1", owner_id="user-1", name="First"),
                Workspace(id="workspace-2", owner_id="user-2", name="Second"),
                Membership(user_id="user-1", workspace_id="workspace-1", role="owner"),
                Membership(user_id="user-2", workspace_id="workspace-2", role="owner"),
            ])

    def _start(self):
        with self.Session.begin() as db:
            return begin_authorization(db, self.config, workspace_id="workspace-1", user_id="user-1")

    def test_authorization_url_uses_state_pkce_and_offline_upload_scope(self):
        started = self._start()
        self.assertNotIn(started.state, repr(started))
        parsed = urlsplit(started.url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/v2/auth")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["scope"], [UPLOAD_SCOPE])
        self.assertEqual(query["state"], [started.state])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], [self.config.redirect_uri])

        with self.Session() as db:
            pending = db.scalar(select(YouTubeOAuthState))
            self.assertEqual(pending.state_hash, sha256(started.state.encode()).hexdigest())
            self.assertNotIn(started.state, pending.verifier_ciphertext)
            self.assertEqual(pending.workspace_id, "workspace-1")
            self.assertEqual(pending.user_id, "user-1")
            verifier = Fernet(self.config.encryption_key.encode()).decrypt(pending.verifier_ciphertext.encode()).decode()
            challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            self.assertEqual(query["code_challenge"], [challenge])

    def test_callback_consumes_state_then_stores_encrypted_tokens_for_its_workspace(self):
        started = self._start()
        seen = []

        def handle(request):
            seen.append(request)
            self.assertEqual(request.url.host, "oauth2.googleapis.com")
            form = parse_qs(request.content.decode())
            self.assertEqual(form["grant_type"], ["authorization_code"])
            self.assertEqual(form["code"], ["auth-code"])
            self.assertEqual(form["client_secret"], ["test-secret"])
            self.assertTrue(form["code_verifier"][0])
            with self.Session() as other_db:
                pending = other_db.scalar(select(YouTubeOAuthState))
                self.assertIsNotNone(pending.consumed_at)
            return httpx.Response(200, json={
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": UPLOAD_SCOPE,
            })

        with self.Session() as db, httpx.Client(transport=httpx.MockTransport(handle)) as client:
            status = complete_authorization(
                db, self.config, state=started.state, code="auth-code", current_user_id="user-1", client=client
            )
        self.assertIsInstance(status, ConnectionStatus)
        self.assertEqual(status.workspace_id, "workspace-1")
        self.assertEqual(len(seen), 1)
        with self.Session() as db:
            connection = db.get(YouTubeConnection, "workspace-1")
            self.assertIsNotNone(connection)
            self.assertNotIn("access-secret", connection.access_token_ciphertext)
            self.assertNotIn("refresh-secret", connection.refresh_token_ciphertext)
            self.assertIsNone(db.get(YouTubeConnection, "workspace-2"))
            self.assertEqual(connection_status(db, workspace_id="workspace-1").workspace_id, "workspace-1")

    def test_state_is_bound_to_user_and_single_use_even_when_exchange_fails(self):
        started = self._start()
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(400, json={"error": "invalid_grant"})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.Session() as db:
                with self.assertRaises(OAuthError) as raised:
                    complete_authorization(
                        db, self.config, state=started.state, code="code", current_user_id="user-2", client=client
                    )
                self.assertEqual(raised.exception.code, "invalid_state")
            self.assertEqual(seen, [])
            with self.Session() as db:
                with self.assertRaises(OAuthError) as raised:
                    complete_authorization(
                        db, self.config, state=started.state, code="code", current_user_id="user-1", client=client
                    )
                self.assertEqual(raised.exception.code, "google_token_error")
            self.assertEqual(len(seen), 1)
            with self.Session() as db:
                with self.assertRaises(OAuthError) as raised:
                    complete_authorization(
                        db, self.config, state=started.state, code="code", current_user_id="user-1", client=client
                    )
                self.assertEqual(raised.exception.code, "invalid_state")
            self.assertEqual(len(seen), 1)

    def test_expired_state_never_reaches_google(self):
        started = self._start()
        with self.Session.begin() as db:
            pending = db.scalar(select(YouTubeOAuthState))
            pending.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        seen = []
        def handle(request):
            seen.append(request)
            return httpx.Response(200, json={})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.Session() as db, self.assertRaises(OAuthError) as raised:
                complete_authorization(
                    db, self.config, state=started.state, code="code", current_user_id="user-1", client=client
                )
        self.assertEqual(raised.exception.code, "invalid_state")
        self.assertEqual(seen, [])

    def test_callback_rejects_initiator_demoted_from_workspace_owner(self):
        started = self._start()
        with self.Session.begin() as db:
            db.get(Membership, ("user-1", "workspace-1")).role = "editor"
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json={})

        with self.Session() as db, httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(OAuthError) as raised:
                complete_authorization(
                    db, self.config, state=started.state, code="code", current_user_id="user-1", client=client
                )
        self.assertEqual(raised.exception.code, "invalid_state")
        self.assertEqual(seen, [])

    def test_expiring_connection_refreshes_and_keeps_existing_refresh_token(self):
        started = self._start()

        def exchange(request):
            return httpx.Response(200, json={
                "access_token": "old-access", "refresh_token": "old-refresh",
                "expires_in": 3600, "token_type": "Bearer", "scope": UPLOAD_SCOPE,
            })

        with self.Session() as db, httpx.Client(transport=httpx.MockTransport(exchange)) as client:
            complete_authorization(db, self.config, state=started.state, code="code", current_user_id="user-1", client=client)
        with self.Session.begin() as db:
            db.get(YouTubeConnection, "workspace-1").expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        seen = []

        def refresh(request):
            seen.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={
                "access_token": "new-access", "expires_in": 3600,
                "token_type": "Bearer", "scope": UPLOAD_SCOPE,
            })

        with self.Session() as db, httpx.Client(transport=httpx.MockTransport(refresh)) as client:
            token = get_access_token(db, self.config, workspace_id="workspace-1", client=client)
        self.assertEqual(token, "new-access")
        self.assertEqual(seen[0]["refresh_token"], ["old-refresh"])
        self.assertEqual(seen[0]["grant_type"], ["refresh_token"])
        with self.Session() as db:
            connection = db.get(YouTubeConnection, "workspace-1")
            self.assertEqual(Fernet(self.config.encryption_key.encode()).decrypt(connection.refresh_token_ciphertext.encode()), b"old-refresh")
            self.assertNotIn("new-access", connection.access_token_ciphertext)

    def test_fresh_consent_rotates_connection_generation_but_refresh_does_not(self):
        def token_response(request):
            return httpx.Response(200, json={
                "access_token": "access", "refresh_token": "refresh",
                "expires_in": 3600, "token_type": "Bearer", "scope": UPLOAD_SCOPE,
            })

        with httpx.Client(transport=httpx.MockTransport(token_response)) as client:
            first = self._start()
            with self.Session() as db:
                complete_authorization(db, self.config, state=first.state, code="code-a",
                                       current_user_id="user-1", client=client)
            original = datetime(2026, 1, 1, tzinfo=timezone.utc)
            with self.Session.begin() as db:
                db.get(YouTubeConnection, "workspace-1").connected_at = original
            second = self._start()
            with self.Session() as db:
                complete_authorization(db, self.config, state=second.state, code="code-b",
                                       current_user_id="user-1", client=client)
            with self.Session() as db:
                connection = db.get(YouTubeConnection, "workspace-1")
                self.assertGreater(connection.connected_at.replace(tzinfo=timezone.utc), original)
                consent_generation = connection.connected_at
            with self.Session.begin() as db:
                db.get(YouTubeConnection, "workspace-1").expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            with self.Session() as db:
                get_access_token(db, self.config, workspace_id="workspace-1", client=client)
            with self.Session() as db:
                self.assertEqual(db.get(YouTubeConnection, "workspace-1").connected_at, consent_generation)

    def test_fresh_consent_without_refresh_token_preserves_previous_connection(self):
        first = self._start()

        def first_exchange(request):
            return httpx.Response(200, json={
                "access_token": "channel-a-access", "refresh_token": "channel-a-refresh",
                "expires_in": 3600, "token_type": "Bearer", "scope": UPLOAD_SCOPE,
            })

        with self.Session() as db, httpx.Client(transport=httpx.MockTransport(first_exchange)) as client:
            complete_authorization(db, self.config, state=first.state, code="code-a",
                                   current_user_id="user-1", client=client)
        with self.Session() as db:
            previous = db.get(YouTubeConnection, "workspace-1")
            previous_values = (previous.connected_at, previous.access_token_ciphertext,
                               previous.refresh_token_ciphertext)

        second = self._start()

        def second_exchange(request):
            return httpx.Response(200, json={
                "access_token": "channel-b-access", "expires_in": 3600,
                "token_type": "Bearer", "scope": UPLOAD_SCOPE,
            })

        with self.Session() as db, httpx.Client(transport=httpx.MockTransport(second_exchange)) as client:
            with self.assertRaises(OAuthError) as raised:
                complete_authorization(db, self.config, state=second.state, code="code-b",
                                       current_user_id="user-1", client=client)
        self.assertEqual(raised.exception.code, "missing_refresh_token")
        with self.Session() as db:
            connection = db.get(YouTubeConnection, "workspace-1")
            self.assertEqual((connection.connected_at, connection.access_token_ciphertext,
                              connection.refresh_token_ciphertext), previous_values)

    def test_migration_adds_connection_tables_after_auth_security(self):
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
assert inspect(engine).has_table('youtube_connections')
assert inspect(engine).has_table('youtube_oauth_states')
assert 'youtube_connections' in Base.metadata.tables
assert 'youtube_oauth_states' in Base.metadata.tables
"""
        result = subprocess.run([sys.executable, "-c", program], cwd=target,
            env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
