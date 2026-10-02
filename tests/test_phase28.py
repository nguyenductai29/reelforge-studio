"""Phase 28: V1.0 production verification and release closure.

* migration 0025: a verification check has a status; verified checks become passed, and a downgrade keeps only
  what the older schema can say;
* Admin → Verification records passed / failed / not applicable / not checked with who and when, never by itself;
  "not applicable" only for an optional provider; the older {"verified": …} form still works; the summary is
  complete only when every gate is decided;
* the in-app gates cover the release checklist: every key has a label in vi, en and ja, and
  docs/RELEASE_V1_CHECKLIST.md maps its gates to real keys and names every key;
* app/release_check.py: ports, services, timers, health and the source judged from a fake system; on a real
  (disposable) database it reads the migration head, the key, the backups and the configuration, never prints the
  database password, the master key or a legacy value, and the report never calls an unrecorded gate passed;
  READY_FOR_TAG only without a FAIL, with CI not failing, and with every gate decided;
* tests/ci_annotate.py turns unittest failures into annotations readable without access to the CI log;
* deploy.sh runs its steps in the documented order.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

from app import readiness, release_check
from app.release_check import FAIL, MANUAL, PASS, WARN, Check

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import ci_annotate  # noqa: E402


MIGRATION = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
config = Config("alembic.ini")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
command.upgrade(config, "0024_operations")
with engine.begin() as c:
    c.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) VALUES ('u1', 'a@b.c', 'x', true, true)"))
    c.execute(text("INSERT INTO verification_checks (key, verified_at, verified_by_user_id, note, updated_at) VALUES "
                   "('ffmpeg_verified', '2026-10-01 10:00:00', 'u1', 'ok', '2026-10-01 10:00:00'), "
                   "('youtube_upload', NULL, NULL, 'later', '2026-10-01 10:00:00')"))
command.upgrade(config, "0025_verification_status")
with engine.begin() as c:
    rows = dict(c.execute(text("SELECT key, status FROM verification_checks")).all())
    assert rows == {"ffmpeg_verified": "passed", "youtube_upload": None}, rows
    c.execute(text("INSERT INTO verification_checks (key, status, verified_at, verified_by_user_id, updated_at) VALUES "
                   "('tiktok_upload', 'not_applicable', '2026-10-02 10:00:00', 'u1', '2026-10-02 10:00:00'), "
                   "('email_dns', 'failed', '2026-10-02 10:00:00', 'u1', '2026-10-02 10:00:00')"))
try:
    with engine.begin() as c:
        c.execute(text("INSERT INTO verification_checks (key, status, updated_at) VALUES ('x', 'maybe', '2026-10-02 10:00:00')"))
except IntegrityError:
    pass
else:
    raise AssertionError("an unknown status was stored")
command.downgrade(config, "0024_operations")
with engine.connect() as c:
    rows = {key: (at is not None, by) for key, at, by in
            c.execute(text("SELECT key, verified_at, verified_by_user_id FROM verification_checks"))}
    assert rows["ffmpeg_verified"] == (True, "u1"), rows
    assert rows["tiktok_upload"] == (False, None) and rows["email_dns"] == (False, None), rows
    assert "status" not in {column["name"] for column in inspect(c).get_columns("verification_checks")}
command.upgrade(config, "head")
with engine.connect() as c:
    assert dict(c.execute(text("SELECT key, status FROM verification_checks")).all())["ffmpeg_verified"] == "passed"
print("ok")
'''


def run_migration(database_url: str, directory: str) -> subprocess.CompletedProcess:
    target = Path(directory)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "instance").mkdir()
    (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": database_url}))
    return subprocess.run([sys.executable, "-c", MIGRATION], cwd=target, env={**os.environ, "PYTHONPATH": str(target)},
                          capture_output=True, text=True)


def postgresql_url() -> str:
    url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
        return ""
    return "postgresql+psycopg://" + url[len("postgresql://"):] if url.startswith("postgresql://") else url


class MigrationTest(unittest.TestCase):
    def test_sqlite_statuses_up_and_down(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_postgresql_statuses_up_and_down(self):
        url = postgresql_url()
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(url, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_revision_id_fits_postgresql(self):
        self.assertLessEqual(len("0025_verification_status"), 32)
        self.assertTrue((ROOT / "migrations" / "versions" / "0025_verification_status.py").is_file())


VERIFICATION = r'''
from app import readiness
from app.models import AuditEvent
ORIGIN = {"Origin": "http://testserver"}

listed = client.get("/api/admin/verification").json()
items, summary = listed["items"], listed["summary"]
assert [item["key"] for item in items] == list(readiness.CHECKLIST_KEYS)
assert all(item["status"] == "not_checked" and not item["verified"] and item["recorded_at"] is None for item in items)
assert summary["not_checked"] == summary["total"] == len(readiness.CHECKLIST) and not summary["complete"]
assert summary["open"] == list(readiness.CHECKLIST_KEYS)
by_key = {item["key"]: item for item in items}
assert by_key["tiktok_upload"]["optional"] and not by_key["release_ci_green"]["optional"]
assert by_key["release_ci_green"]["group"] == "release" and by_key["legal_terms_reviewed"]["group"] == "legal"

# A required gate is never "not applicable"; an optional provider may be.
r = client.put("/api/admin/verification/release_ci_green", json={"status": "not_applicable"})
assert r.status_code == 422, r.text
r = client.put("/api/admin/verification/tiktok_upload", json={"status": "not_applicable", "note": "TikTok not used"})
assert r.status_code == 200, r.text
skipped = r.json()
assert skipped["status"] == "not_applicable" and skipped["recorded_by"] == "owner@example.com" and skipped["recorded_at"]
assert skipped["verified"] is False and skipped["verified_at"] is None and skipped["note"] == "TikTok not used"

failed = client.put("/api/admin/verification/email_dns", json={"status": "failed", "note": "DKIM fails"}).json()
assert failed["status"] == "failed" and failed["recorded_at"] and failed["recorded_by"] == "owner@example.com"
# Saving only the note keeps who recorded the status, and when.
again = client.put("/api/admin/verification/email_dns", json={"status": "failed", "note": "DKIM fails: selector s1"}).json()
assert again["recorded_at"] == failed["recorded_at"] and again["note"] == "DKIM fails: selector s1"

# The older form: verified true is passed, false is not checked.
legacy = client.put("/api/admin/verification/ffmpeg_verified", json={"verified": True, "note": "6.1"}).json()
assert legacy["status"] == "passed" and legacy["verified"] and legacy["verified_by"] == "owner@example.com"
cleared = client.put("/api/admin/verification/ffmpeg_verified", json={"verified": False}).json()
assert cleared["status"] == "not_checked" and cleared["recorded_at"] is None and not cleared["verified"]
assert client.put("/api/admin/verification/ffmpeg_verified", json={"note": "x"}).status_code == 422
assert client.put("/api/admin/verification/ffmpeg_verified", json={"status": "maybe"}).status_code == 422
assert client.put("/api/admin/verification/made_up", json={"status": "passed"}).status_code == 404

# Only system administrators.
member = TestClient(app, headers=ORIGIN)
assert member.post("/api/register", json={"email": "m@example.com", "password": "member-password-1",
                                          "workspace_name": "M", "accept_terms": True}).status_code == 201
assert member.get("/api/admin/verification").status_code == 403
assert member.put("/api/admin/verification/email_dns", json={"status": "passed"}).status_code == 403

with Session() as db:
    details = [json.loads(event.details_json) for event in
               db.scalars(select(AuditEvent).where(AuditEvent.action == "admin.verification_updated"))]
assert {"status": "failed", "previous": "not_checked"} in details and {"status": "failed", "previous": "failed"} in details
assert all("note" not in item for item in details)

# Complete only when every gate passed, or is not applicable where that is allowed.
for key in readiness.CHECKLIST_KEYS:
    if key != "tiktok_upload":
        assert client.put(f"/api/admin/verification/{key}", json={"status": "passed"}).status_code == 200, key
summary = client.get("/api/admin/verification").json()["summary"]
assert summary["complete"] and summary["open"] == [], summary
assert summary["passed"] == len(readiness.CHECKLIST) - 1 and summary["not_applicable"] == 1
assert client.put("/api/admin/verification/email_dns", json={"status": "failed"}).status_code == 200
summary = client.get("/api/admin/verification").json()["summary"]
assert not summary["complete"] and summary["open"] == ["email_dns"] and summary["failed"] == 1
print("verification ok")
'''


class VerificationApiTest(unittest.TestCase):
    def test_statuses_who_and_when_never_automatic(self):
        result = run_program(VERIFICATION)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])
        self.assertIn("verification ok", result.stdout)


def dictionary_section(language: str, section: str) -> str:
    """The text of ``admin.verification.<section>`` in one language file."""
    source = (ROOT / "frontend" / "src" / "lib" / "i18n" / f"{language}.ts").read_text(encoding="utf-8")
    start = source.index("    verification: {\n      readiness:")
    body = source[start:source.index("\n    },\n", start)]
    # One line ("groups: { … },") or a block closed on its own line.
    match = re.search(rf"\n      {section}: \{{([^\n]*)\}},\n", body) or \
        re.search(rf"\n      {section}: \{{\n(.*?)\n      \}},\n", body, re.S)
    assert match, (language, section)
    return match.group(1)


class ChecklistCoverageTest(unittest.TestCase):
    def test_keys_are_unique_short_and_grouped(self):
        keys = readiness.CHECKLIST_KEYS
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(len(key) <= 40 for key in keys))  # verification_checks.key is String(40)
        order = []
        for _, group, _, _ in readiness.CHECKLIST:
            if not order or order[-1] != group:
                order.append(group)
        self.assertEqual(len(order), len(set(order)), "each group is listed in one block")
        self.assertTrue(readiness.OPTIONAL <= set(keys))
        for key in ("release_ci_green", "release_preflight", "release_deploy", "restore_rehearsal",
                    "legal_terms_reviewed", "legal_privacy_reviewed", "bank_qr_round_trip", "security_headers"):
            self.assertNotIn(key, readiness.OPTIONAL)

    def test_every_gate_and_group_has_a_label_in_every_language(self):
        keys = set(readiness.CHECKLIST_KEYS)
        groups = {group for _, group, _, _ in readiness.CHECKLIST}
        for language in ("vi", "en", "ja"):
            with self.subTest(language=language):
                labels = set(re.findall(r"(\w+): \"", dictionary_section(language, "items")))
                self.assertEqual(labels, keys)
                self.assertEqual(set(re.findall(r"(\w+): \"", dictionary_section(language, "groups"))), groups)
                self.assertEqual(set(re.findall(r"(\w+): \"", dictionary_section(language, "statusNames"))),
                                 set(readiness.STATUSES))

    def test_the_release_checklist_maps_to_real_gates_and_names_them_all(self):
        text = (ROOT / "docs" / "RELEASE_V1_CHECKLIST.md").read_text(encoding="utf-8")
        named = set()
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if line.startswith("|") else []
            if len(cells) >= 4 and cells[-1] not in ("Recorded as", "---") and not set(cells[-1]) <= {"-", ":"}:
                named |= set(re.findall(r"`([a-z0-9_]+)`", cells[-1]))
        keys = set(readiness.CHECKLIST_KEYS)
        self.assertEqual(named - keys - {"preflight", "ci"}, set(), "unknown gate named in the checklist")
        self.assertEqual(keys - named, set(), "gate missing from docs/RELEASE_V1_CHECKLIST.md")


class ReleaseDocsTest(unittest.TestCase):
    NOTICE = {"en": "[bracketed]", "vi": "[ngoặc vuông]", "ja": "[角括弧]"}  # the template notice, not a placeholder
    MARKER = {"en": "LEGAL REVIEW REQUIRED", "vi": "CẦN LUẬT SƯ RÀ SOÁT", "ja": "法務の確認が必要"}

    def test_every_legal_placeholder_is_listed_and_the_review_marker_stays(self):
        status = (ROOT / "docs" / "V1_RELEASE_STATUS.md").read_text(encoding="utf-8")
        for language in ("en", "vi", "ja"):
            source = (ROOT / "frontend" / "src" / "lib" / "i18n" / f"{language}.ts").read_text(encoding="utf-8")
            legal = source[source.index("  legal: {"):source.index("\n  },\n", source.index("  legal: {"))]
            placeholders = set(re.findall(r"\[[^\[\]\n]{2,120}\]", legal)) - {self.NOTICE[language]}
            with self.subTest(language=language):
                self.assertGreaterEqual(len(placeholders), 11)
                self.assertEqual({item for item in placeholders if f"`{item}`" not in status}, set())
                self.assertIn(self.MARKER[language], legal)

    def test_the_documents_name_the_current_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
        head = script.get_current_head()  # raises if two migrations ever share a parent (two heads)
        self.assertEqual(head, "0026_change_production_origin")
        for name in ("RELEASE_V1_CHECKLIST.md", "LIVE_VERIFICATION.md", "V1_RELEASE_STATUS.md", "FINAL_PRODUCT_AUDIT.md",
                     "PRODUCTION_BOOTSTRAP.md"):
            with self.subTest(doc=name):
                self.assertIn(head, (ROOT / "docs" / name).read_text(encoding="utf-8"))
        for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
            with self.subTest(doc=path.name):
                text = path.read_text(encoding="utf-8")
                for old in ("0024_operations (head)", "0025_verification_status (head)", "head `0025_verification_status`"):
                    self.assertNotIn(old, text)

    def test_the_release_stays_a_candidate_until_recorded_otherwise(self):
        status = (ROOT / "docs" / "V1_RELEASE_STATUS.md").read_text(encoding="utf-8")
        self.assertRegex(status.splitlines()[2], r"\*\*Status: (RELEASE_CANDIDATE|RELEASED v1\.0\.0)\.?\*\*")
        self.assertIn("git tag -a v1.0.0", status)
        self.assertIn("# git push origin v1.0.0", status)  # documented, never run by a tool


class FakeProbe(release_check.Probe):
    """A server where everything answers as it should, unless told otherwise."""

    def __init__(self, *, units=None, ss=None, fetches=None, git=None):
        self.units = units or {}
        self.ss = ss
        self.fetches = fetches or {}
        self.git = git or {}
        self.commands = []

    def run(self, command, timeout=30):
        self.commands.append(command)
        if command[0] == "git":
            key = " ".join(command[3:])
            return self.git.get(key, (0, {"rev-parse HEAD": "08f1eef9" + "0" * 32 + "\n",
                                          "rev-parse --abbrev-ref HEAD": "feat/studio-foundation\n"}.get(key, "")))
        if command[0] == "systemctl":
            if command[1] == "--version":
                return 0, "systemd 255\n"
            if command[1] in ("is-enabled", "is-active"):
                enabled, active = self.units.get(command[2], ("enabled", "active") if command[2] in (
                    "reelforge-api", "reelforge-frontend", "reelforge-backup.timer",
                    "reelforge-media-maintenance.timer") or command[2].startswith("reelforge-worker@") else
                    ("not-found", "inactive"))
                return 0, (enabled if command[1] == "is-enabled" else active) + "\n"
            if command[1] == "list-units":
                return 0, "".join(f"{unit} loaded failed failed X\n" for unit, (_, active) in self.units.items()
                                  if active == "failed")
            return 0, "no\n"
        if command[0] == "ss":
            return (127, "") if self.ss is None else (0, self.ss)
        return super().run(command, timeout)

    def fetch(self, url, *, timeout=10, headers=None, local=True):
        if url in self.fetches:
            return self.fetches[url]
        if url.endswith("/health/ready"):
            return 200, json.dumps({"status": "ok", "checks": {}, "warnings": []}).encode()
        return 200, b"ok"


SS = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      2048       127.0.0.1:8000       0.0.0.0:*
LISTEN 0      511        127.0.0.1:3001       0.0.0.0:*
LISTEN 0      4096       127.0.0.53%lo:53     0.0.0.0:*
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*
LISTEN 0      4096           [::1]:5432          [::]:*
"""


def by_name(checks):
    return {(check.area, check.name): check for check in checks}


class ReleaseCheckUnitTest(unittest.TestCase):
    def test_ports_only_on_loopback(self):
        self.assertEqual(release_check.listeners(SS)[53], ["127.0.0.53"])
        self.assertEqual(release_check.listeners(SS)[5432], ["::1"])
        good = by_name(release_check.port_checks(FakeProbe(ss=SS)))
        self.assertEqual((good[("PORTS", "API")].status, good[("PORTS", "frontend")].status), (PASS, PASS))
        self.assertEqual(good[("PORTS", "API")].detail, "127.0.0.1:8000")
        exposed = SS.replace("127.0.0.1:3001", "*:3001").replace("127.0.0.1:8000", "[::1]:8000")
        checks = by_name(release_check.port_checks(FakeProbe(ss=exposed)))
        self.assertEqual(checks[("PORTS", "API")].status, PASS)  # ::1 is loopback too
        self.assertEqual(checks[("PORTS", "frontend")].status, FAIL)
        self.assertIn("reachable from the network", checks[("PORTS", "frontend")].detail)
        down = by_name(release_check.port_checks(FakeProbe(ss=SS.replace("127.0.0.1:8000", "0.0.0.0:8000"))))
        self.assertEqual(down[("PORTS", "API")].status, FAIL)
        missing = by_name(release_check.port_checks(FakeProbe(ss=SS.replace("127.0.0.1:8000", "127.0.0.1:8080"))))
        self.assertIn("nothing listens", missing[("PORTS", "API")].detail)
        self.assertEqual([check.status for check in release_check.port_checks(FakeProbe(ss=None))], [MANUAL])

    def test_services_workers_and_timers(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory)
            for unit in (ROOT / "deploy" / "systemd").glob("reelforge*"):
                shutil.copy(unit, installed / unit.name)
            (installed / "journald.conf").write_text("[Journal]\n")
            saved = release_check.SYSTEMD_DIR, release_check.JOURNALD_CONF
            release_check.SYSTEMD_DIR, release_check.JOURNALD_CONF = installed, installed / "journald.conf"
            try:
                fine = by_name(release_check.service_checks(FakeProbe(), db_ok=False))
                self.assertTrue(all(check.status == PASS for check in fine.values()), fine)
                self.assertIn(("SERVICES", "reelforge-worker@scheduler"), fine)
                (installed / "reelforge-api.service").write_text("[Service]\nUser=someone\n")
                broken = FakeProbe(units={"reelforge-frontend": ("enabled", "failed"),
                                          "reelforge-worker@social": ("disabled", "inactive"),
                                          "reelforge-media-maintenance.timer": ("disabled", "inactive")})
                checks = by_name(release_check.service_checks(broken, db_ok=False))
            finally:
                release_check.SYSTEMD_DIR, release_check.JOURNALD_CONF = saved
        self.assertEqual(checks[("SERVICES", "reelforge-api")].status, PASS)
        self.assertEqual(checks[("SERVICES", "reelforge-frontend")].status, FAIL)
        self.assertEqual(checks[("SERVICES", "failed units")].detail, "reelforge-frontend")
        self.assertEqual(checks[("SERVICES", "workers not enabled")].status, WARN)
        self.assertIn("social", checks[("SERVICES", "workers not enabled")].detail)
        self.assertEqual(checks[("SERVICES", "reelforge-media-maintenance.timer")].status, FAIL)
        self.assertIn("sudo systemctl enable --now", checks[("SERVICES", "reelforge-media-maintenance.timer")].detail)
        self.assertEqual(checks[("SERVICES", "unit files")].status, WARN)
        self.assertIn("reelforge-api.service", checks[("SERVICES", "unit files")].detail)
        no_systemd = FakeProbe()
        no_systemd.run = lambda command, timeout=30: (127, "")
        self.assertEqual([check.status for check in release_check.service_checks(no_systemd, db_ok=False)], [MANUAL])

    def test_health_and_source(self):
        api = release_check.API
        ready = by_name(release_check.health_checks(FakeProbe()))
        self.assertTrue(all(check.status == PASS for check in ready.values()), ready)
        warned = by_name(release_check.health_checks(FakeProbe(fetches={
            f"{api}/health/ready": (200, b'{"status": "ok", "checks": {}, "warnings": ["setup_open"]}')})))
        self.assertEqual(warned[("HEALTH", "/health/ready")].status, WARN)
        down = by_name(release_check.health_checks(FakeProbe(fetches={
            f"{api}/health/live": (0, b""),
            f"{api}/health/ready": (503, b'{"status": "unavailable", "checks": {"migrations": {"status": "behind"}}}')})))
        self.assertEqual(down[("HEALTH", "/health/live")].status, FAIL)
        self.assertEqual(down[("HEALTH", "/health/ready")].detail, "HTTP 503: migrations behind")

        clean = by_name(release_check.source_checks(FakeProbe(), expect_commit="08f1eef9"))
        self.assertEqual(clean[("SOURCE", "working tree")].status, PASS)
        self.assertEqual(clean[("SOURCE", "expected commit")].status, PASS)
        self.assertEqual(clean[("SOURCE", "CI on this commit")].status, MANUAL)
        dirty = by_name(release_check.source_checks(FakeProbe(git={
            "status --porcelain --untracked-files=no": (0, " M deploy.sh\n")}), expect_commit="abc1234"))
        self.assertEqual(dirty[("SOURCE", "working tree")].status, FAIL)
        self.assertIn("deploy.sh", dirty[("SOURCE", "working tree")].detail)
        self.assertEqual(dirty[("SOURCE", "expected commit")].status, FAIL)
        unknown = release_check.source_checks(FakeProbe(git={"rev-parse HEAD": (128, "")}), expect_commit="abc1234")
        self.assertEqual([check.status for check in unknown], [WARN, FAIL])

    def test_ci_from_github_never_prints_the_remote_credentials(self):
        remote = "https://x-access-token:ghs_SECRET_TOKEN@github.com/nguyenductai29/reelforge-studio.git\n"
        commit = "08f1eef9" + "0" * 32
        url = (f"https://api.github.com/repos/nguyenductai29/reelforge-studio/commits/{commit}/check-runs"
               "?per_page=100")

        def runs(*jobs):
            return 200, json.dumps({"check_runs": [{"name": name, "status": status, "conclusion": conclusion}
                                                   for name, status, conclusion in jobs]}).encode()

        cases = ((runs(("backend (3.11)", "completed", "success"), ("annotate", "completed", "skipped")), "success"),
                 (runs(("backend (3.11)", "completed", "failure")), "failure"),
                 (runs(("e2e", "in_progress", None)), "pending"), (runs(), "none"), ((403, b"{}"), "unknown"))
        for answer, expected in cases:
            with self.subTest(expected=expected):
                probe = FakeProbe(git={"remote get-url origin": (0, remote)}, fetches={url: answer})
                ci = release_check.github_ci(probe, commit)
                self.assertEqual(ci["status"], expected)
                self.assertEqual(ci.get("repository"), "nguyenductai29/reelforge-studio")
                self.assertNotIn("ghs_SECRET_TOKEN", json.dumps(ci))
        self.assertEqual(release_check.github_ci(FakeProbe(git={"remote get-url origin": (0, "/srv/git/x.git")}),
                                                 commit)["status"], "unknown")

    def test_verdict_and_rendering_never_claim_a_manual_gate(self):
        keys = readiness.CHECKLIST_KEYS
        gates = [{"key": key, "group": group, "paid": paid, "optional": key in readiness.OPTIONAL, "how": how,
                  "status": "passed", "recorded_by": "admin@example.com", "recorded_at": "2026-10-02T10:00:00+00:00",
                  "note": None} for key, group, paid, how in readiness.CHECKLIST]

        def summary_of(items):
            return readiness.checklist_summary({item["key"]: item["status"] for item in items})

        fine = [Check("SOURCE", "commit", PASS, "x"), Check("BACKUPS", "off-server copy", MANUAL, "y"),
                Check("STORAGE", "free space", WARN, "z")]
        green = {"status": "success", "jobs": []}
        self.assertEqual(release_check.verdict(fine, gates, summary_of(gates), green), (release_check.READY, []))
        self.assertEqual(release_check.verdict(fine, gates, summary_of(gates), {"status": "unknown"})[0],
                         release_check.READY)
        result, blockers = release_check.verdict(fine, gates, summary_of(gates),
                                                 {"status": "failure", "jobs": [{"name": "e2e", "conclusion": "failure"}]})
        self.assertEqual((result, blockers), (release_check.CANDIDATE, ["CI on this commit: failure (e2e)"]))
        result, blockers = release_check.verdict(fine + [Check("PORTS", "API", FAIL, "listens on 0.0.0.0:8000")],
                                                 gates, summary_of(gates), green)
        self.assertEqual(result, release_check.CANDIDATE)
        self.assertIn("pre-flight FAIL: PORTS / API", blockers[0])
        unrecorded = [dict(item, status="not_checked", recorded_by=None, recorded_at=None) if item["key"] == keys[0]
                      else item for item in gates]
        self.assertEqual(release_check.verdict(fine, unrecorded, summary_of(unrecorded), green),
                         (release_check.CANDIDATE, [f"gates not checked (1): {keys[0]}"]))
        wrongly_skipped = [dict(item, status="not_applicable") if item["key"] == "release_ci_green" else item
                           for item in gates]
        self.assertEqual(release_check.verdict(fine, wrongly_skipped, summary_of(wrongly_skipped), green)[1],
                         ["gates not applicable but required (1): release_ci_green"])
        mixed = [dict(item, status={"email_dns": "failed", "tiktok_upload": "not_applicable",
                                    "youtube_upload": "not_checked"}.get(item["key"], item["status"])) for item in gates]
        self.assertEqual(release_check.verdict(fine, mixed, summary_of(mixed), green)[1],
                         ["gates failed (1): email_dns", "gates not checked (1): youtube_upload"])
        self.assertEqual(release_check.verdict(fine, None, None, green)[0], release_check.CANDIDATE)

        data = {"generated_at": "2026-10-02T10:00:00+00:00", "host": "server", "commit": "08f1eef9", "branch": "b",
                "verdict": release_check.CANDIDATE, "blockers": [f"gates not checked (1): {keys[0]}"],
                "preflight": {"counts": release_check.counts(fine), "checks": [vars(check) for check in fine]},
                "readiness": {"checked_at": "x", "attention": []}, "ci": {"status": "not_checked"},
                "gates": {"summary": summary_of(unrecorded), "items": unrecorded}}
        text = release_check.render_report(data)
        self.assertIn(f"    MANUAL {keys[0]}", text)
        self.assertNotIn(f"PASS   {keys[0]}", text)
        self.assertIn("Verdict: RELEASE_CANDIDATE", text)

    def test_exit_status_follows_fail_only(self):
        saved = release_check.preflight
        try:
            for checks, code in (([Check("A", "a", WARN), Check("B", "b", MANUAL)], 0),
                                 ([Check("A", "a", PASS), Check("B", "b", FAIL)], 1)):
                release_check.preflight = lambda **_: checks
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(release_check.main(["preflight", "--json"]), code)
                self.assertEqual(json.loads(out.getvalue())["result"], FAIL if code else PASS)
        finally:
            release_check.preflight = saved

    def test_scrub_hides_passwords_and_url_credentials(self):
        bare = 'OperationalError: password authentication failed (password "pa55word")'
        self.assertEqual(release_check.scrub(bare, ["pa55word"]),
                         'OperationalError: password authentication failed (password "***")')
        in_url = "could not connect to postgresql://studio:pa55word@127.0.0.1/reelforge_studio_db"
        self.assertEqual(release_check.scrub(in_url, []),  # a URL's credentials go even when the password is unknown
                         "could not connect to postgresql://***@127.0.0.1/reelforge_studio_db")

    def test_workers_match_the_heartbeats_and_deploy(self):
        from app.heartbeat import WORKERS

        names = release_check._workers()
        self.assertEqual(names, tuple(name.removesuffix("_worker") for name in WORKERS))
        self.assertIn("for worker in " + " ".join(names) + "; do", (ROOT / "deploy.sh").read_text(encoding="utf-8"))


PREFLIGHT = r'''
import contextlib, io, sys
from app import backup, release_check, storage
from app.release_check import FAIL, MANUAL, PASS, WARN
from app.models import SystemSetting

# The master key in a file (the same key the PRELUDE's data is encrypted with), and one stored secret.
key_file = Path("instance/master.key").resolve()
key_file.write_text(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"])
if os.name == "posix":
    os.chmod(key_file, 0o600)
os.environ["REELFORGE_MASTER_KEY_FILE"] = str(key_file)
backups = Path("instance/backups").resolve()
with Session.begin() as db:
    system_config.save(db, None, secrets={"ai.anthropic.api_key": ("replace", "sk-ant-STORED-SENTINEL")}, section="ai")
    system_config.save(db, None, values={"backups.directory": str(backups)}, section="backups")
    root = storage.media_root(db)
system_config.invalidate()
root.mkdir(parents=True, exist_ok=True)
# Legacy values the services would load: listed by name, never printed.
Path(".env.runtime").write_text("RUNWARE_API_KEY=rw-LEGACY-SENTINEL\nREELFORGE_LOG_LEVEL=INFO\nEMPTY=\n")
system_env = Path("instance/system-runtime.env")
system_env.write_text("REPLICATE_API_TOKEN=r8-SYSTEM-SENTINEL\n")
release_check.SYSTEM_RUNTIME_ENV = system_env
release_check.SYSTEMD_DIR = Path("instance/no-systemd")
release_check.JOURNALD_CONF = Path("instance/journald.conf")

class Server(release_check.Probe):
    def run(self, command, timeout=30):
        name = Path(command[0]).name
        if command[0] == "git":
            return {"rev-parse": (0, ("08f1eef9" + "0" * 32) if command[-1] == "HEAD" and "--abbrev-ref" not in command
                                  else "feat/studio-foundation"),
                    "status": (0, ""),
                    "remote": (0, "https://x-access-token:ghs_REMOTE_SENTINEL@github.com/nguyenductai29/reelforge-studio.git")
                    }[command[3]]
        if command[0] == "systemctl":
            if command[1] == "--version":
                return 0, "systemd 255"
            if command[1] == "is-enabled":
                return 0, "enabled" if not command[2].startswith("reelforge-") or "-worker@" in command[2] \
                    or command[2] in ("reelforge-backup.timer", "reelforge-media-maintenance.timer") else "not-found"
            if command[1] == "is-active":
                return 0, "active"
            return 0, ""
        if command[0] == "ss":
            return 0, "LISTEN 0 1 127.0.0.1:8000 0.0.0.0:*\nLISTEN 0 1 127.0.0.1:3001 0.0.0.0:*\n"
        if name in ("ffmpeg", "ffprobe"):
            return 0, f"{name} version 6.1.1"
        return super().run(command, timeout)

    def fetch(self, url, *, timeout=10, headers=None, local=True):
        if "api.github.com" in url:
            assert "ghs_REMOTE_SENTINEL" not in url and "Authorization" not in (headers or {})
            return 200, json.dumps({"check_runs": [{"name": "backend (3.11)", "status": "completed",
                                                    "conclusion": "failure"}]}).encode()
        if url.endswith("/health/ready"):
            return 200, b'{"status": "ok", "checks": {}, "warnings": []}'
        return 200, b"ok"

SENTINELS = ("sk-ant-STORED-SENTINEL", "rw-LEGACY-SENTINEL", "r8-SYSTEM-SENTINEL", "ghs_REMOTE_SENTINEL",
             "sk-SENTINEL", "gemini-SENTINEL", os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"])

def clean(text):
    for secret in SENTINELS:
        assert secret not in text, secret

def checks_of(checks):
    return {(check.area, check.name): check for check in checks}

first = checks_of(release_check.preflight(Server()))
assert first[("DATABASE", "connection")].status == FAIL and "PostgreSQL" in first[("DATABASE", "connection")].detail
assert first[("DATABASE", "expected head")].status == PASS, first[("DATABASE", "expected head")]
assert first[("MASTER KEY", "key")].status == PASS and str(key_file) in first[("MASTER KEY", "key")].detail
assert first[("MASTER KEY", "stored secrets")].status == PASS, first[("MASTER KEY", "stored secrets")]
assert first[("MASTER KEY", "off-server copy")].status == MANUAL
assert first[("SERVICES", "reelforge-api")].status == PASS and first[("PORTS", "frontend")].status == PASS
assert first[("HEALTH", "/health/ready")].status == PASS
assert first[("STORAGE", "media root")].status == PASS and first[("STORAGE", "writable")].status == PASS
assert not list(root.glob(".release-preflight-*")), "the probe file is removed"
assert first[("BACKUPS", "last success")].status == FAIL, first[("BACKUPS", "last success")]
assert first[("FFMPEG", "ffmpeg")].status == PASS and "6.1.1" in first[("FFMPEG", "ffmpeg")].detail
# The disposable copy runs as a development machine: its bootstrap.json carries the local override.
assert first[("SYSTEM CONFIG", "bootstrap.json")].status == FAIL
runtime_file = first[("SYSTEM CONFIG", "legacy values: .env.runtime")]
assert runtime_file.status == WARN and "RUNWARE_API_KEY" in runtime_file.detail, runtime_file
assert "EMPTY" not in runtime_file.detail and "REELFORGE_LOG_LEVEL" not in runtime_file.detail
assert "REPLICATE_API_TOKEN" in first[("SYSTEM CONFIG", "legacy values: system-runtime.env")].detail
assert first[("SYSTEM CONFIG", "public origin")].status == PASS
assert first[("SYSTEM CONFIG", "public origin")].detail == "https://reelforge.mul-service.com"
# The PRELUDE's redirect variables name http://localhost:3000, not the public origin: listed, never fixed silently.
redirects = first[("SYSTEM CONFIG", "OAuth redirects")]
assert redirects.status == WARN and "youtube http://localhost:3000/youtube/callback" in redirects.detail, redirects
assert first[("SYSTEM CONFIG", "secure cookies")].status == PASS
assert first[("SYSTEM CONFIG", "trusted proxies")].status == PASS
assert first[("SECURITY", "first-run setup")].status == PASS and first[("SECURITY", "active administrator")].status == PASS
assert first[("SECURITY", "administrator 2FA")].status == WARN
assert first[("SECURITY", "break-glass command")].status == PASS, first[("SECURITY", "break-glass command")]
clean(json.dumps([vars(check) for check in first.values()]))

# A backup, then a key file next to the dumps; a wrong key; an origin that is not the expected one.
assert backup.run()["ok"]
Path(backups / "master.key").write_text("x")
with Session.begin() as db:
    db.get(SystemSetting, "secure_cookies").value = "false"
key_file.write_text(Fernet.generate_key().decode())
second = checks_of(release_check.preflight(Server(), expect_origin="https://studio.example.com"))
assert second[("BACKUPS", "last success")].status == PASS and second[("BACKUPS", "newest dump")].status == PASS
assert second[("BACKUPS", "key apart")].status == FAIL and "master.key" in second[("BACKUPS", "key apart")].detail
assert second[("MASTER KEY", "stored secrets")].status == FAIL, second[("MASTER KEY", "stored secrets")]
assert second[("SYSTEM CONFIG", "secure cookies")].status == FAIL
assert second[("SYSTEM CONFIG", "public origin")].status == FAIL
key_file.write_text(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"])

# The report: recorded gates only; CI as GitHub says; never a secret.
release_check.Probe = Server
data = release_check.report(Server())
clean(json.dumps(data))
assert data["verdict"] == release_check.CANDIDATE and data["ci"]["status"] == "failure"
assert data["ci"]["repository"] == "nguyenductai29/reelforge-studio"
assert "CI on this commit: failure (backend (3.11))" in data["blockers"]
assert data["gates"]["summary"]["not_checked"] == len(data["gates"]["items"])
assert all(item["status"] == "not_checked" and item["recorded_by"] is None for item in data["gates"]["items"])
client.put("/api/admin/verification/release_preflight", json={"status": "passed", "note": "no FAIL on 08f1eef9"})
gates = {item["key"]: item for item in release_check.report(Server(), network=False)["gates"]["items"]}
assert gates["release_preflight"]["status"] == "passed" and gates["release_preflight"]["recorded_by"] == "owner@example.com"
assert gates["release_ci_green"]["status"] == "not_checked"
text = release_check.render_report(release_check.report(Server(), network=False))
clean(text)
assert "MANUAL release_ci_green" in text and "PASS   release_preflight · owner@example.com" in text, text
assert "CI on this commit: not_checked" in text

with contextlib.redirect_stdout(io.StringIO()) as out:
    code = release_check.main(["preflight"])
clean(out.getvalue())
assert code == 1 and "Result: FAIL" in out.getvalue(), out.getvalue()[-2000:]
with contextlib.redirect_stdout(io.StringIO()) as out:
    code = release_check.main(["report", "--json", "--no-network"])
clean(out.getvalue())
assert code == 1 and json.loads(out.getvalue())["verdict"] == "RELEASE_CANDIDATE"
print("release check ok")
'''


class ReleaseCheckIntegrationTest(unittest.TestCase):
    def test_preflight_and_report_on_a_disposable_installation(self):
        result = run_program(PREFLIGHT)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])
        self.assertIn("release check ok", result.stdout)

    def test_the_real_command_on_a_database_never_migrated(self):
        """The command itself, with the real system probes: FAIL lines and exit status 1, never a crash or a key."""
        from cryptography.fernet import Fernet

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({
                "database_url": f"sqlite:///{target}/instance/empty.db"}))
            key = Fernet.generate_key().decode()
            (target / "instance" / "master.key").write_text(key)
            env = {name: value for name, value in os.environ.items()
                   if not name.startswith(("REELFORGE_", "OPENAI", "GEMINI", "RUNWAY"))}
            result = subprocess.run([sys.executable, "-m", "app.release_check", "preflight", "--json"], cwd=target,
                                    env={**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8",
                                         "REELFORGE_MASTER_KEY_FILE": str(target / "instance" / "master.key")},
                                    capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(result.returncode, 1, result.stdout[-2000:] + result.stderr[-4000:])
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(key, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        found = {(check["area"], check["name"]): check["status"] for check in data["checks"]}
        self.assertEqual(data["result"], FAIL)
        self.assertEqual(found[("DATABASE", "alembic current")], FAIL)
        self.assertEqual(found[("MASTER KEY", "key")], PASS)
        self.assertIn(found[("SOURCE", "commit")], (PASS, WARN))
        self.assertTrue({"SOURCE", "DATABASE", "MASTER KEY", "SERVICES", "PORTS", "HEALTH", "STORAGE", "BACKUPS",
                         "FFMPEG", "SYSTEM CONFIG", "SECURITY"} <= {area for area, _ in found})
        self.assertEqual(found[("HEALTH", "/health/live")], FAIL)  # nothing listens on 127.0.0.1:8000 here


UNITTEST_LOG = """test_a (tests.test_x.A.test_a) ... ok
test_b (tests.test_x.B.test_b) ... FAIL
test_c (tests.test_y.C.test_c) ... ERROR

======================================================================
FAIL: test_b (tests.test_x.B.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/runner/work/tests/test_x.py", line 9, in test_b
    self.assertEqual(1, 2)
AssertionError: 1 != 2 (50% off: a, b)

======================================================================
ERROR: test_c (tests.test_y.C.test_c)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/runner/work/tests/test_y.py", line 3, in test_c
    raise KeyError("x")
KeyError: 'x'

----------------------------------------------------------------------
Ran 3 tests in 0.010s

FAILED (failures=1, errors=1)
"""


class CiAnnotateTest(unittest.TestCase):
    def test_failures_become_annotations(self):
        found = ci_annotate.annotations(UNITTEST_LOG.replace("\n", "\r\n"))
        self.assertEqual(len(found), 2)
        self.assertTrue(found[0].startswith("::error title=FAIL tests.test_x.B.test_b::Traceback"))
        self.assertIn("AssertionError: 1 != 2 (50%25 off: a, b)", found[0])  # % is escaped in a message
        self.assertNotIn("\n", found[0])
        self.assertIn("%0A", found[0])
        self.assertTrue(found[1].startswith("::error title=ERROR tests.test_y.C.test_c::"))
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "unittest.log"
            log.write_text(UNITTEST_LOG, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ci_annotate.main([str(log)]), 0)
            self.assertIn("2 failing test(s); 2 annotated", out.getvalue())
            log.write_text("Ran 3 tests in 0.1s\n\nFAILED (errors=1)\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                ci_annotate.main([str(log)])
            self.assertIn("::error title=unittest::No FAIL/ERROR block found (FAILED (errors=1))", out.getvalue())

    def test_ci_keeps_the_exit_status_of_a_piped_run(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        runs = re.findall(r"run: python -m unittest discover -s tests -v 2>&1 \| tee unittest\.log", ci)
        self.assertEqual(len(runs), 2)
        self.assertEqual(ci.count("shell: bash  # with pipefail"), 2)
        self.assertEqual(ci.count("python tests/ci_annotate.py unittest.log"), 2)


class DeployScriptTest(unittest.TestCase):
    def test_steps_run_in_the_documented_order(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        steps = re.findall(r'(?m)^echo "== (\d+)\. ([^=]+) =="$', script)
        self.assertEqual([int(number) for number, _ in steps], list(range(1, len(steps) + 1)))
        order = ["git pull", "instance/bootstrap.json is missing", "bash deploy/ensure-master-key.sh",
                 "pip install -r requirements.txt", "alembic upgrade head", "npm run build",
                 "sudo systemctl restart reelforge-api", 'systemctl is-active --quiet "${unit}"',
                 "http://127.0.0.1:8000/health/ready", "NeedDaemonReload", "reelforge-media-maintenance.timer",
                 "FAILED: these services are not running", "ReelForge deployment completed OK"]
        positions = [script.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions), list(zip(order, positions)))
        self.assertIn('*"(head)"*)', script)  # the migration is checked after upgrading
        self.assertIn('problems+=("the API is not ready (/health/ready)")', script)
        self.assertNotIn("sudo cp", script)  # units are never overwritten by a deploy
        # next build rewrites the tracked frontend/next-env.d.ts: put back right after the build, the checkout stays
        # the deployed commit (the pre-flight's working-tree check) and the next pull cannot conflict on it.
        restore = script.rindex("git checkout -- frontend/next-env.d.ts")
        self.assertLess(script.index("npm run build"), restore)
        self.assertLess(restore, script.index("sudo systemctl restart reelforge-api"))
        self.assertLess(script.index("git checkout -- frontend/next-env.d.ts"), script.index("git pull"))

    def test_scripts_parse(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")
        for script in ("deploy.sh", "deploy/release-preflight.sh", "deploy/release-report.sh"):
            with self.subTest(script=script):
                result = subprocess.run([bash, "-n", str(ROOT / script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        for script in ("deploy/release-preflight.sh", "deploy/release-report.sh"):
            text = (ROOT / script).read_text(encoding="utf-8")
            self.assertIn('exec "$PYTHON" -m app.release_check', text)


if __name__ == "__main__":
    unittest.main()
