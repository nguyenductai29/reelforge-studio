"""The production origin is the default: https://reelforge.mul-service.com with Secure cookies.

* a new installation stores the HTTPS origin and ``secure_cookies = true``;
* migration 0021 (historical) moved an installation still on the exact old localhost defaults to the first
  production origin, https://studio.imokome-cloud.com, and kept every value an admin chose (another domain,
  another port, a cookie choice); migration 0026 then moves that origin to the current one
  (tests/test_domain_migration.py);
* login from the production origin passes the same-origin check; foreign origins, the old production origin
  included, are refused;
* the session cookie is Secure, HttpOnly and SameSite=Strict with the production default;
* a development machine overrides the origin in its own instance/bootstrap.json, never in the database;
* deployment: the frontend and the API listen on 127.0.0.1 only, and deploy.sh refuses a development override.

Offline: every program runs in a disposable copy of the app on SQLite (PostgreSQL for the
migration when REELFORGE_TEST_DATABASE_URL names an isolated test database).
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = "https://reelforge.mul-service.com"
DEVELOPMENT = {"frontend_origin": "http://localhost:3000", "secure_cookies": False}


def run(program: str, database_url: str | None = None, bootstrap: dict | None = None,
        legacy: dict | None = None) -> subprocess.CompletedProcess:
    """``program`` in a disposable copy of the app; ``bootstrap`` adds keys to instance/bootstrap.json."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("app", "migrations"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        url = database_url or f"sqlite:///{target}/instance/test.db"
        if legacy is not None:  # an installation older than bootstrap.json
            (target / "instance" / "config.json").write_text(json.dumps({"database_url": url, **legacy}))
        else:
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": url, **(bootstrap or {})}))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("GOOGLE_", "TIKTOK_", "FACEBOOK_", "REELFORGE_ENV_FILE"))}
        return subprocess.run([sys.executable, "-c", program], cwd=target,
                              env={**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"},
                              capture_output=True, text=True, encoding="utf-8", timeout=300)


def postgresql_url() -> str:
    url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
        return ""
    return "postgresql+psycopg://" + url[len("postgresql://"):] if url.startswith("postgresql://") else url


MIGRATION = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
config = Config("alembic.ini")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
BEFORE, MIGRATION = "0020_manual_payment_statuses", "0021_default_production_origin"
command.upgrade(config, BEFORE)
# 0021 is history: its target is the first production origin (0026 moves it to the current one).
OLD, NEW = "http://localhost:3000", "https://studio.imokome-cloud.com"
OTHERS = {"storage_dir": "instance/media", "trial_project_limit": 2, "registration_enabled": True}

def store(values):
    with engine.begin() as c:
        c.execute(text("DELETE FROM system_settings"))
        for key, value in {**OTHERS, **values}.items():
            c.execute(text("INSERT INTO system_settings (key, value) VALUES (:k, :v)"), {"k": key, "v": json.dumps(value)})

def stored():
    with engine.connect() as c:
        return {key: json.loads(value) for key, value in c.execute(text("SELECT key, value FROM system_settings")).all()}

KEPT = None
CASES = [
    # The untouched old defaults: both move.
    ({"frontend_origin": OLD, "secure_cookies": False}, {"frontend_origin": NEW, "secure_cookies": True}),
    ({"frontend_origin": OLD, "secure_cookies": True}, {"frontend_origin": NEW, "secure_cookies": True}),
    ({"frontend_origin": OLD}, {"frontend_origin": NEW}),  # no cookie row: none is invented
    # Anything an admin chose stays, cookie choice included.
    ({"frontend_origin": "https://reels.example.org", "secure_cookies": False}, KEPT),
    ({"frontend_origin": "https://reels.example.org", "secure_cookies": True}, KEPT),
    ({"frontend_origin": "http://192.168.3.100:3000", "secure_cookies": False}, KEPT),
    ({"frontend_origin": "http://localhost:3001", "secure_cookies": False}, KEPT),
    ({"frontend_origin": "http://127.0.0.1:3000", "secure_cookies": False}, KEPT),
    ({"frontend_origin": "https://localhost:3000", "secure_cookies": False}, KEPT),
    ({"frontend_origin": OLD + "/", "secure_cookies": False}, KEPT),  # not the exact old default
    ({"frontend_origin": NEW, "secure_cookies": False}, KEPT),  # production with cookies deliberately off
    ({"frontend_origin": NEW, "secure_cookies": True}, KEPT),
    ({"secure_cookies": False}, KEPT),  # no origin row
    ({}, KEPT),  # a new installation: the API stores the new defaults when it first starts
]
for before, after in CASES:
    store(before)
    command.upgrade(config, MIGRATION)
    expected = {**OTHERS, **(before if after is KEPT else after)}
    assert stored() == expected, (before, stored())
    # Downgrading 0021 changes nothing: the old code reads these values as they are.
    command.downgrade(config, BEFORE)
    assert stored() == expected, ("downgrade", before, stored())
command.upgrade(config, "head")
print("ok")
'''


class MigrationTest(unittest.TestCase):
    def test_sqlite_0021_replaced_only_the_exact_old_defaults(self):
        result = run(MIGRATION)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_postgresql_0021_replaced_only_the_exact_old_defaults(self):
        url = postgresql_url()
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        result = run(MIGRATION, database_url=url)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_revision_follows_0020(self):
        text = (ROOT / "migrations" / "versions" / "0021_default_production_origin.py").read_text(encoding="utf-8")
        self.assertIn('revision = "0021_default_production_origin"', text)
        self.assertIn('down_revision = "0020_manual_payment_statuses"', text)


COOKIES = r'''
def session_cookie(response):
    """The rf_session Set-Cookie header, as {attribute: value}; a flag maps to True."""
    headers = [value for key, value in response.headers.multi_items()
               if key.lower() == "set-cookie" and value.startswith("rf_session=")]
    assert len(headers) == 1, response.headers
    attributes = {}
    for part in headers[0].split(";")[1:]:
        name, _, value = part.strip().partition("=")
        attributes[name.lower()] = value or True
    return attributes

def no_cookie(response):
    assert not any(key.lower() == "set-cookie" for key, _ in response.headers.multi_items()), response.headers
'''

PRODUCTION_APP = r'''
import json
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
config = Config("alembic.ini")
PROD = "https://reelforge.mul-service.com"
if UPGRADE:
    # An installation from before 0021, still on the old defaults the API stored when it first started.
    command.upgrade(config, "0020_manual_payment_statuses")
    engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
    with engine.begin() as c:
        for key, value in {"frontend_origin": "http://localhost:3000", "secure_cookies": False,
                           "storage_dir": "instance/media", "trial_project_limit": 2, "registration_enabled": True}.items():
            c.execute(text("INSERT INTO system_settings (key, value) VALUES (:k, :v)"), {"k": key, "v": json.dumps(value)})
command.upgrade(config, "head")
import app.main as main
from app import system_config
from app.db import Session
system_config.activate()

with Session() as db:
    assert main.setting(db, "frontend_origin") == PROD, main.setting(db, "frontend_origin")
    assert main.setting(db, "secure_cookies") is True
assert main.LOCAL == {}
assert system_config.frontend_origin() == PROD
assert system_config.redirect_uri("youtube") == PROD + "/youtube/callback"

# The public origin is HTTPS, so the browser (here: a client on https://) sends the Secure cookie back.
client = TestClient(main.app, base_url="https://testserver")
OWNER = {"email": "owner@example.com", "password": "long-password-123"}
response = client.post("/api/setup", json=OWNER, headers={"Origin": PROD})
assert response.status_code == 200, response.text
cookie = session_cookie(response)
assert cookie.get("secure") is True and cookie.get("httponly") is True, cookie
assert cookie.get("samesite") == "strict" and cookie.get("path") == "/" and cookie.get("max-age") == "604800", cookie
assert client.get("/api/dashboard").status_code == 200

# POST /api/login from the production origin: same-origin check passed, Secure + HttpOnly + SameSite=Strict.
client.cookies.clear()
response = client.post("/api/login", json=OWNER, headers={"Origin": PROD})
assert response.status_code == 200, response.text
cookie = session_cookie(response)
assert cookie.get("secure") is True and cookie.get("httponly") is True and cookie.get("samesite") == "strict", cookie
assert client.get("/api/dashboard").status_code == 200

# Foreign origins are refused before the credentials are looked at, and get no cookie.
stranger = TestClient(main.app, base_url="https://testserver")
# The first production origin (before migration 0026) is refused like any other.
for origin in ("https://evil.example.com", "https://studio.imokome-cloud.com", "http://reelforge.mul-service.com",
               "https://reelforge.mul-service.com.evil.example.com", "https://evil.reelforge.mul-service.com",
               "http://localhost:3000", "null"):
    response = stranger.post("/api/login", json=OWNER, headers={"Origin": origin})
    assert response.status_code == 403 and response.json()["detail"] == "Invalid origin", (origin, response.text)
    assert response.json()["code"] == "invalid_origin" and response.json()["request_id"], response.text
    no_cookie(response)
    assert client.post("/api/logout", headers={"Origin": origin}).status_code == 403
assert stranger.get("/api/dashboard").status_code == 401

# Admin -> System Settings -> General shows the stored values, and both stay editable.
system = client.get("/api/settings").json()["system"]
assert system["frontend_origin"] == PROD and system["secure_cookies"] is True and system["local_override"] is None, system
body = {"frontend_origin": "https://reels.example.org/", "secure_cookies": True, "trial_project_limit": 2,
        "registration_enabled": True}
assert client.put("/api/settings/system", json=body, headers={"Origin": "https://evil.example.com"}).status_code == 403
response = client.put("/api/settings/system", json=body, headers={"Origin": PROD})
assert response.status_code == 200, response.text
assert response.json()["system"]["frontend_origin"] == "https://reels.example.org"
assert client.post("/api/login", json=OWNER, headers={"Origin": PROD}).status_code == 403
assert client.post("/api/login", json=OWNER, headers={"Origin": "https://reels.example.org"}).status_code == 200
NEW = {"Origin": "https://reels.example.org"}
assert client.put("/api/settings/system", json={**body, "frontend_origin": "http://reels.example.org"}, headers=NEW).status_code == 400
assert client.put("/api/settings/system", json={**body, "secure_cookies": False}, headers=NEW).status_code == 200
cookie = session_cookie(client.post("/api/login", json=OWNER, headers=NEW))
assert "secure" not in cookie and cookie.get("httponly") is True and cookie.get("samesite") == "strict", cookie
response = client.put("/api/settings/system", json={**body, "frontend_origin": PROD}, headers=NEW)
assert response.status_code == 200 and response.json()["system"] == {
    "frontend_origin": PROD, "secure_cookies": True, "storage_dir": "instance/media", "trial_project_limit": 2,
    "registration_enabled": True, "local_override": None}, response.text

# Logging out clears the cookie with the attributes it was set with.
cookie = session_cookie(client.post("/api/logout", headers={"Origin": PROD}))
assert cookie.get("secure") is True and cookie.get("httponly") is True and cookie.get("samesite") == "strict", cookie
assert cookie.get("max-age") == "0", cookie
print("ok")
'''

DEVELOPMENT_APP = r'''
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
import app.main as main
from app import system_config
from app.db import Session
system_config.activate()
PROD, DEV = "https://reelforge.mul-service.com", "http://localhost:3000"

# The override applies to this machine; the database keeps the public defaults for the others.
assert main.LOCAL == {"frontend_origin": DEV, "secure_cookies": False}, main.LOCAL
with Session() as db:
    assert main.setting(db, "frontend_origin") == PROD and main.setting(db, "secure_cookies") is True
assert system_config.frontend_origin() == DEV
assert system_config.redirect_uri("youtube") == DEV + "/youtube/callback"

# next dev on http://localhost:3000: plain HTTP, so the cookie must not be Secure. HttpOnly and SameSite stay.
client = TestClient(main.app)
OWNER = {"email": "owner@example.com", "password": "long-password-123"}
response = client.post("/api/setup", json=OWNER, headers={"Origin": DEV})
assert response.status_code == 200, response.text
cookie = session_cookie(response)
assert "secure" not in cookie and cookie.get("httponly") is True and cookie.get("samesite") == "strict", cookie
assert client.get("/api/dashboard").status_code == 200
client.cookies.clear()
assert client.post("/api/login", json=OWNER, headers={"Origin": DEV}).status_code == 200
assert client.get("/api/dashboard").status_code == 200
for origin in ("https://evil.example.com", PROD):
    response = client.post("/api/login", json=OWNER, headers={"Origin": origin})
    assert response.status_code == 403, (origin, response.text)

system = client.get("/api/settings").json()["system"]
assert system["frontend_origin"] == PROD and system["secure_cookies"] is True, system
assert system["local_override"] == {"frontend_origin": DEV, "secure_cookies": False}, system
# Saving the form stores what the admin typed (for production), never the override.
body = {"frontend_origin": PROD, "secure_cookies": True, "trial_project_limit": 3, "registration_enabled": True}
response = client.put("/api/settings/system", json=body, headers={"Origin": DEV})
assert response.status_code == 200, response.text
with Session() as db:
    assert main.setting(db, "frontend_origin") == PROD and main.setting(db, "secure_cookies") is True
print("ok")
'''

LEGACY_APP = r'''
from alembic import command
from alembic.config import Config
command.upgrade(Config("alembic.ini"), "head")
import app.main as main
from app.db import Session
# instance/config.json keeps its old meaning: its values are copied into System Settings once.
assert main.LOCAL == {}
with Session() as db:
    assert main.setting(db, "frontend_origin") == "https://legacy.example.org"
    assert main.setting(db, "secure_cookies") is True
print("ok")
'''

LOCAL_SETTINGS = r'''
from app import db
from app.db import local_settings
db.source_file = db.CONFIG_FILE
cases = [
    ({}, {}),
    ({"database_url": "sqlite://"}, {}),
    ({"frontend_origin": "http://localhost:3000"}, {"frontend_origin": "http://localhost:3000", "secure_cookies": False}),
    ({"frontend_origin": "http://localhost:3000/", "secure_cookies": False},
     {"frontend_origin": "http://localhost:3000", "secure_cookies": False}),
    ({"frontend_origin": "https://dev.example.org"}, {"frontend_origin": "https://dev.example.org", "secure_cookies": True}),
    ({"frontend_origin": "https://dev.example.org", "secure_cookies": False},
     {"frontend_origin": "https://dev.example.org", "secure_cookies": False}),
]
for config, expected in cases:
    db.config = config
    assert local_settings() == expected, (config, local_settings())
for config, message in (
        ({"frontend_origin": "http://localhost:3000", "secure_cookies": True}, "require an https"),
        ({"frontend_origin": "http://localhost:3000/app"}, "scheme and host only"),
        ({"frontend_origin": "localhost:3000"}, "scheme and host only"),
        ({"frontend_origin": 3000}, "scheme and host only"),
        ({"secure_cookies": False}, "scheme and host only"),
        ({"frontend_origin": "http://localhost:3000", "secure_cookies": "false"}, "true or false")):
    db.config = config
    try:
        local_settings()
    except RuntimeError as exc:
        assert message in str(exc), (config, exc)
    else:
        raise AssertionError(config)
# Only instance/bootstrap.json overrides; the legacy config.json does not.
db.source_file = db.LEGACY_CONFIG_FILE
db.config = {"frontend_origin": "http://localhost:3000"}
assert local_settings() == {}
print("ok")
'''

INVALID_APP = r'''
from alembic import command
from alembic.config import Config
command.upgrade(Config("alembic.ini"), "head")
try:
    import app.main
except RuntimeError as exc:
    print(exc)
else:
    raise AssertionError("an invalid override must stop the API")
'''


class LoginOriginTest(unittest.TestCase):
    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout)

    def test_new_installation_defaults_to_the_https_origin_with_secure_cookies(self):
        self.assert_ok(run(COOKIES + "UPGRADE = False\n" + PRODUCTION_APP))

    def test_an_installation_on_the_old_defaults_logs_in_from_the_production_origin_after_upgrading(self):
        self.assert_ok(run(COOKIES + "UPGRADE = True\n" + PRODUCTION_APP))

    def test_a_development_machine_overrides_the_origin_in_its_bootstrap_only(self):
        self.assert_ok(run(COOKIES + DEVELOPMENT_APP, bootstrap=DEVELOPMENT))

    def test_the_legacy_config_file_is_still_copied_once(self):
        self.assert_ok(run(LEGACY_APP, legacy={"frontend_origin": "https://legacy.example.org", "secure_cookies": True}))

    def test_the_override_is_validated_like_the_admin_form(self):
        self.assert_ok(run(LOCAL_SETTINGS))
        result = run(INVALID_APP, bootstrap={"frontend_origin": "http://localhost:3000", "secure_cookies": True})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
        self.assertIn("secure_cookies require an https frontend_origin", result.stdout)


class DeploymentTest(unittest.TestCase):
    def test_the_frontend_and_the_api_listen_on_loopback_only(self):
        units = ROOT / "deploy" / "systemd"
        frontend = (units / "reelforge-frontend.service").read_text(encoding="utf-8")
        self.assertIn("Environment=PORT=3001", frontend)
        self.assertRegex(frontend, r"(?m)^ExecStart=/usr/bin/npm start -- --hostname 127\.0\.0\.1$")
        api = (units / "reelforge-api.service").read_text(encoding="utf-8")
        self.assertIn("--host 127.0.0.1 --port 8000", api)

    def test_deploy_refuses_a_development_override_before_migrating(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        check = re.search(r"python -c '(import json, sys\n.*?)'; then", script, re.S)
        self.assertIsNotNone(check)
        self.assertLess(script.index(check.group(0)), script.index("alembic upgrade head"))
        self.assertLess(script.index(check.group(0)), script.index("systemctl restart reelforge-api"))
        for bootstrap, refused in (({"database_url": "postgresql://x"}, False),
                                   ({"database_url": "postgresql://x", **DEVELOPMENT}, True),
                                   ({"database_url": "postgresql://x", "secure_cookies": True}, True)):
            with self.subTest(bootstrap=bootstrap), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "instance").mkdir()
                (Path(directory) / "instance" / "bootstrap.json").write_text(json.dumps(bootstrap))
                result = subprocess.run([sys.executable, "-c", check.group(1)], cwd=directory, capture_output=True)
                self.assertEqual(result.returncode == 0, refused, result.stderr)

    def test_the_docs_name_the_public_origin_and_the_private_services(self):
        for name in ("README.md", "docs/PRODUCTION_BOOTSTRAP.md", "docs/home-server-deployment.md",
                     "docs/SYSTEM_CONFIGURATION.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(doc=name):
                self.assertIn(PRODUCTION, text)
                self.assertIn("127.0.0.1:8000", text)
                self.assertIn("127.0.0.1:3001", text)


if __name__ == "__main__":
    unittest.main()
