"""The production domain change: https://studio.imokome-cloud.com → https://reelforge.mul-service.com (migration 0026).

* case A: the exact old production origin moves to the new one, with Secure cookies (never turned off);
* case B: a custom origin stays, and so does its cookie choice;
* case C: the new origin already stored stays (idempotent);
* case D: a development origin such as http://localhost:3000 stays;
* case E: a new installation migrated from nothing stores the new origin when the API starts;
* case F: an installation at 0024 (or at 0021's localhost defaults) reaches the new origin through head;
* case G: a downgrade returns only the exact new origin to the old one;
* OAuth redirect overrides that are exactly old-origin callbacks follow the origin; any other stays, and readiness
  and the pre-flight warn about a redirect that is not under the public origin;
* every derived public URL (payOS webhook, OnePAY IPN and return, OAuth callbacks, email links) uses the new origin;
* the same-origin check accepts the new origin and refuses the old one and any other.

Offline: every program runs in a disposable copy of the app on SQLite (PostgreSQL for the migration when
REELFORGE_TEST_DATABASE_URL names an isolated test database).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OLD, NEW = "https://studio.imokome-cloud.com", "https://reelforge.mul-service.com"


def run(program: str, database_url: str | None = None) -> subprocess.CompletedProcess:
    """``program`` in a disposable copy of the app, as the production server runs it (no local origin override)."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("app", "migrations"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        url = database_url or f"sqlite:///{target}/instance/test.db"
        (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": url}))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("GOOGLE_", "TIKTOK_", "FACEBOOK_", "REELFORGE_ENV_FILE", "PAYOS_", "ONEPAY_"))}
        return subprocess.run([sys.executable, "-c", program], cwd=target,
                              env={**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"},
                              capture_output=True, text=True, encoding="utf-8", timeout=300)


def postgresql_url() -> str:
    url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
        return ""
    return "postgresql+psycopg://" + url[len("postgresql://"):] if url.startswith("postgresql://") else url


COMMON = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
config = Config("alembic.ini")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
OLD, NEW = "https://studio.imokome-cloud.com", "https://reelforge.mul-service.com"
# HEAD is the newest migration: the chain upgrades through 0026 to it (0027 adds the movie source tables).
BEFORE, HEAD = "0025_verification_status", "0027_movie_sources"
OTHERS = {"storage_dir": "instance/media", "trial_project_limit": 2, "registration_enabled": True}
CALLBACKS = {"social.youtube.redirect_uri": "/youtube/callback", "social.tiktok.redirect_uri": "/channels/callback/tiktok",
             "social.facebook.redirect_uri": "/channels/callback/facebook"}

def store(values, overrides=None, by=None):
    with engine.begin() as c:
        c.execute(text("DELETE FROM system_settings"))
        c.execute(text("DELETE FROM system_config"))
        for key, value in {**OTHERS, **values}.items():
            c.execute(text("INSERT INTO system_settings (key, value) VALUES (:k, :v)"), {"k": key, "v": json.dumps(value)})
        for key, value in (overrides or {}).items():
            c.execute(text("INSERT INTO system_config (key, value, updated_at, updated_by_user_id) "
                           "VALUES (:k, :v, '2026-09-01 00:00:00', :by)"), {"k": key, "v": json.dumps(value), "by": by})

def stored():
    with engine.connect() as c:
        return {key: json.loads(value) for key, value in c.execute(text("SELECT key, value FROM system_settings")).all()}

def overrides():
    with engine.connect() as c:
        return {key: json.loads(value) for key, value in c.execute(text("SELECT key, value FROM system_config")).all()}
'''

MIGRATION = COMMON + r'''
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
command.upgrade(config, BEFORE)

KEPT = None
OLD_CALLBACKS = {key: OLD + path for key, path in CALLBACKS.items()}
NEW_CALLBACKS = {key: NEW + path for key, path in CALLBACKS.items()}
CUSTOM_CALLBACK = {"social.tiktok.redirect_uri": "https://custom.example.com/channels/callback/tiktok"}
# (stored before 0026, overrides before, stored after upgrading, overrides after upgrading, stored after downgrading)
CASES = [
    # A: the old production default moves; Secure cookies stay on, or are turned on; never off.
    ({"frontend_origin": OLD, "secure_cookies": True}, {}, {"frontend_origin": NEW, "secure_cookies": True}, {},
     {"frontend_origin": OLD, "secure_cookies": True}),
    ({"frontend_origin": OLD, "secure_cookies": False}, {}, {"frontend_origin": NEW, "secure_cookies": True}, {},
     {"frontend_origin": OLD, "secure_cookies": True}),
    ({"frontend_origin": OLD}, {}, {"frontend_origin": NEW}, {}, {"frontend_origin": OLD}),  # no cookie row invented
    # A, with the old callbacks typed as overrides: they follow; a custom one stays.
    ({"frontend_origin": OLD, "secure_cookies": True}, {**OLD_CALLBACKS, **CUSTOM_CALLBACK},
     {"frontend_origin": NEW, "secure_cookies": True},
     {**NEW_CALLBACKS, **CUSTOM_CALLBACK}, {"frontend_origin": OLD, "secure_cookies": True}),
    # B: a custom origin, its cookie choice and its overrides stay (even an old-origin callback).
    ({"frontend_origin": "https://custom.example.com", "secure_cookies": False}, OLD_CALLBACKS, KEPT, KEPT, KEPT),
    ({"frontend_origin": "https://custom.example.com", "secure_cookies": True}, {}, KEPT, KEPT, KEPT),
    # C: the new origin already stored: unchanged by the upgrade (its stale old callbacks follow it).
    ({"frontend_origin": NEW, "secure_cookies": True}, {}, KEPT, KEPT, {"frontend_origin": OLD, "secure_cookies": True}),
    ({"frontend_origin": NEW, "secure_cookies": False}, OLD_CALLBACKS, KEPT, NEW_CALLBACKS,
     {"frontend_origin": OLD, "secure_cookies": False}),
    # D: development origins stay.
    ({"frontend_origin": "http://localhost:3000", "secure_cookies": False}, {}, KEPT, KEPT, KEPT),
    ({"frontend_origin": "http://127.0.0.1:3010", "secure_cookies": False}, {}, KEPT, KEPT, KEPT),
    # Not exactly the old origin: kept.
    ({"frontend_origin": OLD + "/", "secure_cookies": False}, {}, KEPT, KEPT, KEPT),
    ({"frontend_origin": "http://studio.imokome-cloud.com", "secure_cookies": False}, {}, KEPT, KEPT, KEPT),
    ({"frontend_origin": "https://STUDIO.imokome-cloud.com", "secure_cookies": True}, {}, KEPT, KEPT, KEPT),
    ({"secure_cookies": False}, {}, KEPT, KEPT, KEPT),  # no origin row
    ({}, {}, KEPT, KEPT, KEPT),  # a new installation: the API stores the new defaults when it first starts
]
for before, before_overrides, after, after_overrides, downgraded in CASES:
    store(before, before_overrides)
    command.upgrade(config, HEAD)
    expected = {**OTHERS, **(before if after is KEPT else after)}
    expected_overrides = before_overrides if after_overrides is KEPT else after_overrides
    assert stored() == expected, (before, stored())
    assert overrides() == expected_overrides, (before, before_overrides, overrides())
    command.upgrade(config, HEAD)  # idempotent: nothing to do at head
    assert stored() == expected and overrides() == expected_overrides
    # G: only the exact new origin returns to the old one; nothing else changes back.
    command.downgrade(config, BEFORE)
    assert stored() == {**OTHERS, **(before if downgraded is KEPT else downgraded)}, ("downgrade", before, stored())
    if downgraded is not KEPT and stored().get("frontend_origin") == OLD:
        restored = {key: (OLD + CALLBACKS[key] if value == NEW + CALLBACKS[key] else value)
                    for key, value in expected_overrides.items()}
        assert overrides() == restored, ("downgrade overrides", before_overrides, overrides())
    else:
        assert overrides() == expected_overrides, ("downgrade overrides", before_overrides, overrides())
# A migration changes a value as nobody: a moved override no longer names the admin who typed the old one;
# one it keeps still does.
with engine.begin() as c:
    c.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) "
                   "VALUES ('admin-1', 'admin@example.com', 'x', true, true)"))
store({"frontend_origin": OLD, "secure_cookies": True}, {**OLD_CALLBACKS, **CUSTOM_CALLBACK}, by="admin-1")
command.upgrade(config, HEAD)
with engine.connect() as c:
    authors = dict(c.execute(text("SELECT key, updated_by_user_id FROM system_config")).all())
assert authors == {"social.youtube.redirect_uri": None, "social.facebook.redirect_uri": None,
                   "social.tiktok.redirect_uri": "admin-1"}, authors
print("ok")
'''

CHAIN = COMMON + r'''
START = __START__
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
command.upgrade(config, START)
if START == "0020_manual_payment_statuses":
    # An installation from before 0021, still on the localhost defaults the API stored when it first started.
    store({"frontend_origin": "http://localhost:3000", "secure_cookies": False})
elif START == "0024_operations":
    # F: an existing production database at 0024, on the old production origin since 0021.
    store({"frontend_origin": OLD, "secure_cookies": True})
command.upgrade(config, "head")
with engine.connect() as c:
    assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD
if START != "base":
    assert stored() == {**OTHERS, "frontend_origin": NEW, "secure_cookies": True}, stored()
else:
    assert stored() == {}, stored()  # E: nothing to migrate; the API stores its defaults when it starts
import app.main as main
from app.db import Session
with Session() as db:
    assert main.setting(db, "frontend_origin") == NEW and main.setting(db, "secure_cookies") is True
print("ok")
'''

APP = COMMON + r'''
import os
command.upgrade(config, "0024_operations")
# Production as it was: the old origin, a YouTube override typed as its old callback, a custom TikTok one.
store({"frontend_origin": OLD, "secure_cookies": True},
      {"social.youtube.redirect_uri": OLD + "/youtube/callback",
       "social.tiktok.redirect_uri": "https://custom.example.com/channels/callback/tiktok"})
command.upgrade(config, "head")
# OAuth apps configured (legacy variables are enough for the readiness check); no redirect variable. Their
# tokens are encrypted with the master key, which a configured app therefore needs.
from pathlib import Path
from cryptography.fernet import Fernet
Path("instance/master.key").write_text(Fernet.generate_key().decode())
os.environ.update({"GOOGLE_OAUTH_CLIENT_ID": "id.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "google-secret",
                   "TIKTOK_CLIENT_KEY": "tiktok-key", "TIKTOK_CLIENT_SECRET": "tiktok-secret",
                   "FACEBOOK_APP_ID": "1234567890", "FACEBOOK_APP_SECRET": "facebook-secret"})
from fastapi.testclient import TestClient
import app.main as main
from app import readiness, release_check, system_config
from app.db import Session
system_config.activate()
assert main.LOCAL == {}

# Derived public URLs follow the origin; the override that was the old callback followed it too.
assert system_config.frontend_origin() == NEW
assert system_config.redirect_uri("youtube") == NEW + "/youtube/callback"
assert system_config.redirect_uri("facebook") == NEW + "/channels/callback/facebook"
assert system_config.redirect_uri("tiktok") == "https://custom.example.com/channels/callback/tiktok"
assert main.public_link("/verify-email", "t0k") == NEW + "/verify-email#token=t0k"

client = TestClient(main.app, base_url="https://testserver")
OWNER = {"email": "owner@example.com", "password": "long-password-123"}
assert client.post("/api/setup", json=OWNER, headers={"Origin": NEW}).status_code == 200
setup = client.get("/api/admin/payment-config").json()
endpoints = {(item["provider"], endpoint["key"]): endpoint["url"]
             for item in setup["providers"] for endpoint in item.get("endpoints", [])}
assert endpoints == {("payos", "webhook"): NEW + "/api/webhooks/payos",
                     ("onepay", "ipn"): NEW + "/api/webhooks/onepay",
                     ("onepay", "return"): NEW + "/api/billing/onepay/return"}, endpoints

# Readiness and the pre-flight show the custom TikTok redirect as not under the public origin.
publishing = {check["key"]: check for check in readiness.publishing_checks()}
assert publishing["youtube"]["status"] == "ok" and publishing["youtube"]["redirect"] == NEW + "/youtube/callback"
assert publishing["facebook"]["status"] == "ok", publishing
assert publishing["tiktok"]["status"] == "warning" and publishing["tiktok"]["detail"] == "redirect_mismatch", publishing
check = release_check._redirect_check(NEW)
assert check.status == release_check.WARN and "tiktok https://custom.example.com" in check.detail, check
assert "youtube" not in check.detail and "facebook" not in check.detail, check

# The same-origin check: the new origin passes; the old one and any other are refused, before any password check.
client.cookies.clear()
assert client.post("/api/login", json=OWNER, headers={"Origin": NEW}).status_code == 200
stranger = TestClient(main.app, base_url="https://testserver")
for origin in (OLD, "https://evil.example.com"):
    response = stranger.post("/api/login", json=OWNER, headers={"Origin": origin})
    assert response.status_code == 403 and response.json()["detail"] == "Invalid origin", (origin, response.text)
    assert client.post("/api/logout", headers={"Origin": origin}).status_code == 403
# The old origin is accepted again only if an administrator stores it explicitly.
body = {"frontend_origin": OLD, "secure_cookies": True, "trial_project_limit": 2, "registration_enabled": True}
assert client.put("/api/settings/system", json=body, headers={"Origin": NEW}).status_code == 200
main._ORIGIN_CACHE.update(value=None)
assert stranger.post("/api/login", json=OWNER, headers={"Origin": OLD}).status_code == 200
assert stranger.post("/api/login", json=OWNER, headers={"Origin": NEW}).status_code == 403
print("ok")
'''


class DomainMigrationTest(unittest.TestCase):
    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-4000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])

    def test_revision_follows_the_verification_status(self):
        text = (ROOT / "migrations" / "versions" / "0026_change_production_origin.py").read_text(encoding="utf-8")
        self.assertIn('revision = "0026_change_production_origin"', text)
        self.assertIn('down_revision = "0025_verification_status"', text)
        self.assertIn(f'OLD_ORIGIN = "{OLD}"', text)
        self.assertIn(f'NEW_ORIGIN = "{NEW}"', text)
        self.assertLessEqual(len("0026_change_production_origin"), 32)  # alembic_version.version_num
        # History stays: 0021 still targets the first production origin.
        history = (ROOT / "migrations" / "versions" / "0021_default_production_origin.py").read_text(encoding="utf-8")
        self.assertIn(f'NEW_ORIGIN = "{OLD}"', history)
        self.assertNotIn(NEW, history)

    def test_sqlite_cases_a_to_d_and_g(self):
        self.assert_ok(run(MIGRATION))

    def test_postgresql_cases_a_to_d_and_g(self):
        url = postgresql_url()
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        self.assert_ok(run(MIGRATION, database_url=url))

    def test_case_e_a_new_installation_stores_the_new_origin(self):
        self.assert_ok(run(CHAIN.replace("__START__", repr("base"))))

    def test_case_f_an_installation_at_0024_reaches_the_new_origin(self):
        self.assert_ok(run(CHAIN.replace("__START__", repr("0024_operations"))))

    def test_an_installation_on_the_localhost_defaults_goes_through_0021_then_0026(self):
        self.assert_ok(run(CHAIN.replace("__START__", repr("0020_manual_payment_statuses"))))

    def test_postgresql_case_f(self):
        url = postgresql_url()
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        self.assert_ok(run(CHAIN.replace("__START__", repr("0024_operations")), database_url=url))

    def test_callbacks_redirects_and_the_same_origin_check_follow_the_new_origin(self):
        self.assert_ok(run(APP))


if __name__ == "__main__":
    unittest.main()
