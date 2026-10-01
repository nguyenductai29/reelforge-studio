"""Phase 21: configuration hardening and deployment readiness.

* every environment variable the backend reads is classified (bootstrap, legacy, experimental, dev);
* a fresh installation runs with no .env.runtime: database URL + master key file only;
* a separate worker process picks up a changed provider key and payment mode without a restart;
* the master key: init, missing, permissions, no overwrite, wrong key, never logged;
* migration 0020 gives manual VietQR orders explicit statuses;
* systemd units and deploy.sh need no provider secrets.

Offline: no provider, gateway or bank is contacted.
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
from unittest.mock import patch

from cryptography.fernet import Fernet

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

from app import logs, master_key, secret_box, system_config

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
# Modules allowed to read os.environ directly; everything else goes through app.system_config.
DIRECT_ENV_MODULES = {
    "system_config.py": "the resolver itself (legacy fallback)",
    "master_key.py": "bootstrap: the key file path and the legacy key",
    "db.py": "bootstrap: the database URL",
    "logs.py": "bootstrap: log format/level, and scrubbing secret values",
    "runtime_env.py": "the legacy runtime file loader",
    "payment_config.py": "legacy OnePAY fallback",
    "payment_providers/onepay.py": "legacy OnePAY fallback",
    "providers/dola.py": "experimental Dola gateway",
    "providers/catalog.py": "experimental Dola gateway",
    "provider_check.py": "dev tool: smoke-test choices",
    "smoke_test.py": "dev tool: the live-test opt-in",
}
ENV_LITERAL = re.compile(r'"((?:REELFORGE|OPENAI|ANTHROPIC|GEMINI|RUNWAY|RUNWAYML|FAL|RUNWARE|REPLICATE|DOLA|GOOGLE|TIKTOK|'
                         r'FACEBOOK|ONEPAY|RENDER|VIDEO|IMAGE|VOICE|TEXT|TRANSCRIPTION|WORKSPACE|CREDITS)_[A-Z0-9_]+)"')


def sources():
    for path in APP.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path.relative_to(APP).as_posix(), path.read_text(encoding="utf-8")


class EnvironmentAuditTest(unittest.TestCase):
    def test_every_variable_the_backend_names_is_classified(self):
        known = set(system_config.ENVIRONMENT) | set(system_config.BY_ENV)
        unknown = {}
        for name, text in sources():
            for variable in ENV_LITERAL.findall(text):
                # Module constants such as TEXT_PROVIDERS in __all__ are not variables.
                if variable not in known and not variable.endswith(("_", "_PROVIDERS", "_TASK", "_HANDLERS")):
                    unknown.setdefault(variable, name)
        self.assertEqual(unknown, {}, "classify these in system_config.ENVIRONMENT or the registry")

    def test_only_bootstrap_legacy_and_tool_modules_read_the_environment_directly(self):
        readers = {name for name, text in sources() if re.search(r"os\.environ|os\.getenv", text)}
        self.assertEqual(readers - set(DIRECT_ENV_MODULES), set())
        catalog = (APP / "providers" / "catalog.py").read_text(encoding="utf-8")
        for variable in re.findall(r'os\.environ\.get\("([A-Z_]+)"', catalog):
            self.assertTrue(variable.startswith("DOLA_"), variable)

    def test_categories_are_the_documented_ones(self):
        self.assertEqual({category for category, _ in system_config.ENVIRONMENT.values()},
                         {"bootstrap", "legacy", "experimental", "dev"})
        bootstrap = sorted(name for name, (category, _) in system_config.ENVIRONMENT.items() if category == "bootstrap")
        self.assertEqual(bootstrap, ["REELFORGE_DATABASE_URL", "REELFORGE_LOG_FORMAT", "REELFORGE_LOG_LEVEL",
                                     "REELFORGE_MASTER_KEY_FILE"])
        documented = (ROOT / "docs" / "SYSTEM_CONFIGURATION.md").read_text(encoding="utf-8")
        for name in system_config.ENVIRONMENT:
            if not name.startswith(("REELFORGE_SMOKE_", "ONEPAY_", "DOLA_")):
                with self.subTest(variable=name):
                    self.assertIn(name, documented)

    def test_no_runtime_file_is_needed(self):
        from app import runtime_env

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runtime_env, "DEFAULT_FILE", Path(directory) / ".env.runtime"), \
                patch.dict(os.environ, {"REELFORGE_ENV_FILE": ""}):
            self.assertEqual(runtime_env.load_runtime_env(), (None, []))


class MasterKeyHardeningTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        patcher = patch.multiple(master_key, PRODUCTION_FILE=Path(self.directory) / "none.key",
                                 DEVELOPMENT_FILE=Path(self.directory) / "dev.key")
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in (master_key.FILE_ENV, master_key.LEGACY_ENV):
            os.environ.pop(name, None)

    def test_init_creates_the_directory_refuses_overwrite_and_never_prints_the_key(self):
        target = Path(self.directory) / "etc" / "reelforge" / "master.key"
        printed = []
        with patch.object(master_key, "encrypted_data_exists", lambda: False):
            self.assertEqual(master_key.init(target, out=printed.append), 0)
        key = target.read_text().strip()
        Fernet(key.encode())
        if os.name == "posix":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        os.environ[master_key.FILE_ENV] = str(target)
        # A second run changes nothing, and an unrelated existing file is never overwritten.
        self.assertEqual(master_key.init(target, out=printed.append), 0)
        self.assertEqual(target.read_text().strip(), key)
        other = Path(self.directory) / "other.key"
        other.write_text("not a key")
        os.environ[master_key.FILE_ENV] = str(other)
        self.assertEqual(master_key.init(other, out=printed.append), 1)
        self.assertEqual(other.read_text(), "not a key")
        self.assertNotIn(key, "\n".join(printed))

    def test_missing_key_fails_safely_with_guidance(self):
        os.environ[master_key.FILE_ENV] = str(Path(self.directory) / "absent.key")
        lines = []
        self.assertEqual(master_key.print_status(out=lines.append), 1)
        self.assertIn("problem: missing", lines)
        self.assertTrue(any(line.startswith("error: no usable master key") for line in lines))
        with self.assertRaises(secret_box.SecretBoxError) as caught:
            secret_box.encrypt_json("payment-config:payos", {"api_key": "x"})
        self.assertEqual(caught.exception.code, "key_missing")

    def test_readable_by_others_is_a_warning(self):
        target = Path(self.directory) / "master.key"
        target.write_text(Fernet.generate_key().decode())
        os.environ[master_key.FILE_ENV] = str(target)
        with patch.object(master_key, "_mode", lambda path: 0o644):
            self.assertIs(master_key.status()["permissions_ok"], False)
            lines = []
            self.assertEqual(master_key.print_status(out=lines.append), 0)
            self.assertTrue(any("chmod 600" in line for line in lines))
        with patch.object(master_key, "_mode", lambda path: 0o600):
            self.assertIs(master_key.status()["permissions_ok"], True)

    def test_data_encrypted_with_one_key_does_not_decrypt_with_another(self):
        first, second = Path(self.directory) / "a.key", Path(self.directory) / "b.key"
        first.write_text(Fernet.generate_key().decode())
        second.write_text(Fernet.generate_key().decode())
        os.environ[master_key.FILE_ENV] = str(first)
        sealed = secret_box.encrypt_json("system-config:ai.openai.api_key", {"value": "sk-test"})
        self.assertEqual(secret_box.decrypt_json("system-config:ai.openai.api_key", sealed), {"value": "sk-test"})
        os.environ[master_key.FILE_ENV] = str(second)
        with self.assertRaises(secret_box.SecretBoxError) as caught:
            secret_box.decrypt_json("system-config:ai.openai.api_key", sealed)
        self.assertEqual(caught.exception.code, "cannot_decrypt")

    def test_a_replaced_file_is_read_again_and_the_key_is_scrubbed_from_logs(self):
        target = Path(self.directory) / "master.key"
        first = Fernet.generate_key().decode()
        target.write_text(first)
        os.environ[master_key.FILE_ENV] = str(target)
        self.assertEqual(master_key.load(), first)
        self.assertEqual(logs.scrub(f"oops {first} leaked"), f"oops {logs.REDACTED} leaked")
        second = Fernet.generate_key().decode()
        target.write_text(second + "\n\n")  # another size: a changed file is never served from the cache
        self.assertEqual(master_key.load(), second)


class DeploymentFilesTest(unittest.TestCase):
    def test_units_need_no_provider_secrets_and_the_runtime_file_is_optional(self):
        units = sorted((ROOT / "deploy" / "systemd").glob("*.service"))
        self.assertGreaterEqual(len(units), 4)
        secret_names = set(system_config.BY_ENV) | {"REELFORGE_TOKEN_ENCRYPTION_KEY"}
        for unit in units:
            text = unit.read_text(encoding="utf-8")
            with self.subTest(unit=unit.name):
                self.assertNotIn("EnvironmentFile=/", text)  # only the optional "-" form
                for name in secret_names:
                    self.assertNotRegex(text, rf"(?m)^Environment={name}=")
        worker = (ROOT / "deploy" / "systemd" / "reelforge-worker@.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.%i_worker", worker)

    def test_deploy_checks_the_master_key_before_restarting(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertLess(script.index("bash deploy/ensure-master-key.sh"), script.index("systemctl restart reelforge-api"))
        self.assertLess(script.index("bash deploy/ensure-master-key.sh"), script.index("alembic upgrade head"))
        self.assertIn('"reelforge-worker@${worker}"', script)
        self.assertNotIn("runtime.env", script.split("set -euo pipefail", 1)[1].replace("/etc/reelforge/runtime.env is", ""))


class EncryptedReportTest(unittest.TestCase):
    def test_exit_codes_say_none_yes_or_unknown(self):
        for found, code, text in ((False, 0, "encrypted data: none"), (True, 1, "encrypted data: yes")):
            lines = []
            with patch.object(master_key, "encrypted_data_exists", lambda found=found: found):
                self.assertEqual(master_key.report_encrypted(out=lines.append), code)
            self.assertEqual(lines, [text])

        def unreachable():
            raise OSError("database down")
        lines = []
        with patch.object(master_key, "encrypted_data_exists", unreachable):
            self.assertEqual(master_key.report_encrypted(out=lines.append), 2)
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
                os.environ.pop(master_key.LEGACY_ENV, None)
                os.environ[master_key.FILE_ENV] = str(Path(directory) / "absent.key")
                self.assertEqual(master_key.init(Path(directory) / "master.key", out=lines.append), 2)
                self.assertFalse((Path(directory) / "master.key").exists())


BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is needed to run deploy/ensure-master-key.sh")
class EnsureMasterKeyScriptTest(unittest.TestCase):
    """deploy/ensure-master-key.sh on a disposable copy of the app (SQLite), never the real server paths."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)
        for folder in ("app", "migrations", "deploy"):
            shutil.copytree(ROOT / folder, self.directory / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", self.directory / "alembic.ini")
        (self.directory / "instance").mkdir()
        self.database = self.directory / "instance" / "test.db"
        (self.directory / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{self.database}"}))
        self.key = self.directory / "etc" / "reelforge" / "master.key"
        self.legacy_file = self.directory / "etc" / "reelforge" / "runtime.env"
        env = self.env()
        migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=self.directory, env=env,
                                  capture_output=True, text=True)
        self.assertEqual(migrated.returncode, 0, migrated.stderr[-3000:])

    def env(self):
        env = {name: value for name, value in os.environ.items()
               if name not in (master_key.LEGACY_ENV, master_key.FILE_ENV, "REELFORGE_ENV_FILE")}
        return {**env, "PYTHONPATH": str(self.directory), "PYTHON": sys.executable.replace("\\", "/"),
                "REELFORGE_MASTER_KEY_FILE": self.key.as_posix(), "REELFORGE_LEGACY_ENV_FILE": self.legacy_file.as_posix()}

    def run_script(self):
        return subprocess.run([BASH, "deploy/ensure-master-key.sh"], cwd=self.directory,
                              env={**self.env(), "PYTHONIOENCODING": "utf-8"}, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def store_encrypted_row(self):
        import sqlite3

        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO system_config (key, ciphertext, updated_at) "
                               "VALUES ('ai.openai.api_key', 'gAAAA-ciphertext', '2026-10-01')")

    def test_a_new_installation_gets_a_key_and_a_second_run_changes_nothing(self):
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        key = self.key.read_text().strip()
        Fernet(key.encode())
        self.assertIn("encrypted data: none", first.stdout)
        self.assertIn("source: file", first.stdout)
        self.assertNotIn(key, first.stdout + first.stderr)
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.key.read_text().strip(), key)
        self.assertNotIn("creating the master key", second.stdout)

    def test_existing_encrypted_data_stops_the_deploy(self):
        self.store_encrypted_row()
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("encrypted data: yes", result.stdout)
        self.assertIn("STOP", result.stderr)
        self.assertIn("Restore the original key file", result.stderr)
        self.assertFalse(self.key.exists())

    def test_a_legacy_key_in_the_runtime_file_is_moved_into_the_file_never_replaced(self):
        # Encrypted data exists, and the key still lives in the legacy runtime file the services load:
        # the script copies that same key into the key file instead of stopping or generating another.
        self.store_encrypted_row()
        legacy = Fernet.generate_key().decode()
        self.legacy_file.parent.mkdir(parents=True)
        self.legacy_file.write_text(f"REELFORGE_TOKEN_ENCRYPTION_KEY={legacy}\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("moving it into", result.stdout)
        self.assertEqual(self.key.read_text().strip(), legacy)
        self.assertIn("source: file", result.stdout)
        self.assertNotIn(legacy, result.stdout + result.stderr)

    def test_with_the_default_path_a_legacy_key_simply_keeps_working(self):
        legacy = Fernet.generate_key().decode()
        self.legacy_file.parent.mkdir(parents=True)
        self.legacy_file.write_text(f"REELFORGE_TOKEN_ENCRYPTION_KEY={legacy}\n")
        env = {**self.env(), "PYTHONIOENCODING": "utf-8"}
        env.pop("REELFORGE_MASTER_KEY_FILE")  # the production default: /etc/reelforge/master.key, absent here
        result = subprocess.run([BASH, "deploy/ensure-master-key.sh"], cwd=self.directory, env=env,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source: legacy_env", result.stdout)
        self.assertNotIn("creating the master key", result.stdout)

    def test_an_invalid_key_file_is_never_overwritten(self):
        self.key.parent.mkdir(parents=True)
        self.key.write_text("not a key")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("never overwritten", result.stderr)
        self.assertEqual(self.key.read_text(), "not a key")


MIGRATION = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
config = Config("alembic.ini")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
command.upgrade(config, "0019_system_configuration")
NOW = "2026-10-01 00:00:00"
with engine.begin() as c:
    c.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) VALUES ('u1', 'a@b.c', 'x', true, true)"))
    c.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) VALUES ('w1', 'S', 'u1', 'trial', :n)"), {"n": NOW})
    rows = (("reported", "bank_qr", "pending", NOW), ("fresh", "bank_qr", "pending", None),
            ("rejected", "bank_qr", "failed", NOW), ("payos", "payos", "pending", None), ("card", "onepay", "failed", None))
    for index, (order_id, provider, status, reported) in enumerate(rows):
        c.execute(text("INSERT INTO payment_orders (id, workspace_id, plan_code, provider, order_code, amount_vnd, "
                       "credits_award, status, created_at, transfer_reported_at) VALUES "
                       "(:id, 'w1', 'standard', :p, :code, 1000, 1, :s, :n, :r)"),
                  {"id": order_id, "p": provider, "code": 1000000000000 + index, "s": status, "n": NOW, "r": reported})
    c.execute(text("INSERT INTO payment_order_events (order_id, action, user_id, created_at) "
                   "VALUES ('rejected', 'rejected', 'u1', :n)"), {"n": NOW})

def statuses():
    with engine.connect() as c:
        return dict(c.execute(text("SELECT id, status FROM payment_orders")).all())

before = statuses()
command.upgrade(config, "head")
assert statuses() == {**before, "reported": "awaiting_confirmation", "rejected": "rejected"}, statuses()
command.downgrade(config, "0019_system_configuration")
assert statuses() == before, statuses()
command.upgrade(config, "head")
print("ok")
'''


def run_migration(database_url: str, directory: str):
    target = Path(directory)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "instance").mkdir()
    (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": database_url}))
    return subprocess.run([sys.executable, "-c", MIGRATION], cwd=target, env={**os.environ, "PYTHONPATH": str(target)},
                          capture_output=True, text=True)


class ManualStatusMigrationTest(unittest.TestCase):
    def test_sqlite_0019_to_0020_gives_manual_orders_explicit_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_postgresql_0019_to_0020_gives_manual_orders_explicit_statuses(self):
        url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
        if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(url, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])


FRESH = r'''
# A new server: no runtime file, no provider or OAuth variable, the master key in a file.
import tempfile
from app import master_key, runtime_env
from app.providers.text import create_text_provider
from app.publishers import google_oauth
legacy_key = os.environ.pop("REELFORGE_TOKEN_ENCRYPTION_KEY")
for name in list(os.environ):
    if name in system_config.BY_ENV:
        os.environ.pop(name)
key_file = Path(tempfile.mkdtemp()) / "etc" / "master.key"
os.environ["REELFORGE_MASTER_KEY_FILE"] = str(key_file)
assert master_key.init(key_file, out=lambda line: None) == 0  # copies nothing: no legacy key is set any more
system_config.invalidate()
assert runtime_env.LOADED["path"] is None
state = client.get("/api/admin/system-config").json()
assert state["master_key"]["source"] == "file" and state["legacy_in_use"] == [] and state["runtime_file"]["path"] is None
assert all(not row["set"] for row in state["environment"] if row["category"] == "legacy")

# Everything an operator needs, from the admin UI.
def put(section, **body):
    response = client.put(f"/api/admin/system-config/{section}", json=body)
    assert response.status_code == 200, response.text
root = Path(tempfile.mkdtemp())
put("ai", secrets={"ai.openai.api_key": {"action": "replace", "value": "sk-fresh-install"}})
put("social", values={"social.youtube.client_id": "fresh.apps.googleusercontent.com"},
    secrets={"social.youtube.client_secret": {"action": "replace", "value": "google-fresh"}})
put("storage", values={"storage.root": str(root)})
put("runtime", values={"runtime.ffmpeg_path": str((tools / "ffmpeg").resolve()),
                       "runtime.ffprobe_path": str((tools / "ffprobe").resolve())})
assert client.put("/api/admin/payment-config/bank_qr", json={
    "enabled": True, "bank_bin": "970436", "account_number": "0123456789", "account_name": "STUDIO",
    "transfer_prefix": "RF"}).status_code == 200
assert client.put("/api/admin/plans/standard", json={"name": "Standard", "project_limit": None, "workflow_limit": None,
                                                     "monthly_credits": 5, "is_active": True,
                                                     "price_vnd": 30000}).status_code == 200

assert create_text_provider("openai")._api_key == "sk-fresh-install"
assert google_oauth.GoogleOAuthConfig.from_environment().redirect_uri == "http://localhost:3000/youtube/callback"
with Session() as db:
    assert main.media_root(db) == root
checks = {s["key"]: {c["key"]: c for c in s["checks"]} for s in client.get("/api/admin/readiness").json()["sections"]}
assert checks["security"]["master_key"]["status"] == "ok" and checks["security"]["master_key"]["key_source"] == "file"
assert checks["configuration"]["legacy_settings"]["status"] == "ok"
assert checks["configuration"]["runtime_file"]["status"] == "ok"
assert (checks["ai"]["openai"]["status"], checks["ai"]["openai"]["source"]) == ("ok", "admin")
assert (checks["publishing"]["youtube"]["status"], checks["publishing"]["youtube"]["source"]) == ("ok", "admin")
assert checks["payments"]["vietqr"]["status"] == "ok" and checks["payments"]["vietqr"]["provider"] == "bank_qr"
assert checks["ffmpeg"]["ffmpeg"]["status"] == "ok"
paid = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert paid.status_code == 201 and paid.json()["transfer"]["content"].startswith("RF")
text_out = client.get("/api/admin/system-config").text + client.get("/api/admin/readiness").text
for secret in ("sk-fresh-install", "google-fresh", legacy_key, master_key.load()):
    assert secret not in text_out
print("fresh ok")
'''

HOT = r'''
# A worker process that started before the change sees the new key and payment mode without a restart.
import subprocess, sys
WORKER = """
import sys, time
from app import system_config
system_config.CACHE_SECONDS = 1
from app.runtime_env import start_process
start_process("hot_config_test")
from app import payment_config, payment_providers
from app.providers.text import create_text_provider
last, deadline = None, time.time() + 30
while time.time() < deadline:
    line = "|".join((create_text_provider("openai")._api_key, payment_providers.for_method("vietqr").name,
                     str(payment_config.get("onepay").enabled)))
    if line != last:
        print(line, flush=True)
        last = line
    if line == "KEY-TWO|bank_qr|False":
        break
    time.sleep(0.2)
"""
put = lambda section, **body: client.put(f"/api/admin/system-config/{section}", json=body)
assert put("ai", secrets={"ai.openai.api_key": {"action": "replace", "value": "KEY-ONE"}}).status_code == 200
worker = subprocess.Popen([sys.executable, "-c", WORKER], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=dict(os.environ))
first = worker.stdout.readline().strip()
assert first == "KEY-ONE|payos|True", (first, worker.stderr.read() if worker.poll() is not None else "")
# The admin changes the key, switches VietQR to manual and turns cards off; the worker is not restarted.
assert put("ai", secrets={"ai.openai.api_key": {"action": "replace", "value": "KEY-TWO"}}).status_code == 200
assert client.put("/api/admin/payment-config/bank_qr", json={
    "enabled": True, "bank_bin": "970436", "account_number": "0123456789", "account_name": "STUDIO",
    "transfer_prefix": "RF"}).status_code == 200
assert client.post("/api/admin/payment-config/onepay/disable").status_code == 200
output, errors = worker.communicate(timeout=40)
lines = [first] + output.split()
assert lines[-1] == "KEY-TWO|bank_qr|False", (lines, errors[-2000:])
assert worker.returncode == 0
print("hot ok")
'''


class Phase21Test(unittest.TestCase):
    def run_body(self, body, marker):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stdout[-3000:] + completed.stderr[-6000:])
        self.assertIn(marker, completed.stdout)

    def test_a_fresh_installation_needs_no_runtime_file(self):
        self.run_body(FRESH, "fresh ok")

    def test_a_running_worker_picks_up_new_settings_without_a_restart(self):
        self.run_body(HOT, "hot ok")


if __name__ == "__main__":
    unittest.main()
