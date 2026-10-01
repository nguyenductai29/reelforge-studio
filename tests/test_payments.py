"""Phase 14 payments: payOS (VietQR) and OnePAY (card) through one settlement path. Offline; every provider is mocked."""
import hashlib
import hmac
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

from app.payment_providers import onepay  # noqa: E402

HASH_KEY = "A3EFDFABA8653DF2342E8DAC29B51AF0"
CONFIG = onepay.OnePayConfig("TESTONEPAY", "6BEB2546", HASH_KEY, payment_url="https://mtf.onepay.vn/paygate/vpcpay.op",
                             query_url="https://mtf.onepay.vn/msp/api/v1/vpc/invoices/queries",
                             query_user="op01", query_password="op123456")


class OnePaySignatureTest(unittest.TestCase):
    def test_hash_covers_sorted_vpc_and_user_fields_only(self):
        params = {"vpc_Amount": "5000000", "vpc_Command": "pay", "user_Note": "x", "Title": "ignored",
                  "AgainLink": "https://a", "vpc_Empty": "", "vpc_SecureHash": "OLD", "vpc_SecureHashType": "SHA256"}
        message = "user_Note=x&vpc_Amount=5000000&vpc_Command=pay"
        expected = hmac.new(bytes.fromhex(HASH_KEY), message.encode(), hashlib.sha256).hexdigest().upper()
        self.assertEqual(onepay.secure_hash(params, HASH_KEY), expected)

    def test_checkout_url_is_signed_and_amount_is_times_100(self):
        url = onepay.checkout_url(CONFIG, merch_txn_ref="1234567890123", amount_vnd=50000, order_info="RF PRO",
                                  return_url="https://studio.example/api/billing/onepay/return", client_ip="1.2.3.4",
                                  again_link="https://studio.example/billing")
        self.assertTrue(url.startswith("https://mtf.onepay.vn/paygate/vpcpay.op?"))
        params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        self.assertEqual((params["vpc_Amount"], params["vpc_MerchTxnRef"], params["vpc_Currency"], params["vpc_Merchant"]),
                         ("5000000", "1234567890123", "VND", "TESTONEPAY"))
        self.assertTrue(onepay.verify(params, HASH_KEY))
        params["vpc_Amount"] = "100"
        self.assertFalse(onepay.verify(params, HASH_KEY))
        self.assertNotIn(HASH_KEY, url)
        with self.assertRaises(onepay.OnePayError):
            onepay.checkout_url(CONFIG, merch_txn_ref="bad ref!", amount_vnd=1, order_info="x",
                                return_url="https://a/b", client_ip="", again_link="https://a")
        with self.assertRaises(onepay.OnePayError):
            onepay.checkout_url(CONFIG, merch_txn_ref="1", amount_vnd=1, order_info="x",
                                return_url="http://public.example/b", client_ip="", again_link="https://a")

    def test_results_are_read_only_when_signed(self):
        signed = {"vpc_MerchTxnRef": "42", "vpc_Merchant": "TESTONEPAY", "vpc_Amount": "5000000",
                  "vpc_TxnResponseCode": "0", "vpc_TransactionNo": "998877"}
        signed["vpc_SecureHash"] = onepay.secure_hash(signed, HASH_KEY)
        result = onepay.read_result(signed, CONFIG)
        self.assertEqual((result.status, result.amount_vnd, result.transaction_no), ("paid", 50000, "998877"))
        for code, status in (("99", "cancelled"), ("300", "pending"), ("5", "failed"), ("", "failed")):
            other = {**signed, "vpc_TxnResponseCode": code}
            other["vpc_SecureHash"] = onepay.secure_hash(other, HASH_KEY)
            self.assertEqual(onepay.read_result(other, CONFIG).status, status)
        forged = {**signed, "vpc_Amount": "100"}
        with self.assertRaises(onepay.OnePayError) as caught:
            onepay.read_result(forged, CONFIG)
        self.assertEqual(caught.exception.code, "invalid_signature")
        foreign = {**signed, "vpc_Merchant": "OTHER"}
        foreign["vpc_SecureHash"] = onepay.secure_hash(foreign, HASH_KEY)
        with self.assertRaises(onepay.OnePayError):
            onepay.read_result(foreign, CONFIG)

    def test_configuration_comes_from_the_environment(self):
        with patch.dict(os.environ, {"ONEPAY_MERCHANT_ID": "", "ONEPAY_ACCESS_CODE": "", "ONEPAY_HASH_KEY": ""}):
            self.assertFalse(onepay.configured())
        with patch.dict(os.environ, {"ONEPAY_MERCHANT_ID": "M", "ONEPAY_ACCESS_CODE": "A", "ONEPAY_HASH_KEY": "zz"}):
            self.assertFalse(onepay.configured())  # the hash key must be hex
        with patch.dict(os.environ, {"ONEPAY_MERCHANT_ID": "M", "ONEPAY_ACCESS_CODE": "A", "ONEPAY_HASH_KEY": HASH_KEY,
                                     "ONEPAY_PAYMENT_URL": "http://insecure.example"}):
            self.assertFalse(onepay.configured())
        self.assertNotIn(HASH_KEY, repr(CONFIG))
        self.assertNotIn("op123456", repr(CONFIG))


PAYMENTS = r'''
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from app import billing, payment_providers
from app.payment_providers import onepay
from app.models import Subscription
HASH = os.environ["ONEPAY_HASH_KEY"]

def signed(**fields):
    fields["vpc_SecureHash"] = onepay.secure_hash(fields, HASH)
    return fields

def result(order_code, amount, code="0", txn="TXN1"):
    return signed(vpc_MerchTxnRef=str(order_code), vpc_Merchant="TESTONEPAY", vpc_Amount=str(amount * 100),
                  vpc_TxnResponseCode=code, vpc_TransactionNo=txn, vpc_Command="pay")

def dr(order_code, amount, txn="TXN1"):
    """A signed QueryDR answer for an existing transaction (OnePAY signs vpc_DRExists too)."""
    return signed(vpc_DRExists="Y", vpc_MerchTxnRef=str(order_code), vpc_Merchant="TESTONEPAY",
                  vpc_Amount=str(amount * 100), vpc_TxnResponseCode="0", vpc_TransactionNo=txn, vpc_Command="queryDR")

def order(order_id):
    with Session() as db:
        return db.get(PaymentOrder, order_id)

def plan_of():
    return client.get("/api/dashboard").json()["workspace"]["plan"]

def ends_at():
    with Session() as db:
        return db.get(Subscription, workspace).ends_at

from app.models import PaymentOrder
price = lambda credits, vnd: {"name": "Plan", "project_limit": None, "workflow_limit": None, "monthly_credits": credits,
                              "is_active": True, "price_vnd": vnd}
assert client.put("/api/admin/plans/standard", json={**price(5, 30000), "name": "Standard"}).status_code == 200
assert client.put("/api/admin/plans/pro", json={**price(10, 50000), "name": "Pro"}).status_code == 200

# Only configured methods are offered: OnePAY is configured here, payOS is not (no bootstrap credentials).
overview = client.get("/api/billing").json()
assert overview["methods"] == [{"id": "card", "provider": "onepay"}] and overview["payos_ready"] is False, overview
assert client.post("/api/billing/checkout", json={"plan_code": "pro"}).status_code == 503  # VietQR is the default
providers = client.get("/api/admin/payment-providers").json()["providers"]
assert {p["provider"]: p["configured"] for p in providers} == {"payos": False, "onepay": True}
assert HASH not in client.get("/api/admin/payment-providers").text and "op123456" not in client.get("/api/admin").text

# Card checkout: a signed OnePAY URL for the exact amount; nothing changes until OnePAY confirms.
checkout = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "card"})
assert checkout.status_code == 201, checkout.text
url = checkout.json()["checkout_url"]
params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
card = order(checkout.json()["order_id"])
assert url.startswith("https://mtf.onepay.vn/paygate/vpcpay.op?") and onepay.verify(params, HASH)
assert (card.provider, params["vpc_Amount"], params["vpc_MerchTxnRef"]) == ("onepay", "3000000", str(card.order_code))
assert params["vpc_ReturnURL"].endswith("/api/billing/onepay/return")
assert plan_of() == "trial" and balance() == 0

# The IPN: forged, wrong amount and unknown orders are refused; a valid one settles once.
forged = {**result(card.order_code, 30000), "vpc_TxnResponseCode": "0", "vpc_Amount": "3000000"}
forged["vpc_SecureHash"] = "0" * 64
assert client.get("/api/webhooks/onepay", params=forged).text == "responsecode=0&desc=confirm-fail"
assert client.get("/api/webhooks/onepay", params=result(card.order_code, 29999)).text == "responsecode=0&desc=amount-mismatch"
assert client.get("/api/webhooks/onepay", params=result(9_999_999_999_999, 30000)).text == "responsecode=0&desc=order-not-found"
assert order(card.id).status == "pending" and plan_of() == "trial"
ok = client.post("/api/webhooks/onepay", content=urlencode(result(card.order_code, 30000)),
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
assert ok.text == "responsecode=1&desc=confirm-success", ok.text
paid = order(card.id)
assert (paid.status, paid.provider_reference, plan_of(), balance()) == ("paid", "TXN1", "standard", 5)
first_end = ends_at()
# The same callback again (OnePAY retries): success, but the subscription and credits change exactly once.
assert client.get("/api/webhooks/onepay", params=result(card.order_code, 30000)).text == "responsecode=1&desc=confirm-success"
assert (balance(), ends_at()) == (5, first_end)

# The browser return: a signed "approved" is applied only after QueryDR confirms it server to server.
def querydr(answer):
    seen = []
    def handler(request):
        seen.append(dict(parse_qs(request.content.decode())))
        return httpx.Response(200, text=urlencode(answer))
    payment_providers.http_client = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    return seen

renewal = client.post("/api/billing/checkout", json={"plan_code": "standard", "method": "card"}).json()
renewal_code = order(renewal["order_id"]).order_code
seen = querydr({"vpc_DRExists": "N"})
back = client.get("/api/billing/onepay/return", params=result(renewal_code, 30000), follow_redirects=False)
assert (back.status_code, back.headers["location"]) == (303, "/billing?payment=returned")
assert order(renewal["order_id"]).status == "pending" and balance() == 5  # not confirmed: nothing applied
assert seen[0]["vpc_Command"] == ["queryDR"] and seen[0]["vpc_MerchTxnRef"] == [str(renewal_code)]
seen = querydr(dr(renewal_code, 30000, txn="TXN2"))
back = client.get("/api/billing/onepay/return", params=result(renewal_code, 30000, txn="TXN2"), follow_redirects=False)
assert order(renewal["order_id"]).status == "paid" and balance() == 10
assert ends_at() > first_end  # the same plan is extended from its current end
# A forged return changes nothing; failed and cancelled payments close the order.
forged_back = client.get("/api/billing/onepay/return", params={**result(renewal_code, 30000), "vpc_Amount": "1"},
                         follow_redirects=False)
assert forged_back.headers["location"] == "/billing?payment=invalid"
declined = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "card"}).json()
declined_code = order(declined["order_id"]).order_code
back = client.get("/api/billing/onepay/return", params=result(declined_code, 50000, code="5"), follow_redirects=False)
assert back.headers["location"] == "/billing?payment=failed" and order(declined["order_id"]).status == "failed"
cancelled = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "card"}).json()
back = client.get("/api/billing/onepay/return", params=result(order(cancelled["order_id"]).order_code, 50000, code="99"),
                  follow_redirects=False)
assert back.headers["location"] == "/billing?payment=cancelled" and order(cancelled["order_id"]).status == "cancelled"
assert balance() == 10 and plan_of() == "standard"

# VietQR through payOS still works the same way, and settles through the same path.
billing.configured = lambda: True
billing.create_link = lambda code, amount, plan, origin: f"https://pay.payos.vn/web/{code}"
assert client.get("/api/billing").json()["methods"] == [{"id": "vietqr", "provider": "payos"}, {"id": "card", "provider": "onepay"}]
upgrade = client.post("/api/billing/checkout", json={"plan_code": "pro"})
assert upgrade.status_code == 201 and upgrade.json()["checkout_url"].startswith("https://pay.payos.vn/"), upgrade.text
qr = order(upgrade.json()["order_id"])
assert qr.provider == "payos"
# Cross-provider: a signed OnePAY callback cannot pay a VietQR order, and a payOS webhook cannot pay a card order.
assert client.get("/api/webhooks/onepay", params=result(qr.order_code, 50000)).text == "responsecode=0&desc=order-not-found"
assert order(qr.id).status == "pending"
billing.verify_webhook = lambda body: SimpleNamespace(order_code=declined_code, amount=50000, currency="VND", reference="x")
assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 200
assert order(declined["order_id"]).status == "failed" and balance() == 10
billing.verify_webhook = lambda body: SimpleNamespace(order_code=qr.order_code, amount=50000, currency="VND", reference="qr-ref")
for _ in range(2):  # payOS may deliver the webhook twice
    assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 200
assert (order(qr.id).status, plan_of(), balance()) == ("paid", "pro", 20)  # upgraded; credits awarded once

# A status check asks the order's own provider: QueryDR for a card order, here run by an admin.
pending = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "card"}).json()
pending_code = order(pending["order_id"]).order_code
querydr(dr(pending_code, 50000, txn="TXN9"))
refreshed = client.post(f"/api/admin/payments/{pending['order_id']}/refresh")
assert refreshed.status_code == 200 and refreshed.json()["status"] == "paid", refreshed.text
assert balance() == 30
querydr(dr(pending_code, 50000))
assert client.post(f"/api/billing/orders/{pending['order_id']}/refresh").json()["status"] == "paid" and balance() == 30

# History: one page at a time, with safe fields only.
history = client.get("/api/billing/orders", params={"limit": 2}).json()
# Six orders: standard, its renewal, declined, cancelled, the VietQR upgrade, the admin-checked one.
assert history["total"] == 6 and len(history["items"]) == 2, history
assert set(history["items"][0]) == {"id", "plan_code", "provider", "method", "reference", "provider_reference",
                                    "amount_vnd", "status", "created_at", "paid_at"}
assert "checkout" not in client.get("/api/billing/orders").text
print("payments ok")
'''


class PaymentsFlowTest(unittest.TestCase):
    def test_vietqr_and_card_settle_through_one_path(self):
        completed = run_program(PAYMENTS, env={
            "ONEPAY_MERCHANT_ID": "TESTONEPAY", "ONEPAY_ACCESS_CODE": "6BEB2546", "ONEPAY_HASH_KEY": HASH_KEY,
            "ONEPAY_PAYMENT_URL": "https://mtf.onepay.vn/paygate/vpcpay.op",
            "ONEPAY_QUERY_URL": "https://mtf.onepay.vn/msp/api/v1/vpc/invoices/queries",
            "ONEPAY_QUERY_USER": "op01", "ONEPAY_QUERY_PASSWORD": "op123456"})
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])


if __name__ == "__main__":
    unittest.main()
