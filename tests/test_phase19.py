"""Phase 19: admin-managed payment gateway configuration (payOS VietQR and OnePAY cards).

Offline: payOS is a fake SDK class and every OnePAY request goes to an httpx.MockTransport, so no
payment, checkout or QueryDR ever leaves the process. Sentinel secrets must never appear in an API
response, an error, a log line, the audit trail or the database outside the ciphertext.
"""
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

PAYOS_SECRET = "DO_NOT_LEAK_PAYOS_SECRET_123"
PAYOS_CHECKSUM = "DO_NOT_LEAK_PAYOS_CHECKSUM_456"
ONEPAY_ACCESS = "DO_NOT_LEAK_ONEPAY_ACCESS_789"
ONEPAY_HASH = "0A1B2C3D4E5F60718293A4B5C6D7E8F9"
ONEPAY_PASSWORD = "DO_NOT_LEAK_ONEPAY_QUERY_PASS"
SENTINELS = (PAYOS_SECRET, PAYOS_CHECKSUM, ONEPAY_ACCESS, ONEPAY_HASH, ONEPAY_PASSWORD, "DO_NOT_LEAK")

COMMON = r'''
import hashlib
import hmac
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import payos as payos_sdk
import app.db as app_db
from app import billing, payment_config, payment_providers, secret_box
from app.payment_providers import onepay
from app.models import (CreditLedger, PaymentConfigAudit, PaymentOrder, PaymentProviderConfig, Subscription,
                        SystemSetting)

PAYOS_SECRET, PAYOS_CHECKSUM = "DO_NOT_LEAK_PAYOS_SECRET_123", "DO_NOT_LEAK_PAYOS_CHECKSUM_456"
ONEPAY_ACCESS, ONEPAY_HASH = "DO_NOT_LEAK_ONEPAY_ACCESS_789", "0A1B2C3D4E5F60718293A4B5C6D7E8F9"
ONEPAY_PASSWORD = "DO_NOT_LEAK_ONEPAY_QUERY_PASS"
SENTINELS = (PAYOS_SECRET, PAYOS_CHECKSUM, ONEPAY_ACCESS, ONEPAY_HASH, ONEPAY_PASSWORD, "DO_NOT_LEAK")

# Every log line of the app, to prove no secret is ever logged.
log_buffer = io.StringIO()
log_handler = logging.StreamHandler(log_buffer)
log_handler.setLevel(logging.DEBUG)
logging.getLogger("app").addHandler(log_handler)
logging.getLogger("app").setLevel(logging.DEBUG)

def clean(text):
    for value in SENTINELS:
        assert value not in text, value

# Nothing may reach a real gateway: any OnePAY request not answered by a test fails loudly.
def no_network(request):
    raise AssertionError(f"unexpected request to {request.url.host}")
payment_providers.http_client = lambda: httpx.Client(transport=httpx.MockTransport(no_network))

# payOS SDK fake: records the credentials it was built with; the webhook is "signed" with the checksum key.
built = []
class FakePayOS:
    def __init__(self, client_id=None, api_key=None, checksum_key=None, **kwargs):
        built.append((client_id, api_key, checksum_key))
        self.checksum_key = checksum_key
        self.payment_requests = SimpleNamespace(
            create=lambda request: SimpleNamespace(checkout_url=f"https://pay.payos.vn/web/{request.order_code}"),
            get=lambda order_code: lookups[order_code])
        self.webhooks = SimpleNamespace(verify=self.verify)

    def verify(self, body):
        payload = json.loads(body)
        expected = hmac.new(self.checksum_key.encode(), json.dumps(payload["data"], sort_keys=True).encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, payload.get("signature", "")):
            raise ValueError("Invalid signature")
        data = payload["data"]
        return SimpleNamespace(order_code=data["orderCode"], amount=data["amount"], currency="VND",
                               reference=data["reference"])
lookups = {}
payos_sdk.PayOS = FakePayOS

def payos_webhook(order_code, amount, checksum, reference="REF"):
    data = {"orderCode": order_code, "amount": amount, "reference": reference}
    signature = hmac.new(checksum.encode(), json.dumps(data, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/payos", content=json.dumps({"success": True, "code": "00", "data": data,
                                                                   "signature": signature}))

keep = {"action": "keep"}
def replace(value):
    return {"action": "replace", "value": value}

def payos_body(enabled=True, **fields):
    return {"enabled": enabled, **fields}

def save(provider, body, c=None):
    return (c or client).put(f"/api/admin/payment-config/{provider}", json=body)

def config(provider):
    response = client.get("/api/admin/payment-config")
    assert response.status_code == 200, response.text
    clean(response.text)
    return {item["provider"]: item for item in response.json()["providers"]}[provider]

def stored(provider):
    with Session() as db:
        row = db.get(PaymentProviderConfig, provider)
        return secret_box.decrypt_json(f"payment-config:{provider}", row.config_ciphertext) if row else None

def audit_trail(provider):
    with Session() as db:
        rows = db.scalars(select(PaymentConfigAudit).where(PaymentConfigAudit.provider == provider)
                          .order_by(PaymentConfigAudit.id)).all()
        return [(row.action, json.loads(row.metadata_json)) for row in rows]

def methods(c=None):
    return [m["id"] for m in (c or client).get("/api/billing").json()["methods"]]

def order(order_id):
    with Session() as db:
        return db.get(PaymentOrder, order_id)

def credits():
    with Session() as db:
        return [(row.delta, row.reference) for row in db.scalars(
            select(CreditLedger).where(CreditLedger.workspace_id == workspace, CreditLedger.reference.like("payment:%")))]

price = lambda credits, vnd: {"name": "Plan", "project_limit": None, "workflow_limit": None, "monthly_credits": credits,
                              "is_active": True, "price_vnd": vnd}
assert client.put("/api/admin/plans/standard", json={**price(5, 30000), "name": "Standard"}).status_code == 200
assert client.put("/api/admin/plans/pro", json={**price(10, 50000), "name": "Pro"}).status_code == 200
other = TestClient(app)
assert other.post("/api/register", json={"email": "owner2@example.com", "password": "long-password-123",
                                         "workspace_name": "Studio khác"}).status_code == 201
'''

SECURITY = COMMON + r'''
# An account the admin created (a normal user) and a self-registered studio owner: neither may touch gateways.
assert client.post("/api/admin/accounts", json={"email": "member@example.com", "password": "long-password-123",
                                                 "workspace_name": "Member"}).status_code == 201
member = TestClient(app)
assert member.post("/api/login", json={"email": "member@example.com", "password": "long-password-123"}).status_code == 200
body = payos_body(client_id=replace("client-ABCD-9876"), api_key=replace(PAYOS_SECRET),
                  checksum_key=replace(PAYOS_CHECKSUM))
for user in (member, other):
    assert user.get("/api/admin/payment-config").status_code == 403
    for provider in ("payos", "onepay"):
        denied = user.put(f"/api/admin/payment-config/{provider}", json=body)
        assert denied.status_code == 403, denied.text
        clean(denied.text)
        assert user.post(f"/api/admin/payment-config/{provider}/check", json={}).status_code == 403
        assert user.post(f"/api/admin/payment-config/{provider}/disable").status_code == 403
        assert user.post(f"/api/admin/payment-config/{provider}/enable", json={}).status_code == 403
    assert user.post("/api/admin/payment-config/check", json={"provider": "payos"}).status_code == 403
with Session() as db:
    assert db.scalar(select(func.count()).select_from(PaymentProviderConfig)) == 0

# The system admin saves payOS: encrypted at rest, write-only afterwards.
saved = save("payos", body)
assert saved.status_code == 200, saved.text
clean(saved.text)
view = saved.json()
assert view["source"] == "admin" and view["enabled"] and view["configured"] and view["available"]
assert view["fields"]["client_id"] == {"configured": True, "status": "configured", "secret": False, "required": True,
                                       "legacy": "payos.client_id", "masked": "clie…9876"}
assert view["fields"]["api_key"]["configured"] and "masked" not in view["fields"]["api_key"]
assert view["updated_by"] == "owner@example.com" and view["updated_at"]
with Session() as db:
    raw = db.execute(text("SELECT * FROM payment_provider_configs")).all()
    audit_rows = db.execute(text("SELECT * FROM payment_config_audit")).all()
    settings = db.execute(text("SELECT value FROM system_settings")).all()
for value in (repr(raw), repr(audit_rows), repr(settings)):
    clean(value)
    assert "client-ABCD-9876" not in value
assert stored("payos") == {"client_id": "client-ABCD-9876", "api_key": PAYOS_SECRET, "checksum_key": PAYOS_CHECKSUM}
assert audit_trail("payos") == [("created", {"changed": ["api_key", "checksum_key", "client_id"], "mode": None})]

# Responses users and admins load never carry a secret.
for path in ("/api/admin", "/api/admin/payment-providers", "/api/admin/readiness", "/api/billing",
             "/api/admin/verification"):
    clean(client.get(path).text)
clean(other.get("/api/billing").text)
assert "client-ABCD-9876" not in client.get("/api/admin/payment-config").text

# Errors never quote a submitted value: a bad hash key, an oversized value, a wrong type, broken JSON.
bad = save("onepay", {"enabled": True, "mode": "sandbox", "merchant_id": replace("M1"),
                      "access_code": replace(ONEPAY_ACCESS), "hash_key": replace("DO_NOT_LEAK_NOT_HEX")})
assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_hash_key", bad.text
assert bad.json()["detail"]["field"] == "hash_key"
clean(bad.text)
huge = save("payos", payos_body(api_key=replace("DO_NOT_LEAK" + "x" * 600)))
assert huge.status_code == 422 and huge.json()["detail"] == {
    "code": "invalid_value", "field": "api_key", "message": "Payment configuration not saved"}
clean(huge.text)
typed = save("payos", payos_body(api_key={"action": "replace", "value": ["DO_NOT_LEAK_LIST"]}))
assert typed.status_code == 422 and typed.json()["detail"]["code"] == "invalid_request", typed.text
clean(typed.text)
broken = client.put("/api/admin/payment-config/payos", content=b'{"api_key": "DO_NOT_LEAK_BROKEN',
                    headers={"Content-Type": "application/json"})
assert broken.status_code == 422
clean(broken.text)
assert client.put("/api/admin/payment-config/stripe", json={}).status_code == 404

# No encryption key: nothing can be saved, and nothing is generated.
key = os.environ.pop("REELFORGE_TOKEN_ENCRYPTION_KEY")
missing_key = save("onepay", {"enabled": True, "mode": "sandbox", "merchant_id": replace("M1"),
                              "access_code": replace(ONEPAY_ACCESS), "hash_key": replace(ONEPAY_HASH)})
assert missing_key.status_code == 422 and missing_key.json()["detail"]["code"] == "key_missing", missing_key.text
assert "REELFORGE_TOKEN_ENCRYPTION_KEY" not in os.environ
state = client.get("/api/admin/payment-config").json()
assert state["encryption"] == {"available": False, "variable": "REELFORGE_TOKEN_ENCRYPTION_KEY"}
payos_state = {p["provider"]: p for p in state["providers"]}["payos"]
assert payos_state["issues"] == [{"level": "error", "code": "key_missing"}] and not payos_state["available"]
readiness = {c["key"]: c for s in client.get("/api/admin/readiness").json()["sections"] if s["key"] == "payments"
             for c in s["checks"]}
assert readiness["encryption"]["status"] == "error" and readiness["payos"]["status"] == "error"

# Another key (a restored database without its key): the saved configuration is unreadable and the provider
# unavailable. It never falls back to the bootstrap credentials, which may be another merchant account.
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
app_db.config["payos"] = {"client_id": "legacy-client", "api_key": "legacy-api", "checksum_key": "legacy-sum"}
changed = config("payos")
assert changed["source"] == "admin" and changed["issues"] == [{"level": "error", "code": "cannot_decrypt"}]
assert "vietqr" not in methods()
assert client.post("/api/admin/payment-config/payos/check", json={}).json()["local"] == {
    "status": "error", "code": "cannot_decrypt"}
# Re-entering every secret recovers; keeping a field cannot (its old value is unreadable).
partial = save("payos", payos_body(api_key=replace(PAYOS_SECRET)))
assert partial.status_code == 422 and partial.json()["detail"]["code"] == "missing"
assert save("payos", body).status_code == 200 and "vietqr" in methods()
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = key
assert config("payos")["issues"] == [{"level": "error", "code": "cannot_decrypt"}]
del app_db.config["payos"]

# Cross-provider swap: payOS ciphertext pasted into OnePAY's row never decrypts.
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
assert save("payos", body).status_code == 200
with Session.begin() as db:
    ciphertext = db.get(PaymentProviderConfig, "payos").config_ciphertext
    now = datetime.now(timezone.utc)
    db.add(PaymentProviderConfig(provider="onepay", enabled=True, mode="sandbox", config_ciphertext=ciphertext,
                                 created_at=now, updated_at=now))
assert config("onepay")["issues"] == [{"level": "error", "code": "cannot_decrypt"}]

clean(log_buffer.getvalue())
print("security ok")
'''

PAYOS = COMMON + r'''
# No configuration anywhere: no VietQR.
assert config("payos")["source"] == "missing" and methods() == []
assert client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"}).status_code == 503

# Legacy bootstrap credentials keep working, with no admin row at all.
app_db.config["payos"] = {"client_id": "legacy-client-0001", "api_key": "legacy-api", "checksum_key": "legacy-sum"}
legacy_view = config("payos")
assert (legacy_view["source"], legacy_view["available"], legacy_view["fields"]["client_id"]["masked"]) == \
       ("bootstrap", True, "lega…0001")
assert methods() == ["vietqr"]
first = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert first.status_code == 201, first.text
assert built[-1] == ("legacy-client-0001", "legacy-api", "legacy-sum")
legacy_order = order(first.json()["order_id"])

# The admin's configuration takes precedence, immediately: the next checkout and webhook use it.
saved = save("payos", payos_body(client_id=replace("admin-client-7777"), api_key=replace(PAYOS_SECRET),
                                 checksum_key=replace(PAYOS_CHECKSUM)))
assert saved.status_code == 200, saved.text
assert (saved.json()["source"], saved.json()["legacy_source"]) == ("admin", "bootstrap")
second = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert second.status_code == 201 and built[-1] == ("admin-client-7777", PAYOS_SECRET, PAYOS_CHECKSUM)
admin_order = order(second.json()["order_id"])
# A webhook signed with the old checksum key is refused; the saved one is accepted.
assert payos_webhook(admin_order.order_code, 30000, "legacy-sum").status_code == 400
assert payos_webhook(admin_order.order_code, 30000, PAYOS_CHECKSUM, "PAID-1").status_code == 200
assert order(admin_order.id).status == "paid" and credits() == [(5, f"payment:{admin_order.id}")]
# Delivered twice: still one credit entry.
assert payos_webhook(admin_order.order_code, 30000, PAYOS_CHECKSUM, "PAID-1").status_code == 200
assert credits() == [(5, f"payment:{admin_order.id}")]

# Updating only the API key: the other values stay; a field left unchanged (keep, or omitted) is not cleared.
only_key = save("payos", payos_body(client_id={"action": "keep", "value": ""}, api_key=replace("NEW-API-KEY-1")))
assert only_key.status_code == 200, only_key.text
assert stored("payos") == {"client_id": "admin-client-7777", "api_key": "NEW-API-KEY-1", "checksum_key": PAYOS_CHECKSUM}
assert audit_trail("payos")[-1] == ("updated", {"changed": ["api_key"], "mode": None})
# An empty replacement is refused rather than read as "clear"; clearing a required key is refused too.
empty = save("payos", payos_body(api_key={"action": "replace", "value": ""}))
assert empty.status_code == 422 and empty.json()["detail"]["code"] == "invalid_value"
cleared = save("payos", payos_body(checksum_key={"action": "clear"}))
assert cleared.status_code == 422 and cleared.json()["detail"] == {
    "code": "missing", "field": "checksum_key", "message": "Payment configuration not saved"}
assert stored("payos")["checksum_key"] == PAYOS_CHECKSUM

# Disable: no new VietQR checkout; the paid order is untouched; a pending order still settles by webhook
# and by Check.
paid_before = (order(admin_order.id).status, order(admin_order.id).paid_at)
third = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert third.status_code == 201
pending = order(third.json()["order_id"])
# Another studio's pending order, settled below by Check (a separate studio, so the existing rule that holds
# an order created before the latest plan change as paid_unapplied does not apply).
theirs = other.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"})
assert theirs.status_code == 201
their_order = order(theirs.json()["order_id"])
disabled = client.post("/api/admin/payment-config/payos/disable")
assert disabled.status_code == 200 and not disabled.json()["enabled"] and not disabled.json()["available"]
assert {"level": "warning", "code": "disabled"} in disabled.json()["issues"]
assert methods() == [] and methods(other) == []
assert client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "vietqr"}).status_code == 503
assert client.get("/api/billing").json()["payos_ready"] is False
assert (order(admin_order.id).status, order(admin_order.id).paid_at) == paid_before
assert payos_webhook(pending.order_code, 30000, PAYOS_CHECKSUM, "PAID-2").status_code == 200
assert order(pending.id).status == "paid"
assert sorted(credits()) == sorted([(5, f"payment:{admin_order.id}"), (5, f"payment:{pending.id}")])
lookups[their_order.order_code] = SimpleNamespace(order_code=their_order.order_code, amount=30000, amount_paid=30000,
                                                  status="PAID", id="LOOKUP-1")
refreshed = client.post(f"/api/admin/payments/{their_order.id}/refresh")
assert refreshed.status_code == 200 and refreshed.json()["status"] == "paid", refreshed.text
assert built[-1] == ("admin-client-7777", "NEW-API-KEY-1", PAYOS_CHECKSUM)
assert len(credits()) == 2 and order(legacy_order.id).status == "pending"

# Enable again: VietQR is back without any restart.
enabled = client.post("/api/admin/payment-config/payos/enable", json={})
assert enabled.status_code == 200 and enabled.json()["available"] and methods() == ["vietqr"]
assert [action for action, _ in audit_trail("payos")] == ["created", "updated", "disabled", "enabled"]

# Disabling a provider whose credentials are still in the bootstrap file works the same way.
with Session.begin() as db:
    db.execute(text("DELETE FROM payment_provider_configs"))
assert config("payos")["source"] == "bootstrap"
assert client.post("/api/admin/payment-config/payos/disable").status_code == 200
assert config("payos")["source"] == "bootstrap" and methods() == []
# Keeping every field only flips the switch back; nothing is imported from the bootstrap file.
assert save("payos", payos_body()).status_code == 200 and methods() == ["vietqr"]
with Session() as db:
    assert db.get(PaymentProviderConfig, "payos").config_ciphertext is None

# A test never creates an order, charges, activates a plan or awards credits.
with Session() as db:
    orders_before = db.scalar(select(func.count()).select_from(PaymentOrder))
    plan_before = db.get(Subscription, workspace).plan_code
ledger_before = credits()
tested = client.post("/api/admin/payment-config/payos/check", json={"remote": True}).json()
assert tested["local"] == {"status": "ok"} and tested["remote"] == {"status": "unsupported"}
with Session() as db:
    assert db.scalar(select(func.count()).select_from(PaymentOrder)) == orders_before
    assert db.get(Subscription, workspace).plan_code == plan_before
assert credits() == ledger_before
assert audit_trail("payos")[-1] == ("tested", {"local": "ok", "remote": True, "remote_status": "unsupported",
                                               "source": "bootstrap"})
clean(log_buffer.getvalue())
print("payos ok")
'''

ONEPAY = COMMON + r'''
ENV_HASH = "A3EFDFABA8653DF2342E8DAC29B51AF0"
os.environ.update({"ONEPAY_MERCHANT_ID": "ENVMERCHANT", "ONEPAY_ACCESS_CODE": "ENVACCESS", "ONEPAY_HASH_KEY": ENV_HASH,
                   "ONEPAY_PAYMENT_URL": "https://mtf.onepay.vn/paygate/vpcpay.op",
                   "ONEPAY_QUERY_USER": "envuser", "ONEPAY_QUERY_PASSWORD": "envpass"})

def checkout_card(c=None):
    response = (c or client).post("/api/billing/checkout", json={"plan_code": "standard", "method": "card"})
    assert response.status_code == 201, response.text
    url = response.json()["checkout_url"]
    return order(response.json()["order_id"]), url, {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}

def ipn(order_code, amount, hash_key, merchant, code="0"):
    fields = {"vpc_MerchTxnRef": str(order_code), "vpc_Merchant": merchant, "vpc_Amount": str(amount * 100),
              "vpc_TxnResponseCode": code, "vpc_TransactionNo": "T1", "vpc_Command": "pay"}
    fields["vpc_SecureHash"] = onepay.secure_hash(fields, hash_key)
    return client.get("/api/webhooks/onepay", params=fields).text

def onepay_body(mode="sandbox", enabled=True, **fields):
    return {"enabled": enabled, "mode": mode, **fields}

# Legacy environment configuration keeps working.
legacy = config("onepay")
assert (legacy["source"], legacy["mode"], legacy["available"]) == ("environment", "sandbox", True)
assert legacy["fields"]["merchant_id"]["masked"] == "ENVM…HANT" and legacy["fields"]["hash_key"]["legacy"] == \
       "ONEPAY_HASH_KEY"
# Another studio's order, created under the environment configuration and confirmed later by Check.
env_order, url, params = checkout_card(other)
assert params["vpc_Merchant"] == "ENVMERCHANT" and onepay.verify(params, ENV_HASH)

# Admin sandbox configuration overrides the environment at once.
saved = save("onepay", onepay_body(merchant_id=replace("ADMINMERCHANT"), access_code=replace(ONEPAY_ACCESS),
                                   hash_key=replace(ONEPAY_HASH), query_user=replace("adminuser"),
                                   query_password=replace(ONEPAY_PASSWORD)))
assert saved.status_code == 200, saved.text
clean(saved.text)
view = saved.json()
assert (view["source"], view["mode"], view["legacy_source"], view["query_configured"]) == \
       ("admin", "sandbox", "environment", True)
assert view["urls"] == {"payment_url": payment_config.SANDBOX_PAYMENT_URL, "query_url": payment_config.SANDBOX_QUERY_URL}
admin_order, url, params = checkout_card()
assert url.startswith(payment_config.SANDBOX_PAYMENT_URL + "?")
assert params["vpc_Merchant"] == "ADMINMERCHANT" and params["vpc_AccessCode"] == ONEPAY_ACCESS
assert onepay.verify(params, ONEPAY_HASH) and not onepay.verify(params, ENV_HASH)
clean(client.get("/api/admin/payments").text)

# The IPN is verified with the saved hash key; the environment's no longer pays anything.
assert ipn(admin_order.order_code, 30000, ENV_HASH, "ADMINMERCHANT") == "responsecode=0&desc=confirm-fail"
assert ipn(admin_order.order_code, 30000, ONEPAY_HASH, "ENVMERCHANT") == "responsecode=0&desc=confirm-fail"
assert ipn(admin_order.order_code, 30000, ONEPAY_HASH, "ADMINMERCHANT") == "responsecode=1&desc=confirm-success"
assert order(admin_order.id).status == "paid" and credits() == [(5, f"payment:{admin_order.id}")]
assert ipn(admin_order.order_code, 30000, ONEPAY_HASH, "ADMINMERCHANT") == "responsecode=1&desc=confirm-success"
assert credits() == [(5, f"payment:{admin_order.id}")]

# QueryDR (Check) asks the sandbox endpoint with the saved credentials.
asked = []
def querydr(request):
    sent = dict(httpx.QueryParams(request.content.decode()))
    asked.append((str(request.url), sent))
    answer = {"vpc_DRExists": "Y", "vpc_MerchTxnRef": sent["vpc_MerchTxnRef"], "vpc_Merchant": "ADMINMERCHANT",
              "vpc_Amount": "3000000", "vpc_TxnResponseCode": "0", "vpc_TransactionNo": "Q1", "vpc_Command": "queryDR"}
    answer["vpc_SecureHash"] = onepay.secure_hash(answer, ONEPAY_HASH)
    return httpx.Response(200, text="&".join(f"{k}={v}" for k, v in answer.items()))
with patch.object(payment_providers, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(querydr))):
    refreshed = other.post(f"/api/billing/orders/{env_order.id}/refresh")
assert refreshed.status_code == 200 and refreshed.json()["status"] == "paid", refreshed.text
assert asked[0][0] == payment_config.SANDBOX_QUERY_URL
assert (asked[0][1]["vpc_Merchant"], asked[0][1]["vpc_User"], asked[0][1]["vpc_Password"]) == \
       ("ADMINMERCHANT", "adminuser", ONEPAY_PASSWORD)
assert onepay.verify(asked[0][1], ONEPAY_HASH)

# Update one secret: the rest stays.
assert save("onepay", onepay_body(access_code=replace("NEW-ACCESS-2"))).status_code == 200
values = stored("onepay")
assert (values["access_code"], values["hash_key"], values["merchant_id"], values["query_password"]) == \
       ("NEW-ACCESS-2", ONEPAY_HASH, "ADMINMERCHANT", ONEPAY_PASSWORD)
assert audit_trail("onepay")[-1] == ("updated", {"changed": ["access_code"], "mode": "sandbox"})

# QueryDR credentials are optional: cleared, the card stays available and the IPN still confirms.
assert save("onepay", onepay_body(query_user={"action": "clear"}, query_password={"action": "clear"})).status_code == 200
cleared = config("onepay")
assert cleared["available"] and not cleared["query_configured"]
assert {"level": "warning", "code": "query_missing"} in cleared["issues"]
half = save("onepay", onepay_body(query_user=replace("only-user")))
assert half.status_code == 422 and half.json()["detail"]["code"] == "query_incomplete"
assert save("onepay", onepay_body(query_user=replace("adminuser"), query_password=replace(ONEPAY_PASSWORD))).status_code == 200

# Invalid values are refused and nothing changes.
before = stored("onepay")
for body, code in ((onepay_body(hash_key=replace("not-hex")), "invalid_hash_key"),
                   (onepay_body(mode="custom", payment_url="http://insecure.example/pay",
                                query_url="https://q.example/dr"), "invalid_url"),
                   (onepay_body(mode="live"), "invalid_request")):
    refused = save("onepay", body)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == code, refused.text
assert stored("onepay") == before

# Custom (advanced) endpoints, then production: production needs an explicit confirmation.
assert save("onepay", onepay_body(mode="custom", payment_url="https://gw.example/pay",
                                  query_url="https://gw.example/dr")).status_code == 200
custom_order, url, _ = checkout_card()
assert url.startswith("https://gw.example/pay?")
unconfirmed = save("onepay", onepay_body(mode="production"))
assert unconfirmed.status_code == 409 and unconfirmed.json()["detail"]["code"] == "confirm_production"
assert config("onepay")["mode"] == "custom"
production = save("onepay", {**onepay_body(mode="production"), "confirm_production": True})
assert production.status_code == 200 and production.json()["mode"] == "production"
assert production.json()["urls"] == {"payment_url": onepay.PRODUCTION_PAYMENT_URL,
                                     "query_url": onepay.PRODUCTION_QUERY_URL}
assert "payment_url" not in stored("onepay")
_, url, _ = checkout_card()  # a URL string only: nothing is sent to OnePAY
assert url.startswith(onepay.PRODUCTION_PAYMENT_URL + "?")
assert audit_trail("onepay")[-1] == ("updated", {"changed": ["mode", "payment_url", "query_url"], "mode": "production"})

# Disable: no new card checkout; a pending card order still settles through the IPN.
pending, _, _ = checkout_card()
assert client.post("/api/admin/payment-config/onepay/disable").status_code == 200
assert "card" not in methods()
assert client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "card"}).status_code == 503
assert ipn(pending.order_code, 30000, ONEPAY_HASH, "ADMINMERCHANT") == "responsecode=1&desc=confirm-success"
assert order(pending.id).status == "paid"
# Enabling the production gateway again also needs the confirmation.
again = client.post("/api/admin/payment-config/onepay/enable", json={})
assert again.status_code == 409 and again.json()["detail"]["code"] == "confirm_production"
assert client.post("/api/admin/payment-config/onepay/enable", json={"confirm_production": True}).status_code == 200
assert "card" in methods()

# The configuration check: local, then one read-only QueryDR (mocked); no order, no credit.
assert save("onepay", onepay_body(mode="sandbox")).status_code == 200
ledger_before = credits()
with Session() as db:
    orders_before = db.scalar(select(func.count()).select_from(PaymentOrder))
probe = []
def not_found(request):
    probe.append((str(request.url), dict(httpx.QueryParams(request.content.decode()))))
    return httpx.Response(200, text="vpc_DRExists=N&vpc_MerchTxnRef=" + probe[-1][1]["vpc_MerchTxnRef"])
with patch.object(payment_providers, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(not_found))):
    checked = client.post("/api/admin/payment-config/onepay/check", json={"remote": True})
assert checked.status_code == 200 and checked.json()["remote"] == {"status": "ok"}, checked.text
assert checked.json()["source"] == "admin" and checked.json()["mode"] == "sandbox"
assert probe[0][0] == payment_config.SANDBOX_QUERY_URL and probe[0][1]["vpc_MerchTxnRef"].startswith("RFCHECK")
clean(checked.text)
with Session() as db:
    assert db.scalar(select(func.count()).select_from(PaymentOrder)) == orders_before
assert credits() == ledger_before
actions = [action for action, _ in audit_trail("onepay")]
assert actions[0] == "created" and actions[-1] == "tested" and "disabled" in actions and "enabled" in actions
history = config("onepay")["history"]
assert history[0]["action"] == "tested" and history[0]["by"] == "owner@example.com"
clean(json.dumps(audit_trail("onepay")))

# Readiness reflects the resolved configuration.
payments = {c["key"]: c for s in client.get("/api/admin/readiness").json()["sections"] if s["key"] == "payments"
            for c in s["checks"]}
assert (payments["onepay"]["status"], payments["onepay"]["source"], payments["onepay"]["mode"]) == \
       ("ok", "admin", "sandbox")
assert payments["encryption"]["status"] == "ok" and payments["payos"]["status"] == "off"
clean(log_buffer.getvalue())
print("onepay ok")
'''


class Phase19Test(unittest.TestCase):
    def run_body(self, body, marker):
        completed = run_program(body)
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, completed.stdout[-3000:] + completed.stderr[-6000:])
        self.assertIn(marker, completed.stdout)
        for value in SENTINELS:
            self.assertNotIn(value, output)

    def test_only_system_admins_configure_gateways_and_secrets_never_leave_the_server(self):
        self.run_body(SECURITY, "security ok")

    def test_payos_admin_configuration_fallback_disable_and_settlement(self):
        self.run_body(PAYOS, "payos ok")

    def test_onepay_admin_configuration_modes_ipn_querydr_and_disable(self):
        self.run_body(ONEPAY, "onepay ok")


if __name__ == "__main__":
    unittest.main()
