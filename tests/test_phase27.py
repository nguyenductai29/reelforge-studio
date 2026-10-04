"""Phase 27: v1.0 release audit — the fixes and the guarantees they rely on.

* workspace isolation: every record of another studio answers 404/403 through every endpoint that takes its ID,
  and nothing about it changes; a member's records follow the studio they switched to;
* roles: viewers never change anything, editors never manage channels, members, settings or billing, admins never
  buy or transfer ownership; a removed member is refused at once;
* first-run setup only from the server itself (never a public address, through Cloudflare or directly), with a
  readiness warning and an audit event; afterwards the public sees 409 like before;
* client addresses: spoofed CF-Connecting-IP / X-Forwarded-For / X-Real-IP / Forwarded headers are ignored unless
  the peer is a trusted proxy, and X-Forwarded-For always;
* sign-in costs the same scrypt work for unknown and deactivated accounts (no timing oracle);
* metrics labels stay bounded whatever method a client sends;
* readiness fails when stored secrets do not decrypt with the key in use;
* audit events for checkout, terms acceptance and channel changes; an email failure never undoes a payment;
* backups refuse a relative or system directory;
* the break-glass recovery command (password, 2FA) signs the account out everywhere and is audited;
* deploy.sh surfaces failed services and the open setup; CI needs no secret and tests the production versions.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from tests.studio_harness import LOCAL_DEVELOPMENT, run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import LOCAL_DEVELOPMENT, run_program

from app import client_ip, metrics

ROOT = Path(__file__).resolve().parents[1]


def run_fresh(body: str):
    """``body`` against a migrated, empty database (no account yet), in a disposable copy of the app."""
    prelude = r'''
import json, os
from cryptography.fernet import Fernet
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app import system_config
system_config.activate()
from app.db import Session
ORIGIN = {"Origin": "http://testserver"}
'''
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("app", "migrations"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        (target / "instance" / "bootstrap.json").write_text(json.dumps({
            "database_url": f"sqlite:///{target}/instance/test.db", **LOCAL_DEVELOPMENT}))
        env = {key: value for key, value in os.environ.items() if not key.startswith(("REELFORGE_TOKEN", "REELFORGE_MASTER"))}
        return subprocess.run([sys.executable, "-c", prelude + body], cwd=target,
                              env={**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"},
                              capture_output=True, text=True, encoding="utf-8")


ISOLATION = r'''
from app import notifications
from app.models import AITool, LoginSession, Notification, PaymentOrder, SupportTicket, User, WorkspaceInvite
ORIGIN = {"Origin": "http://testserver"}

def browser():
    return TestClient(app, headers=ORIGIN)

# Studio A belongs to the PRELUDE's administrator; studio B to another account.
a = client
b = browser()
assert b.post("/api/register", json={"email": "b@example.com", "password": "b-password-12345", "workspace_name": "B",
                                     "accept_terms": True}).status_code == 201
workflow = a.post("/api/workflows", json={"name": "A flow"}).json()["id"]
graph = next(item["graph"] for item in a.get("/api/dashboard").json()["workflows"] if item["id"] == workflow)
tool = a.post("/api/ai-tools", json={"task": "script", "provider": "openai", "model": "gpt-4o-mini"})
assert tool.status_code in (200, 201), tool.text
tool = tool.json()["id"]
ticket = a.post("/api/support/tickets", json={"subject": "A", "category": "other", "description": "a"}).json()["id"]
invite = a.post("/api/workspace/invites", json={"email": "x@example.com", "role": "viewer"}).json()["id"]
now = datetime.now(timezone.utc)
with Session.begin() as db:
    owner = db.scalar(select(User).where(User.email == "owner@example.com"))
    a_user = owner.id
    a_session = db.scalar(select(LoginSession.id).where(LoginSession.user_id == owner.id, LoginSession.id.is_not(None)))
    db.add(WorkflowRun(id="run-a", workspace_id=workspace, workflow_id=workflow, project_id=project,
                       graph_snapshot="{}", status="completed", created_at=now))
    db.flush()
    db.add(WorkflowRunStep(id="step-a", run_id="run-a", node_id="n1", node_type="render", position=0,
                           status="completed", detail=""))
    db.add(Asset(id="asset-a", workspace_id=workspace, filename="a.mp4", content_type="video/mp4", bytes=10,
                 project_id=project, run_id="run-a", created_at=now))
    db.flush()
    db.add(Publication(id="pub-a", workspace_id=workspace, run_id="run-a", asset_id="asset-a", channel="youtube",
                       title="A", description="", state="scheduled", scheduled_for=now + timedelta(days=1),
                       created_at=now, updated_at=now))
    db.add(PaymentOrder(id="order-a", workspace_id=workspace, plan_code="standard", provider="bank_qr",
                        order_code=1111111111111, amount_vnd=199000, credits_award=0, status="pending",
                        provider_reference="RF1111111111111", created_at=now))
    notifications.notify(db, [owner.id], "support.reply", "A", "a", workspace_id=workspace, params={"subject": "A"})
with Session() as db:
    note = db.scalar(select(Notification.id).where(Notification.user_id == a_user))

attempts = [
    ("PATCH", f"/api/projects/{project}", {"title": "taken"}),
    ("GET", f"/api/workflows/{workflow}/readiness", None),
    ("GET", f"/api/workflows/{workflow}/runs", None),
    ("POST", f"/api/workflows/{workflow}/runs", {"project_id": project}),
    ("PUT", f"/api/workflows/{workflow}", graph),
    ("GET", "/api/workflow-runs/run-a", None),
    ("GET", "/api/workflow-runs/run-a/summary", None),
    ("POST", "/api/workflow-runs/run-a/approve", None),
    ("POST", "/api/workflow-runs/run-a/retry", None),
    ("GET", "/api/assets/asset-a", None),
    ("PATCH", "/api/assets/asset-a", {"project_id": None}),
    ("GET", "/api/publications/pub-a", None),
    ("GET", "/api/youtube/publications/pub-a", None),
    ("POST", "/api/publications/pub-a/retry", None),
    ("POST", "/api/youtube/publications/pub-a/retry", None),
    ("POST", "/api/publications", {"run_id": "run-a", "targets": [{"channel": "youtube", "title": "t"}]}),
    ("POST", "/api/publications/pub-a/cancel", None),
    ("PUT", "/api/publications/pub-a/schedule", {"scheduled_for": None}),
    ("PUT", f"/api/ai-tools/{tool}", {"task": "script", "provider": "openai", "model": "x", "is_enabled": False}),
    ("DELETE", f"/api/ai-tools/{tool}", None),
    ("GET", f"/api/support/tickets/{ticket}", None),
    ("POST", f"/api/support/tickets/{ticket}/messages", {"body": "taken"}),
    ("POST", f"/api/support/tickets/{ticket}/close", None),
    ("GET", "/api/billing/orders/order-a/transfer", None),
    ("POST", "/api/billing/orders/order-a/transferred", None),
    ("POST", "/api/billing/orders/order-a/refresh", None),
    ("POST", f"/api/workspace/invites/{invite}/resend", None),
    ("DELETE", f"/api/workspace/invites/{invite}", None),
    ("PUT", f"/api/workspace/members/{a_user}", {"role": "viewer"}),
    ("DELETE", f"/api/workspace/members/{a_user}", None),
    ("POST", f"/api/workspaces/{workspace}/switch", None),
    ("POST", f"/api/notifications/{note}/read", None),
    ("DELETE", f"/api/account/sessions/{a_session}", None),
]
for method, path, body in attempts:
    r = b.request(method, path, json=body)
    assert r.status_code in (403, 404), (method, path, r.status_code, r.text)
# Bulk media deletion skips what is not the caller's.
r = b.post("/api/assets/delete", json={"asset_ids": ["asset-a"]})
assert r.status_code == 200 and not r.json().get("deleted"), r.text

# Nothing of studio A changed, and B lists none of it.
with Session() as db:
    assert db.get(Project, project).title == "Rừng đêm"
    assert db.get(Asset, "asset-a").bytes == 10
    assert db.get(Publication, "pub-a").state == "scheduled"
    assert db.get(PaymentOrder, "order-a").status == "pending"
    assert db.get(SupportTicket, ticket).status != "closed"
    assert db.get(WorkspaceInvite, invite).revoked_at is None
    assert db.get(AITool, tool) is not None
    assert db.get(Notification, note).read_at is None
    assert db.get(LoginSession, db.scalar(select(LoginSession.token_hash).where(LoginSession.id == a_session))) is not None
dashboard = b.get("/api/dashboard").json()
assert not dashboard["projects"] and not dashboard["assets"] and not any(w["id"] == workflow for w in dashboard["workflows"])
assert b.get("/api/publications").json()["total"] == 0
assert b.get("/api/support/tickets").json()["total"] == 0
assert b.get("/api/billing/orders").json()["total"] == 0
assert all(item["user_id"] != a_user for item in b.get("/api/workspace/members").json()["members"])

# A member of both studios: the IDs of the studio they left behind stop working once they switch.
ws_b = b.get("/api/dashboard").json()["workspace"]["id"]
invite_b = b.post("/api/workspace/invites", json={"email": "owner@example.com", "role": "editor"}).json()
assert a.post("/api/invites/accept", json={"token": invite_b["link"].split("#token=")[1]}).status_code == 200
assert a.get("/api/dashboard").json()["workspace"]["id"] == ws_b
assert a.patch(f"/api/projects/{project}", json={"title": "stale"}).status_code == 404
assert a.get("/api/workflow-runs/run-a").status_code == 404
assert a.post(f"/api/workspaces/{workspace}/switch").status_code == 200
assert a.get("/api/workflow-runs/run-a").status_code == 200
print("ok")
'''

ROLES = r'''
from app.models import User
ORIGIN = {"Origin": "http://testserver"}

def browser():
    return TestClient(app, headers=ORIGIN)

owner = client
BANK = {"enabled": True, "bank_bin": "970407", "bank_name": "", "account_number": "0123456789",
        "account_name": "REELFORGE TEST", "transfer_prefix": "RF", "note": "", "sla_message": "", "use_for_vietqr": True}
assert owner.put("/api/admin/payment-config/bank_qr", json=BANK).status_code == 200
assert owner.put("/api/admin/plans/standard", json={"name": "Standard", "project_limit": 20, "workflow_limit": 10,
                                                    "monthly_credits": 100, "is_active": True,
                                                    "price_vnd": 199000}).status_code == 200
members = {}
for role in ("viewer", "editor", "admin"):
    link = owner.post("/api/workspace/invites", json={"email": f"{role}@example.com", "role": role}).json()["link"]
    person = browser()
    r = person.post("/api/register", json={"email": f"{role}@example.com", "password": f"{role}-password-123",
                                           "accept_terms": True, "invite_token": link.split("#token=")[1]})
    assert r.status_code == 201, r.text
    assert person.get("/api/dashboard").json()["workspace"]["role"] == role
    members[role] = person
viewer, editor, admin = members["viewer"], members["editor"], members["admin"]
with Session() as db:
    editor_id = db.scalar(select(User.id).where(User.email == "editor@example.com"))
    admin_id = db.scalar(select(User.id).where(User.email == "admin@example.com"))
SETTINGS = {"default_language": "vi", "video_orientation": "vertical", "approval_required": False,
            "editors_can_publish": True}
TRANSFER = {"user_id": editor_id, "password": "x", "confirm": "My Studio"}

def refused(person, method, path, body=None):
    r = person.request(method, path, json=body)
    assert r.status_code == 403, (method, path, r.status_code, r.text)

# Viewers change nothing.
for method, path, body in [
        ("POST", "/api/projects", {"title": "x", "topic": "y"}),
        ("PATCH", f"/api/projects/{project}", {"title": "x"}),
        ("POST", "/api/workflows", {"name": "x"}),
        ("POST", "/api/ai-tools", {"task": "script", "provider": "openai", "model": "m"}),
        ("POST", "/api/assets/delete", {"asset_ids": ["x"]}),
        ("POST", "/api/publications", {"run_id": "x", "targets": [{"channel": "youtube", "title": "t"}]}),
        ("DELETE", "/api/channels/tiktok", None),
        ("PUT", "/api/settings/workspace", SETTINGS),
        ("PUT", "/api/settings/default-models", {}),
        ("POST", "/api/workspace/invites", {"email": "y@example.com", "role": "viewer"}),
        ("PUT", "/api/workspace", {"name": "Taken"}),
        ("POST", "/api/billing/checkout", {"plan_code": "standard", "method": "vietqr"}),
        ("GET", "/api/billing/orders", None),
        ("POST", "/api/storage/cleanup", {"apply": True}),
        ("POST", "/api/workspace/transfer", TRANSFER)]:
    refused(viewer, method, path, body)
assert viewer.get("/api/dashboard").status_code == 200

# Editors create content, but manage no channel, member, setting or payment.
assert editor.post("/api/projects", json={"title": "Editor's", "topic": "t"}).status_code == 201
for method, path, body in [
        ("DELETE", "/api/channels/tiktok", None),
        ("POST", "/api/workspace/invites", {"email": "y@example.com", "role": "viewer"}),
        ("PUT", "/api/settings/workspace", SETTINGS),
        ("PUT", "/api/settings/default-models", {}),
        ("POST", "/api/billing/checkout", {"plan_code": "standard", "method": "vietqr"}),
        ("GET", "/api/billing/orders", None),
        ("POST", "/api/storage/cleanup", {"apply": True}),
        ("POST", "/api/workspace/transfer", TRANSFER)]:
    refused(editor, method, path, body)
# With "editors may publish" off, editors cannot publish either.
assert owner.put("/api/settings/workspace", json={**SETTINGS, "editors_can_publish": False}).status_code == 200
refused(editor, "POST", "/api/publications", {"run_id": "x", "targets": [{"channel": "youtube", "title": "t"}]})

# Admins manage the team and see payments, but never buy, nor transfer the studio, nor change the owner.
assert admin.get("/api/billing/orders").status_code == 200
assert admin.post("/api/workspace/invites", json={"email": "z@example.com", "role": "viewer"}).status_code == 201
refused(admin, "POST", "/api/billing/checkout", {"plan_code": "standard", "method": "vietqr"})
refused(admin, "POST", "/api/workspace/transfer", TRANSFER)
with Session() as db:
    owner_id = db.scalar(select(User.id).where(User.email == "owner@example.com"))
assert admin.put(f"/api/workspace/members/{owner_id}", json={"role": "viewer"}).status_code in (403, 409)
assert admin.delete(f"/api/workspace/members/{owner_id}").status_code in (403, 409)
# Only the owner buys.
assert owner.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"}).status_code == 201

# A removed member is refused at once.
assert owner.delete(f"/api/workspace/members/{editor_id}").status_code == 204
assert editor.get("/api/dashboard").status_code == 403
assert editor.post("/api/projects", json={"title": "x", "topic": "y"}).status_code == 403
print("ok")
'''

SECURITY = r'''
from app import accounts as account_module, audit as audit_module, backup, mailer, metrics as metrics_module, passwords
from app import secret_box
from app.models import AuditEvent, EmailOutbox, PaymentOrder, SystemConfig, User
ORIGIN = {"Origin": "http://testserver"}

# Setup is closed: nobody may create a first administrator any more.
assert client.get("/api/status").json()["setup_here"] is False
assert client.get("/health/ready").json()["warnings"] == []

# Sign-in costs scrypt work for an unknown or a deactivated account too.
spent = []
original = passwords.check_unknown_account
passwords.check_unknown_account = lambda password: spent.append(password) or original(password)
anonymous = TestClient(app, headers=ORIGIN)
assert anonymous.post("/api/login", json={"email": "nobody@example.com", "password": "whatever-123"}).status_code == 401
assert spent == ["whatever-123"]
r = anonymous.post("/api/register", json={"email": "gone@example.com", "password": "gone-password-123",
                                          "workspace_name": "Gone", "accept_terms": True})
assert r.status_code == 201
with Session.begin() as db:
    db.scalar(select(User).where(User.email == "gone@example.com")).is_active = False
assert anonymous.post("/api/login", json={"email": "gone@example.com", "password": "gone-password-123"}).status_code == 401
assert len(spent) == 2
passwords.check_unknown_account = original

# Spoofed address headers: only CF-Connecting-IP, only from a trusted proxy.
SPOOFED = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "203.0.113.51", "X-Real-IP": "203.0.113.52",
           "Forwarded": "for=203.0.113.53", "True-Client-IP": "203.0.113.54"}
outsider = TestClient(app, headers=ORIGIN, client=("198.51.100.30", 40000))
outsider.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"}, headers=SPOOFED)
proxy = TestClient(app, headers=ORIGIN, client=("127.0.0.1", 40001))
proxy.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"},
           headers={"X-Forwarded-For": "203.0.113.60", "X-Real-IP": "203.0.113.61"})
with Session() as db:
    addresses = {row.ip for row in db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login"))}
assert "198.51.100.30" in addresses and "127.0.0.1" in addresses, addresses
assert not any(address.startswith("203.0.113.") for address in addresses if address), addresses

# Metrics: an invented HTTP method is counted as OTHER.
client.request("FOOBAR123", "/api/status")
text = client.get("/internal/metrics").text
assert 'method="OTHER"' in text and "FOOBAR123" not in text

# Audit events: terms, checkout, channels.
assert client.post("/api/account/terms").status_code == 200
BANK = {"enabled": True, "bank_bin": "970407", "bank_name": "", "account_number": "0123456789",
        "account_name": "REELFORGE TEST", "transfer_prefix": "RF", "note": "", "sla_message": "", "use_for_vietqr": True}
assert client.put("/api/admin/payment-config/bank_qr", json=BANK).status_code == 200
assert client.put("/api/admin/plans/standard", json={"name": "Standard", "project_limit": 20, "workflow_limit": 10,
                                                     "monthly_credits": 100, "is_active": True,
                                                     "price_vnd": 199000}).status_code == 200
checkout = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert checkout.status_code == 201, checkout.text
assert client.delete("/api/channels/youtube").status_code == 204
assert client.delete("/api/channels/tiktok").status_code == 204
with Session() as db:
    actions = [row.action for row in db.scalars(select(AuditEvent))]
for action in ("account.terms_accepted", "billing.checkout_created", "channel.disconnected"):
    assert action in actions, (action, actions)
    assert action in audit_module.ACTIONS

# A failing email never undoes the payment it announces.
with Session.begin() as db:
    system_config.save(db, None, values={"email.enabled": True, "email.provider": "smtp",
                                         "email.from_email": "studio@example.com", "email.smtp.host": "127.0.0.1"},
                       section="email")
mailer.AUTO_DELIVER = False
def broken(*args, **kwargs):
    raise secret_box.SecretBoxError("encryption unavailable")
encrypt = secret_box.encrypt_json
secret_box.encrypt_json = broken
order = checkout.json()["order_id"]
with Session() as db:
    amount = db.get(PaymentOrder, order).amount_vnd
r = client.post(f"/api/admin/payments/{order}/confirm", json={"amount_vnd": amount})
secret_box.encrypt_json = encrypt
assert r.status_code == 200 and r.json()["status"] == "paid", r.text
with Session() as db:
    assert db.get(PaymentOrder, order).status == "paid"
    assert not db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.template == "payment_succeeded"))

# Readiness: a stored secret that does not decrypt with this key makes the API unready.
with Session.begin() as db:
    db.add(SystemConfig(key="ai.openai.api_key", ciphertext="not-a-valid-token", updated_at=datetime.now(timezone.utc)))
r = client.get("/health/ready")
assert r.status_code == 503 and r.json()["checks"]["master_key"]["problem"] == "cannot_decrypt", r.text
with Session.begin() as db:
    db.delete(db.get(SystemConfig, "ai.openai.api_key"))
assert client.get("/health/ready").status_code == 200

# Backups refuse a relative directory in the settings (and, on Linux, a system or shared one) before touching anything.
with Session.begin() as db:
    system_config.save(db, None, values={"backups.directory": "relative-backups"}, section="backups")
result = backup.run()
assert result["ok"] is False and "absolute" in result["error"] and not Path("relative-backups").exists(), result
assert backup.directory_problem(Path(os.path.abspath("instance/backups"))) is None
if os.name == "posix":
    assert backup.directory_problem(Path("/srv/data")) and backup.directory_problem(Path("/etc/"))
print("ok")
'''

SETUP = r'''
from app.models import AuditEvent
INTERNET = "93.184.216.34"
proxy = TestClient(app, headers=ORIGIN, client=("127.0.0.1", 50000))
admin = {"email": "first@example.com", "password": "first-password-123", "accept_terms": True}

# Through Cloudflare (a trusted proxy carrying a public address): no first administrator from there.
status = proxy.get("/api/status", headers={"CF-Connecting-IP": INTERNET}).json()
assert status["setup_required"] is True and status["setup_here"] is False, status
r = proxy.post("/api/setup", json=admin, headers={"CF-Connecting-IP": INTERNET})
assert r.status_code == 403 and r.json()["detail"] == "Create the first administrator on the server", r.text
# Nor from the internet directly.
outside = TestClient(app, headers=ORIGIN, client=(INTERNET, 50001))
assert outside.post("/api/setup", json=admin).status_code == 403
# Readiness says setup is open, without failing.
ready = proxy.get("/health/ready").json()
assert ready["status"] == "ok" and ready["warnings"] == ["setup_open"], ready

# On the server itself (npm run create-admin, or an SSH tunnel): allowed.
local = TestClient(app, headers=ORIGIN, client=("127.0.0.1", 50002))
assert local.get("/api/status").json()["setup_here"] is True
assert local.post("/api/setup", json=admin).status_code == 200
assert proxy.post("/api/setup", json=admin, headers={"CF-Connecting-IP": INTERNET}).status_code == 409
assert proxy.get("/health/ready").json()["warnings"] == []
with Session() as db:
    denied = [row for row in db.scalars(select(AuditEvent).where(AuditEvent.action == "account.registered",
                                                                 AuditEvent.outcome == "denied"))]
assert len(denied) == 2 and {row.ip for row in denied} == {INTERNET}
print("ok")
'''

RECOVERY = r'''
import io, sys
from app import account_recovery, totp
from app.models import AuditEvent, LoginSession, User
ORIGIN = {"Origin": "http://testserver"}

# The administrator turns 2FA on, then loses the phone and the recovery codes.
setup = client.post("/api/account/2fa/setup", json={"password": "long-password-123"}).json()
assert client.post("/api/account/2fa/enable", json={"code": totp.code_at(setup["secret"], totp.step_at())}).status_code == 200
other = TestClient(app, headers=ORIGIN)
assert other.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"}).json()["two_factor_required"] is True

assert account_recovery.reset_two_factor("nobody@example.com") == 1
assert account_recovery.reset_two_factor("OWNER@example.com") == 0
assert client.get("/api/dashboard").status_code == 401  # every session signed out
assert other.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"}).json()["two_factor_required"] is False
assert account_recovery.reset_two_factor("owner@example.com") == 0  # already off: nothing to do

# ... and forgets the password while email does not work.
assert account_recovery.reset_password("owner@example.com", "short") == 1
assert account_recovery.reset_password("owner@example.com", "owner@example.com") == 1
sys.stdin = io.StringIO("recovered-password-456\n")
assert account_recovery.main(["reset-password", "--email", "owner@example.com", "--password-stdin"]) == 0
assert other.get("/api/dashboard").status_code == 401
fresh = TestClient(app, headers=ORIGIN)
assert fresh.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"}).status_code == 401
assert fresh.post("/api/login", json={"email": "owner@example.com", "password": "recovered-password-456"}).status_code == 200
with Session() as db:
    events = [(row.action, json.loads(row.details_json)) for row in db.scalars(select(AuditEvent))
              if row.action in ("admin.user_password_reset", "admin.user_two_factor_reset")]
assert [action for action, _ in events] == ["admin.user_two_factor_reset", "admin.user_password_reset"], events
assert all(details["via"] == "server_command" for _, details in events)
assert "recovered-password" not in json.dumps(events)
print("ok")
'''


class IsolationTest(unittest.TestCase):
    def test_another_studios_records_answer_404_and_stay_unchanged(self):
        result = run_program(ISOLATION)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


class RolesTest(unittest.TestCase):
    def test_viewer_editor_admin_owner_and_removed_member(self):
        result = run_program(ROLES)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


class SecurityFixesTest(unittest.TestCase):
    def test_timing_headers_metrics_audit_email_readiness_backups(self):
        result = run_program(SECURITY)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])

    def test_first_administrator_only_from_the_server(self):
        result = run_fresh(SETUP)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])

    def test_break_glass_recovery_command(self):
        result = run_program(RECOVERY)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


class UnitTest(unittest.TestCase):
    def test_public_addresses(self):
        for address, public in (("93.184.216.34", True), ("2606:4700::1111", True), ("127.0.0.1", False),
                                ("10.1.2.3", False), ("192.168.1.10", False), ("::1", False), ("fd00::1", False),
                                ("198.51.100.7", False), ("::ffff:93.184.216.34", True), ("testclient", False),
                                (None, False)):
            with self.subTest(address=address):
                self.assertIs(client_ip.is_public(address), public)

    def test_metric_method_label_is_bounded(self):
        metrics.observe_http("PROPFIND", "unmatched", 405, 0.01)
        metrics.observe_http("GET", "/api/status", 200, 0.01)
        text = metrics.render()
        self.assertIn('method="OTHER"', text)
        self.assertNotIn("PROPFIND", text)


class DeploymentTest(unittest.TestCase):
    def test_deploy_surfaces_failed_services_and_open_setup(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn('systemctl is-active --quiet "${unit}"', script)
        self.assertIn("FAILED: these services are not running", script)
        self.assertLess(script.index("FAILED: these services are not running"),
                        script.index("ReelForge deployment completed OK"))
        self.assertIn('"setup_open" in json.load(sys.stdin).get("warnings", [])', script)
        self.assertIn("npm run create-admin", script)
        self.assertIn("curl -fsSI http://127.0.0.1:3001", script)  # retried, not a single attempt
        self.assertRegex(script, r"for _ in \$\(seq 1 30\); do\n  if curl -fsSI http://127\.0\.0\.1:3001")
        result = subprocess.run(["bash", "-n", str(ROOT / "deploy.sh")], capture_output=True, text=True) \
            if shutil.which("bash") else None
        if result is not None:
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ci_needs_no_secret_and_tests_the_production_versions(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("secrets.", ci)
        self.assertNotIn("REELFORGE_LIVE_TESTS", ci.replace("REELFORGE_LIVE_TESTS=1, which CI never sets", ""))
        self.assertIn("python: ['3.11', '3.14']", ci)
        self.assertIn("node: ['20', '22']", ci)
        for action in ("actions/checkout@v7", "actions/setup-python@v7", "actions/setup-node@v7",
                       "actions/upload-artifact@v7"):
            self.assertIn(action, ci)
        self.assertIn("alembic check", ci)
        self.assertIn("REELFORGE_TEST_DATABASE_URL", ci)

    def test_units_keep_storage_and_the_master_key_reachable(self):
        for unit in (ROOT / "deploy" / "systemd").glob("*.service"):
            text = unit.read_text(encoding="utf-8")
            with self.subTest(unit=unit.name):
                self.assertIn("ProtectSystem=full", text)  # /etc readable (master key), /srv and /home writable
                self.assertNotIn("ProtectSystem=strict", text)
                self.assertNotIn("ProtectHome=", text)
                self.assertIn("NoNewPrivileges=yes", text)
                self.assertIn("EnvironmentFile=-", text) if "EnvironmentFile" in text else None


if __name__ == "__main__":
    unittest.main()
