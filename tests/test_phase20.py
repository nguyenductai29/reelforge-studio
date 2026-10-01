"""Phase 20: centralized system configuration (database over environment, encrypted secrets, workers without
restarts) and manual VietQR payments confirmed by a system admin.

Offline: AI connection tests answer through httpx.MockTransport, payOS is a fake SDK class, and no QR image
service, bank or provider is contacted. Sentinel secrets must never leave the server.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

from app import bank_qr, master_key, system_config

SENTINELS = ("DO_NOT_LEAK_GEMINI_KEY_1", "DO_NOT_LEAK_OPENAI_KEY_2", "DO_NOT_LEAK_RUNWAY_SECRET_3",
             "DO_NOT_LEAK_GOOGLE_SECRET_4", "DO_NOT_LEAK_TIKTOK_SECRET_5", "DO_NOT_LEAK_FACEBOOK_SECRET_6")


class MasterKeyTest(unittest.TestCase):
    """Resolution order and safety, without a database."""

    def test_file_wins_and_an_explicit_missing_file_never_falls_back(self):
        legacy, other = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "master.key"
            key_file.write_text(other + "\n")
            with patch.dict(os.environ, {master_key.FILE_ENV: str(key_file), master_key.LEGACY_ENV: legacy}):
                self.assertEqual(master_key.load(), other)
                status = master_key.status()
                self.assertEqual((status["source"], status["legacy_env_matches"]), ("file", False))
                self.assertNotIn(other, repr(status))
            with patch.dict(os.environ, {master_key.FILE_ENV: str(Path(directory) / "absent.key"),
                                         master_key.LEGACY_ENV: legacy}):
                self.assertEqual(master_key.load(), "")
                self.assertEqual(master_key.status()["problem"], "missing")
            key_file.write_text("not-a-key")
            with patch.dict(os.environ, {master_key.FILE_ENV: str(key_file)}):
                self.assertEqual(master_key.resolve()["problem"], "invalid")

    def test_legacy_variable_still_works_and_init_copies_it(self):
        legacy = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(master_key, "PRODUCTION_FILE", Path(directory) / "none.key"), \
                patch.object(master_key, "DEVELOPMENT_FILE", Path(directory) / "dev.key"), \
                patch.dict(os.environ, {master_key.LEGACY_ENV: legacy}):
            os.environ.pop(master_key.FILE_ENV, None)
            self.assertEqual((master_key.load(), master_key.status()["source"]), (legacy, "legacy_env"))
            target = Path(directory) / "etc" / "master.key"
            lines = []
            self.assertEqual(master_key.init(target, out=lines.append), 0)
            self.assertEqual(target.read_text().strip(), legacy)
            self.assertNotIn(legacy, " ".join(lines))
            if os.name == "posix":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_no_key_is_generated_over_encrypted_data(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(master_key, "PRODUCTION_FILE", Path(directory) / "none.key"), \
                patch.object(master_key, "DEVELOPMENT_FILE", Path(directory) / "dev.key"), \
                patch.object(master_key, "encrypted_data_exists", lambda: True), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop(master_key.FILE_ENV, None)
            os.environ.pop(master_key.LEGACY_ENV, None)
            target = Path(directory) / "master.key"
            self.assertEqual(master_key.init(target, out=lambda _: None), 1)
            self.assertFalse(target.exists())
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(master_key, "PRODUCTION_FILE", Path(directory) / "none.key"), \
                patch.object(master_key, "DEVELOPMENT_FILE", Path(directory) / "dev.key"), \
                patch.object(master_key, "encrypted_data_exists", lambda: False):
            os.environ.pop(master_key.LEGACY_ENV, None)
            target = Path(directory) / "master.key"
            self.assertEqual(master_key.init(target, out=lambda _: None), 0)
            Fernet(target.read_text().strip().encode())


class VietQRPayloadTest(unittest.TestCase):
    def test_crc_matches_the_ccitt_false_check_value(self):
        self.assertEqual(bank_qr.crc16("123456789"), "29B1")

    def test_payload_names_bank_account_exact_amount_and_order_content(self):
        data = bank_qr.payload("970407", "151018020996", 199000, "RF1234567890123")
        self.assertTrue(data.startswith("000201010212"))
        self.assertIn("0010A000000727", data)
        self.assertIn("0006970407" + "0112151018020996", data)
        self.assertIn("0208QRIBFTTA", data)
        self.assertIn("5303704" + "5406199000" + "5802VN", data)
        self.assertIn("62190815RF1234567890123", data)
        self.assertEqual(data[-8:-4], "6304")
        self.assertEqual(data[-4:], bank_qr.crc16(data[:-4]))
        self.assertTrue(bank_qr.image(data).startswith("data:image/svg+xml"))
        for bad in (("97040", "151018020996", 1, "RF1"), ("970407", "15 10", 1, "RF1"),
                    ("970407", "151018020996", 0, "RF1"), ("970407", "151018020996", 1, "RF 1")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bank_qr.payload(*bad)

    def test_problems_name_missing_fields(self):
        self.assertEqual(bank_qr.problems({}), ["bank_bin", "account_number", "account_name", "transfer_prefix"])
        self.assertEqual(bank_qr.problems({"bank_bin": "970407", "account_number": "151018020996",
                                           "account_name": "NGUYEN DUC TAI", "transfer_prefix": "RF"}), [])


class InactiveConfigTest(unittest.TestCase):
    def test_library_code_without_activation_reads_the_environment_only(self):
        self.assertFalse(system_config.active())
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key", "VIDEO_CREDITS_PER_CLIP": "12"}):
            self.assertEqual(system_config.env("GEMINI_API_KEY"), "env-key")
            self.assertEqual(system_config.get("credits.video_per_clip"), 12)
            self.assertEqual(system_config.source("credits.video_per_clip"), "environment")

    def test_every_legacy_variable_maps_to_one_setting(self):
        names = [setting.env for setting in system_config.SETTINGS if setting.env]
        self.assertEqual(len(names), len(set(names)))
        for setting in system_config.SETTINGS:
            self.assertIn(setting.section, system_config.SECTIONS)
            self.assertLessEqual(len(setting.key), 80)


COMMON = r'''
import io
import tempfile
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch
import payos as payos_sdk
import app.db as app_db
from app import (bank_qr, config_checks, master_key, notifications, payment_providers, readiness, secret_box, storage,
                 system_config)
from app.models import (CreditLedger, Notification, PaymentOrder, PaymentOrderEvent, Subscription, SystemConfig,
                        SystemConfigAudit)
from app.providers.text import create_text_provider, text_provider_config_issue
from app.providers.catalog import video_credit_cost, video_provider_config_issue
from app.publishers import channel_oauth, google_oauth

SENTINELS = ("DO_NOT_LEAK_GEMINI_KEY_1", "DO_NOT_LEAK_OPENAI_KEY_2", "DO_NOT_LEAK_RUNWAY_SECRET_3",
             "DO_NOT_LEAK_GOOGLE_SECRET_4", "DO_NOT_LEAK_TIKTOK_SECRET_5", "DO_NOT_LEAK_FACEBOOK_SECRET_6")
log_buffer = io.StringIO()
handler = logging.StreamHandler(log_buffer)
handler.setLevel(logging.DEBUG)
logging.getLogger("app").addHandler(handler)
logging.getLogger("app").setLevel(logging.DEBUG)

def clean(text):
    for value in SENTINELS:
        assert value not in text, value

def no_network(request):
    raise AssertionError(f"unexpected request to {request.url.host}")
config_checks.http_client = lambda: httpx.Client(transport=httpx.MockTransport(no_network))
payment_providers.http_client = lambda: httpx.Client(transport=httpx.MockTransport(no_network))

def overview(c=None):
    response = (c or client).get("/api/admin/system-config")
    assert response.status_code == 200, response.text
    clean(response.text)
    return response.json()

def setting(key):
    section = key.split(".")[0]
    return {item["key"]: item for item in overview()["sections"][section]}[key]

def put(section, c=None, **body):
    return (c or client).put(f"/api/admin/system-config/{section}", json=body)

def replace(value):
    return {"action": "replace", "value": value}

other = TestClient(app)
assert other.post("/api/register", json={"email": "owner2@example.com", "password": "long-password-123",
                                         "workspace_name": "Studio khác"}).status_code == 201
other_ws = other.get("/api/dashboard").json()["workspace"]["id"]
'''

CONFIG = COMMON + r'''
def token_readable():
    with Session() as db:
        row = db.scalar(select(YouTubeConnection))
        return Fernet(master_key.load().encode()).decrypt(row.access_token_ciphertext.encode()) == b"tok-yt-access"

# Only system admins.
assert other.get("/api/admin/system-config").status_code == 403
denied = put("ai", other, secrets={"ai.gemini.api_key": replace("DO_NOT_LEAK_GEMINI_KEY_1")})
assert denied.status_code == 403
clean(denied.text)
assert other.post("/api/admin/system-config/ai/gemini/test").status_code == 403
assert other.post("/api/admin/system-config/storage/check", json={"root": "/tmp"}).status_code == 403
state = overview()
assert set(state["sections"]) == {"ai", "social", "storage", "runtime", "credits", "notifications"}
assert state["master_key"]["source"] == "legacy_env" and state["master_key"]["encryption_available"]

# Environment fallback, then the admin value wins at once: no restart.
os.environ["GEMINI_API_KEY"] = "env-gemini-key"
assert setting("ai.gemini.api_key")["source"] == "environment" and setting("ai.gemini.api_key")["configured"]
assert system_config.env("GEMINI_API_KEY") == "env-gemini-key"
saved = put("ai", secrets={"ai.gemini.api_key": replace("DO_NOT_LEAK_GEMINI_KEY_1"),
                           "ai.openai.api_key": replace("DO_NOT_LEAK_OPENAI_KEY_2")})
assert saved.status_code == 200, saved.text
clean(saved.text)
gemini = setting("ai.gemini.api_key")
assert (gemini["source"], gemini["configured"], gemini["updated_by"]) == ("admin", True, "owner@example.com")
assert "value" not in gemini
assert system_config.env("GEMINI_API_KEY") == "DO_NOT_LEAK_GEMINI_KEY_1"
# Workers build providers from the stored key.
assert text_provider_config_issue("openai") is None
assert create_text_provider("openai")._api_key == "DO_NOT_LEAK_OPENAI_KEY_2"
with Session() as db:
    raw = repr(db.execute(text("SELECT * FROM system_config")).all())
    audit = repr(db.execute(text("SELECT * FROM system_config_audit")).all())
clean(raw)
clean(audit)
assert "ai.gemini.api_key" in audit

# A provider the admin switches off has no key, whatever the environment says.
assert put("ai", values={"ai.gemini.enabled": False}).status_code == 200
assert system_config.env("GEMINI_API_KEY") == "" and text_provider_config_issue("gemini")[0] == "missing_key"
assert put("ai", values={"ai.gemini.enabled": True}).status_code == 200
# Clearing the stored key falls back to the environment again.
assert put("ai", secrets={"ai.gemini.api_key": {"action": "clear"}}).status_code == 200
assert system_config.env("GEMINI_API_KEY") == "env-gemini-key"

# Runway: secret and output hosts from the admin UI.
assert video_provider_config_issue("runway")[0] == "missing_key"
assert put("ai", secrets={"ai.runway.api_secret": replace("DO_NOT_LEAK_RUNWAY_SECRET_3")},
           values={"ai.runway.output_hosts": "dnznrvs05pmza.cloudfront.net"}).status_code == 200
assert video_provider_config_issue("runway") is None
assert setting("ai.runway.output_hosts")["value"] == "dnznrvs05pmza.cloudfront.net"

# Test connection: one read-only listing request with the stored key; the answer is reduced to a status.
seen = []
def listing(request):
    seen.append((request.method, str(request.url), request.headers.get("authorization")))
    return httpx.Response(200 if len(seen) == 1 else 401, json={"data": ["DO_NOT_LEAK_OPENAI_KEY_2"]})
with patch.object(config_checks, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(listing))):
    ok = client.post("/api/admin/system-config/ai/openai/test")
    refused = client.post("/api/admin/system-config/ai/openai/test")
assert ok.json()["remote"] == {"status": "ok"} and refused.json()["remote"] == {"status": "error", "code": "unauthorized"}
assert seen[0] == ("GET", "https://api.openai.com/v1/models", "Bearer DO_NOT_LEAK_OPENAI_KEY_2")
clean(ok.text + refused.text)
assert client.post("/api/admin/system-config/ai/fal/test").json()["local"] == {"status": "error", "code": "key_missing"}

# Validation: ranges, types, unknown keys, secrets sent as plain values; errors never echo input.
for body, code in (({"values": {"credits.video_per_clip": 0}}, "out_of_range"),
                   ({"values": {"credits.video_per_clip": "ten"}}, "invalid_value"),
                   ({"values": {"credits.unknown": 1}}, "unknown_setting"),
                   ({"values": {"ai.gemini.api_key": "DO_NOT_LEAK_GEMINI_KEY_1"}}, "invalid_request")):
    section = "credits" if "credits" in json.dumps(body) else "ai"
    refused = client.put(f"/api/admin/system-config/{section}", json=body)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == code, (body, refused.text)
    clean(refused.text)
wrong_section = put("ai", values={"credits.video_per_clip": 5})
assert wrong_section.status_code == 422 and wrong_section.json()["detail"]["code"] == "unknown_setting"
typed = put("ai", secrets={"ai.gemini.api_key": {"action": "replace", "value": ["DO_NOT_LEAK_GEMINI_KEY_1"]}})
assert typed.status_code == 422
clean(typed.text)

# Credits and runtime: workers see a change at once in this process, and within the cache time elsewhere.
assert put("credits", values={"credits.video_per_clip": 25}).status_code == 200
assert video_credit_cost() == 25 and setting("credits.video_per_clip")["source"] == "admin"
with Session.begin() as db:
    db.execute(text("UPDATE system_config SET value = '30' WHERE key = 'credits.video_per_clip'"))
assert video_credit_cost() == 25  # another process's change: cached for a few seconds
with system_config._lock:
    system_config._cache["at"] -= system_config.CACHE_SECONDS + 1
assert video_credit_cost() == 30
assert put("runtime", values={"runtime.render_timeout_seconds": 900, "runtime.still_seconds": 7.5}).status_code == 200
from app import render
assert render.render_timeout_seconds() == 900 and render.still_seconds() == 7.5
assert put("notifications", values={"notifications.credits_low_threshold": 55,
                                    "notifications.sse_poll_seconds": 1.5}).status_code == 200
assert notifications.low_credit_threshold() == 55 and main._sse_settings()[0] == 1.5
# Resetting a value returns to the environment or the default.
assert put("credits", reset=["credits.video_per_clip"]).status_code == 200
assert video_credit_cost() == 10 and setting("credits.video_per_clip")["source"] == "default"

# Social OAuth apps: redirect URLs derived from frontend_origin, secrets write-only, tokens untouched.
connect_youtube()
assert put("social", values={"social.youtube.client_id": "admin-client.apps.googleusercontent.com",
                             "social.youtube.redirect_uri": ""},
           secrets={"social.youtube.client_secret": replace("DO_NOT_LEAK_GOOGLE_SECRET_4")}).status_code == 200
os.environ.pop("GOOGLE_OAUTH_REDIRECT_URI")
youtube = google_oauth.GoogleOAuthConfig.from_environment()
assert (youtube.client_id, youtube.client_secret) == ("admin-client.apps.googleusercontent.com",
                                                      "DO_NOT_LEAK_GOOGLE_SECRET_4")
assert youtube.redirect_uri == "http://localhost:3000/youtube/callback"
assert overview()["redirects"]["youtube"] == "http://localhost:3000/youtube/callback"
assert token_readable()  # existing tokens are untouched and still decrypt
for channel, values, secret in (("tiktok", {"social.tiktok.client_key": "admin-tiktok"}, "DO_NOT_LEAK_TIKTOK_SECRET_5"),
                                ("facebook", {"social.facebook.app_id": "99887766"}, "DO_NOT_LEAK_FACEBOOK_SECRET_6")):
    os.environ.pop(f"{channel.upper()}_REDIRECT_URI")
    key = f"social.{channel}.client_secret" if channel == "tiktok" else "social.facebook.app_secret"
    assert put("social", values=values, secrets={key: replace(secret)}).status_code == 200
    config = channel_oauth.ChannelConfig.from_environment(channel)
    assert config.client_secret == secret and config.redirect_uri == f"http://localhost:3000/channels/callback/{channel}"
    assert config.client_id == list(values.values())[0]
assert put("social", values={"social.tiktok.approved_scopes": "user.info.basic,video.upload,video.publish"}).status_code == 200
assert system_config.env("TIKTOK_APPROVED_SCOPES") == "user.info.basic,video.upload,video.publish"
assert client.get("/api/admin/readiness").status_code == 200

# Storage: validated root, no silent move of existing files, retention and the quota ceiling.
upload("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")
new_root = Path(tempfile.mkdtemp(prefix="rf-root-"))
for raw, problem in (("relative/path", "not_absolute"), (str(new_root / "missing"), "missing")):
    bad = put("storage", values={"storage.root": raw})
    assert bad.status_code == 422 and bad.json()["detail"]["problem"] == problem, bad.text
checked = client.post("/api/admin/system-config/storage/check", json={"root": str(new_root)}).json()
assert checked["ok"] and checked["moves"] and checked["files"] == 1
needs_confirm = put("storage", values={"storage.root": str(new_root)})
assert needs_confirm.status_code == 409 and needs_confirm.json()["detail"]["files"] == 1
old_root = overview()["storage"]["root"]
assert put("storage", values={"storage.root": str(new_root)}, confirm_root_change=True).status_code == 200
state = overview()["storage"]
assert state["root"] == str(new_root) and state["source"] == "admin" and old_root != str(new_root)
with Session() as db:
    assert storage.media_root(db) == new_root
assert Path(old_root).exists()  # nothing was moved or deleted
assert put("storage", values={"storage.retention_intermediate_days": 45, "storage.quota_ceiling_bytes": 5000000}
           ).status_code == 200
assert storage.RetentionPolicy.from_environment().intermediate_days == 45 and storage.server_cap() == 5000000

# Without the master key: nothing secret is saved, stored secrets are unreadable (no fallback), nothing is lost.
key = os.environ.pop("REELFORGE_TOKEN_ENCRYPTION_KEY")
missing = put("ai", secrets={"ai.anthropic.api_key": replace("DO_NOT_LEAK_GEMINI_KEY_1")})
assert missing.status_code == 422 and missing.json()["detail"]["code"] == "key_missing"
clean(missing.text)
assert overview()["master_key"]["problem"] == "missing"
system_config.invalidate()
assert system_config.env("OPENAI_API_KEY") == "" and setting("ai.openai.api_key")["source"] == "error"
security = {c["key"]: c for s in client.get("/api/admin/readiness").json()["sections"] if s["key"] == "security"
            for c in s["checks"]}
assert security["master_key"]["status"] == "error"
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = key
system_config.invalidate()
assert system_config.env("OPENAI_API_KEY") == "DO_NOT_LEAK_OPENAI_KEY_2"
# The same key moved into a file (the production layout) keeps everything readable.
key_file = Path(tempfile.mkdtemp()) / "master.key"
key_file.write_text(key)
os.environ["REELFORGE_MASTER_KEY_FILE"] = str(key_file)
os.environ.pop("REELFORGE_TOKEN_ENCRYPTION_KEY")
system_config.invalidate()
assert overview()["master_key"]["source"] == "file"
assert system_config.env("OPENAI_API_KEY") == "DO_NOT_LEAK_OPENAI_KEY_2"
assert token_readable()

# Nothing secret anywhere admins or users look.
for path in ("/api/admin", "/api/admin/readiness", "/api/admin/payment-config", "/api/settings", "/api/billing"):
    clean(client.get(path).text)
clean(log_buffer.getvalue())
print("config ok")
'''

MANUAL = COMMON + r'''
price = lambda credits, vnd: {"name": "Plan", "project_limit": None, "workflow_limit": None, "monthly_credits": credits,
                              "is_active": True, "price_vnd": vnd}
assert client.put("/api/admin/plans/standard", json={**price(5, 30000), "name": "Standard"}).status_code == 200
assert client.put("/api/admin/plans/pro", json={**price(10, 50000), "name": "Pro"}).status_code == 200
BANK = {"enabled": True, "bank_bin": "970407", "bank_name": "", "account_number": "151018020996",
        "account_name": "Nguyen Duc Tai", "transfer_prefix": "rf", "note": "Ghi đúng nội dung.",
        "sla_message": "Xác nhận trong 2 giờ làm việc."}

def credits(workspace_id):
    with Session() as db:
        return [(row.delta, row.reference) for row in db.scalars(
            select(CreditLedger).where(CreditLedger.workspace_id == workspace_id,
                                       CreditLedger.reference.like("payment:%")))]

def methods(c=None):
    return (c or client).get("/api/billing").json()["methods"]

# Admin only; incomplete details refused; a preview for the values on screen.
assert other.put("/api/admin/payment-config/bank_qr", json=BANK).status_code == 403
assert other.post("/api/admin/payment-config/bank_qr/preview", json=BANK).status_code == 403
incomplete = client.put("/api/admin/payment-config/bank_qr", json={**BANK, "account_number": ""})
assert incomplete.status_code == 422 and incomplete.json()["detail"]["field"] == "payments.bank_qr.account_number"
preview = client.post("/api/admin/payment-config/bank_qr/preview", json=BANK).json()
assert preview["content"] == "RFTEST01" and preview["amount_vnd"] == 100000 and preview["qr"].startswith("data:image/svg")
assert preview["bank_name"] == "Techcombank" and preview["payload"][-4:] == bank_qr.crc16(preview["payload"][:-4])
saved = client.put("/api/admin/payment-config/bank_qr", json=BANK)
assert saved.status_code == 200, saved.text
view = saved.json()
assert view["available"] and view["values"]["account_name"] == "NGUYEN DUC TAI" and view["values"]["transfer_prefix"] == "RF"
assert client.get("/api/admin/payment-config").json()["vietqr_mode"] == "manual"
assert methods() == [{"id": "vietqr", "provider": "bank_qr"}] and methods(other) == methods()

# Checkout: a QR in ReelForge for the exact price, with content unique to the order; nothing is paid.
first = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert first.status_code == 201, first.text
body = first.json()
transfer = body["transfer"]
order = Session().get(PaymentOrder, body["order_id"])
assert body["checkout_url"] is None and order.provider == "bank_qr" and order.status == "pending"
assert transfer["amount_vnd"] == 30000 and transfer["content"] == f"RF{order.order_code}"
assert transfer["account_number"] == "151018020996" and transfer["account_name"] == "NGUYEN DUC TAI"
assert "5405" + "30000" in transfer["payload"] and f"08{len(transfer['content']):02d}{transfer['content']}" in transfer["payload"]
assert transfer["payload"][-4:] == bank_qr.crc16(transfer["payload"][:-4])
second = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "vietqr"}).json()
assert second["transfer"]["content"] != transfer["content"] and second["transfer"]["amount_vnd"] == 50000
assert credits(workspace) == [] and client.get("/api/dashboard").json()["workspace"]["plan"] == "trial"
again = client.get(f"/api/billing/orders/{body['order_id']}/transfer").json()
assert again["transfer"]["content"] == transfer["content"] and again["order"]["transfer_content"] == transfer["content"]
# Another studio sees nothing of it.
assert other.get(f"/api/billing/orders/{body['order_id']}/transfer").status_code == 404
assert other.post(f"/api/billing/orders/{body['order_id']}/transferred").status_code == 404

# The buyer reports the transfer; admins are told; reporting twice changes nothing.
reported = client.post(f"/api/billing/orders/{body['order_id']}/transferred")
assert reported.status_code == 200 and reported.json()["transfer_reported_at"] and reported.json()["status"] == "pending"
assert client.post(f"/api/billing/orders/{body['order_id']}/transferred").status_code == 200
with Session() as db:
    kinds = [row.type for row in db.scalars(select(Notification).where(Notification.type == "payment.transfer_reported"))]
    events = db.scalars(select(PaymentOrderEvent).where(PaymentOrderEvent.order_id == body["order_id"])).all()
assert kinds == ["payment.transfer_reported"] and [e.action for e in events] == ["transfer_reported"]
awaiting = client.get("/api/admin/payments", params={"status": "awaiting_confirmation"}).json()
assert [item["id"] for item in awaiting["items"]] == [body["order_id"]] and awaiting["items"][0]["transfer_content"]
assert client.get("/api/admin").json()["counts"]["transfers_to_confirm"] == 1
# Check (refresh) asks nobody and changes nothing for a manual order.
assert client.post(f"/api/billing/orders/{body['order_id']}/refresh").json()["status"] == "pending"

# Only a system admin confirms, for the exact amount, once.
assert other.post(f"/api/admin/payments/{body['order_id']}/confirm", json={"amount_vnd": 30000}).status_code == 403
wrong = client.post(f"/api/admin/payments/{body['order_id']}/confirm", json={"amount_vnd": 29000})
assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "amount_mismatch"
confirmed = client.post(f"/api/admin/payments/{body['order_id']}/confirm", json={"amount_vnd": 30000})
assert confirmed.status_code == 200 and confirmed.json()["status"] == "paid", confirmed.text
assert credits(workspace) == [(5, f"payment:{body['order_id']}")]
assert client.get("/api/dashboard").json()["workspace"]["plan"] == "standard"
assert client.post(f"/api/admin/payments/{body['order_id']}/confirm", json={"amount_vnd": 30000}).status_code == 409
assert credits(workspace) == [(5, f"payment:{body['order_id']}")]
history = client.get(f"/api/admin/payments/{body['order_id']}/events").json()["items"]
assert [(e["action"], e["by"], e["amount_vnd"]) for e in history] == [
    ("transfer_reported", "owner@example.com", 30000), ("confirmed", "owner@example.com", 30000)]
assert client.post(f"/api/admin/payments/{body['order_id']}/reject", json={}).status_code == 409

# Reject: the order fails and the owner is told; money found later can still be confirmed, once.
theirs = other.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"}).json()
other.post(f"/api/billing/orders/{theirs['order_id']}/transferred")
rejected = client.post(f"/api/admin/payments/{theirs['order_id']}/reject", json={"note": "Không thấy giao dịch"})
assert rejected.status_code == 200 and rejected.json()["status"] == "failed"
assert "payment.failed" in [n["type"] for n in other.get("/api/notifications").json()["items"]]
assert client.post(f"/api/admin/payments/{theirs['order_id']}/confirm", json={"amount_vnd": 30000}).json()["status"] == "paid"
assert credits(other_ws) == [(5, f"payment:{theirs['order_id']}")]
# Other providers' orders are never confirmed by hand.
with Session.begin() as db:
    db.add(PaymentOrder(id="card-order", workspace_id=other_ws, plan_code="standard", provider="onepay",
                        order_code=4444444444444, amount_vnd=30000, credits_award=5, status="pending",
                        created_at=datetime.now(timezone.utc)))
assert client.post("/api/admin/payments/card-order/confirm", json={"amount_vnd": 30000}).status_code == 409

# Disabling manual VietQR stops new checkouts; the pending manual order can still be confirmed.
assert client.post("/api/admin/payment-config/bank_qr/disable").status_code == 200
assert methods() == []
assert client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "vietqr"}).status_code == 503
assert client.post("/api/admin/payment-config/bank_qr/enable", json={}).status_code == 200

# Automatic payOS mode: unchanged checkout and webhook; switching keeps the pending manual order confirmable.
app_db.config["payos"] = {"client_id": "legacy-client-0001", "api_key": "legacy-api", "checksum_key": "legacy-sum"}
billing = __import__("app.billing", fromlist=["billing"])
billing.create_link = lambda code, amount, plan, origin: f"https://pay.payos.vn/web/{code}"
switched = client.put("/api/admin/payment-config/payos", json={"enabled": True, "use_for_vietqr": True})
assert switched.status_code == 200 and switched.json()["available"], switched.text
assert methods() == [{"id": "vietqr", "provider": "payos"}]
hosted = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "vietqr"})
assert hosted.status_code == 201 and hosted.json()["checkout_url"].startswith("https://pay.payos.vn/")
payos_order = Session().get(PaymentOrder, hosted.json()["order_id"])
billing.verify_webhook = lambda body: SimpleNamespace(order_code=payos_order.order_code, amount=50000, currency="VND",
                                                      reference="payos-ref")
assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 200
assert Session().get(PaymentOrder, payos_order.id).status == "paid"
assert client.post(f"/api/admin/payments/{second['order_id']}/confirm", json={"amount_vnd": 50000}).status_code == 200

# Cards: the OnePAY environment configuration still works next to all this.
os.environ.update({"ONEPAY_MERCHANT_ID": "TESTONEPAY", "ONEPAY_ACCESS_CODE": "6BEB2546",
                   "ONEPAY_HASH_KEY": "A3EFDFABA8653DF2342E8DAC29B51AF0",
                   "ONEPAY_PAYMENT_URL": "https://mtf.onepay.vn/paygate/vpcpay.op"})
assert {m["id"] for m in methods()} == {"vietqr", "card"}
card = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "card"})
assert card.status_code == 201 and card.json()["checkout_url"].startswith("https://mtf.onepay.vn/")
clean(log_buffer.getvalue())
print("manual ok")
'''


class Phase20Test(unittest.TestCase):
    def run_body(self, body, marker):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stdout[-3000:] + completed.stderr[-6000:])
        self.assertIn(marker, completed.stdout)
        for value in SENTINELS:
            self.assertNotIn(value, completed.stdout + completed.stderr)

    def test_central_configuration_ai_social_storage_and_the_master_key(self):
        self.run_body(CONFIG, "config ok")

    def test_manual_vietqr_is_confirmed_by_an_admin_exactly_once(self):
        self.run_body(MANUAL, "manual ok")


if __name__ == "__main__":
    unittest.main()
