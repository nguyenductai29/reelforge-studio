"""Phase 18: notifications (durable, workspace-safe, SSE), support tickets, admin payment setup and the
live-verification center. Offline: no provider, payment or platform call leaves the process."""
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

COMMON = r'''
import uuid
from unittest.mock import patch
from app import notifications, payments, render_worker
from app.models import Notification, PaymentOrder, Plan, Subscription, WorkflowRun
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
other = TestClient(app, headers={"Origin": "http://testserver"})
assert other.post("/api/register", json={"accept_terms": True, "email": "other@example.com", "password": "long-password-123",
                                         "workspace_name": "Studio khác"}).status_code == 201
other_ws = other.get("/api/dashboard").json()["workspace"]["id"]

def items(c=client, **params):
    return c.get("/api/notifications", params={"limit": 100, **params}).json()["items"]

def kinds(c=client):
    return [item["type"] for item in items(c)]
'''

NOTIFY = COMMON + r'''
# A render that reaches review: one notification for the studio, none for another studio.
photo = upload("a.png", PNG, "image/png").json()["id"]
workflow, saved = save_graph([node("img", "source_media", {"asset_id": photo}), node("render", "render", x=300),
                              node("review", "review", x=600)],
                             [edge("img", "image", "render", "media"), edge("render", "rendered_video", "review", "media")])
assert saved.status_code == 200, saved.text
run = start(workflow)
assert render_worker.run_one(runner=ffmpeg_runner())
latest = items()[0]
assert (latest["type"], latest["params"]["rendered"]) == ("run.awaiting_review", True), latest
assert latest["link"] == f"/workflows/{workflow}?run={run['id']}" and latest["params"]["run_id"] == run["id"]
assert "run.awaiting_review" not in kinds(other)
# The same transition again (a second worker pass) adds nothing.
with Session.begin() as db:
    notifications.run_status_changed(db, db.get(WorkflowRun, run["id"]), "running", "awaiting_review")
assert kinds().count("run.awaiting_review") == 1

# A payment settles once, even when the webhook arrives twice.
with Session.begin() as db:
    db.add(PaymentOrder(id=str(uuid.uuid4()), workspace_id=workspace, plan_code="standard", provider="payos",
                        order_code=987654321, amount_vnd=199000, credits_award=0, status="pending",
                        created_at=datetime.now(timezone.utc)))
for _ in range(2):
    with Session.begin() as db:
        assert payments.apply_paid(db, 987654321, 199000, "REF-1", provider="payos") == "paid"
assert kinds().count("payment.succeeded") == 1
paid = next(item for item in items() if item["type"] == "payment.succeeded")
assert paid["params"] == {"plan": "standard", "amount": 199000, "provider": "payos"} and paid["link"] == "/billing"

# Credits: an admin adjustment is told; a debit across the low threshold is told once.
assert client.post(f"/api/admin/workspaces/{workspace}/credits", json={"delta": 40, "reason": "bù lỗi"}).status_code == 200
adjusted = next(item for item in items() if item["type"] == "credits.adjusted")
assert adjusted["params"]["delta"] == 40 and adjusted["params"]["reason"] == "bù lỗi"
current = balance()
assert current >= 20, current
with Session.begin() as db:
    usage.post_credit(db, workspace, -(current - 15), "usage", "usage:low-1")
with Session.begin() as db:
    usage.post_credit(db, workspace, -1, "usage", "usage:low-2")
assert kinds().count("credits.low") == 1
assert next(item for item in items() if item["type"] == "credits.low")["params"]["balance"] == 15

# Storage: the write that passes 80 % is told (only the studio that stored it).
used = client.get("/api/storage").json()["used_bytes"]
limit_bytes = int((used + 900) / 0.85)
with Session.begin() as db:
    db.execute(update(Plan).values(storage_limit_bytes=limit_bytes))
assert upload("b.png", PNG + b"\x00" * 852, "image/png").status_code == 201
warning = next(item for item in items() if item["type"] == "storage.warning")
assert warning["params"]["percent"] == 80 and warning["link"] == "/settings?tab=storage"
assert "storage.warning" not in kinds(other)
with Session.begin() as db:
    db.execute(update(Plan).values(storage_limit_bytes=None))

# Publishing: a scheduled post, then a scheduled post that cannot go out (the channel was disconnected).
connect_youtube()
approved, _ = approved_run()
later = datetime.now(timezone.utc) + timedelta(hours=2)
publish(approved, target("youtube"), scheduled_for=later.isoformat(), status=201)
assert kinds().count("publish.scheduled") == 1
with Session.begin() as db:
    db.execute(text("DELETE FROM youtube_connections"))
with Session.begin() as db:
    publications.dispatch_due(db, now=later + timedelta(minutes=1))
failed = next(item for item in items() if item["type"] == "publish.failed")
assert failed["params"]["channel"] == "youtube" and failed["link"] == "/publishing"

# Reading: unread count, one read, another user's notification is not readable, read all, pagination.
unread = client.get("/api/notifications/unread-count").json()["unread"]
assert unread == sum(1 for item in items() if item["read_at"] is None) and unread >= 6, unread
first = items()[0]["id"]
assert client.post(f"/api/notifications/{first}/read").json()["unread"] == unread - 1
mine = {item["id"] for item in items()}
with Session.begin() as db:
    notifications.notify(db, [db.scalar(text("SELECT id FROM users WHERE email = 'other@example.com'"))], "support.reply",
                         "x", "y", dedupe="private-to-other")
theirs = items(other)[0]["id"]
assert theirs not in mine and client.post(f"/api/notifications/{theirs}/read").status_code == 404
page = client.get("/api/notifications", params={"limit": 2, "offset": 1}).json()
assert len(page["items"]) == 2 and page["total"] == len(items()) and page["offset"] == 1
assert client.get("/api/notifications", params={"unread": "true"}).json()["total"] == unread - 1
assert client.post("/api/notifications/read-all").json()["unread"] == 0
assert other.get("/api/notifications/unread-count").json()["unread"] == 1

# SSE: cookie authentication, only the user's own events, resumable ids, heartbeat-free short streams.
os.environ["REELFORGE_SSE_POLL_SECONDS"] = "0.05"
os.environ["REELFORGE_SSE_MAX_SECONDS"] = "0.5"
assert TestClient(app, headers={"Origin": "http://testserver"}).get("/api/notifications/stream").status_code == 401
def stream(c, last=None):
    headers = {"Last-Event-ID": str(last)} if last is not None else {}
    with c.stream("GET", "/api/notifications/stream", headers=headers) as response:
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        assert "no-transform" in response.headers["cache-control"] and response.headers["x-accel-buffering"] == "no"
        return response.read().decode()
body = stream(client, last=0)
ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
assert set(ids) == mine and theirs not in ids and body.startswith("retry: 5000"), body[:300]
assert 'event: unread\ndata: {"unread": 0}' in body
resumed = stream(client, last=max(mine) - 1)
assert [int(line[4:]) for line in resumed.splitlines() if line.startswith("id: ")] == [max(mine)]
fresh = stream(client)
assert "event: notification" not in fresh and "event: unread" in fresh
other_body = stream(other, last=0)
assert [int(line[4:]) for line in other_body.splitlines() if line.startswith("id: ")] == [theirs]
print("notifications ok")
'''

SUPPORT = COMMON + r'''
# The other studio opens a ticket; the system admin (this client) is told.
created = other.post("/api/support/tickets", json={"subject": "Không thanh toán được", "category": "billing",
                                                   "description": "Thẻ bị từ chối."})
assert created.status_code == 201, created.text
ticket = created.json()
assert ticket["status"] == "waiting_support" and ticket["thread"][0]["author_type"] == "user"
assert "support.new" in kinds()
# Context IDs must belong to the studio.
run_id, _ = approved_run()
foreign = other.post("/api/support/tickets", json={"subject": "x", "category": "generation", "description": "y",
                                                   "run_id": run_id})
assert foreign.status_code == 422 and foreign.json()["detail"]["code"] == "invalid_context", foreign.text
mine = client.post("/api/support/tickets", json={"subject": "Render lỗi", "category": "generation",
                                                 "description": "Bước render thất bại.", "run_id": run_id}).json()
assert mine["context"] == {"run_id": run_id}
# Each studio sees only its own tickets.
assert [t["id"] for t in other.get("/api/support/tickets").json()["items"]] == [ticket["id"]]
assert [t["id"] for t in client.get("/api/support/tickets").json()["items"]] == [mine["id"]]
assert other.get(f"/api/support/tickets/{mine['id']}").status_code == 404
assert other.post(f"/api/support/tickets/{mine['id']}/messages", json={"body": "hi"}).status_code == 404
# Admin console: search, filters, non-admins refused.
assert other.get("/api/admin/support").status_code == 403
listed = client.get("/api/admin/support", params={"q": "THANH TOÁN"}).json()
assert [t["id"] for t in listed["items"]] == [ticket["id"]] and listed["items"][0]["created_by_email"] == "other@example.com"
assert client.get("/api/admin/support", params={"q": ticket["id"][:8]}).json()["total"] == 1
assert client.get("/api/admin/support", params={"q": "other@example"}).json()["total"] == 1
# Wildcards are literal, in the ID prefix too.
assert client.get("/api/admin/support", params={"q": "%"}).json()["total"] == 0
assert client.get("/api/admin/support", params={"q": "_" * 8}).json()["total"] == 0
assert client.get("/api/admin/support", params={"category": "generation"}).json()["total"] == 1
assert client.get("/api/admin/support", params={"status": "waiting_support"}).json()["total"] == 2
assert client.get("/api/admin", params={}).json()["counts"]["support_open"] == 2
# Admin reply: the user is told, sees "support" (not the admin's account) as the author, and the ticket waits on them.
reply = client.post(f"/api/admin/support/{ticket['id']}/messages", json={"body": "Hãy thử lại với VietQR."})
assert reply.status_code == 201 and reply.json()["status"] == "waiting_user"
assert reply.json()["thread"][-1]["author_email"] == "owner@example.com"
seen = other.get(f"/api/support/tickets/{ticket['id']}").json()
assert seen["thread"][-1]["author_type"] == "admin" and seen["thread"][-1]["author_email"] is None
told = next(item for item in items(other) if item["type"] == "support.reply")
assert told["link"] == f"/support/{ticket['id']}"
# Priority and status changes; a status change tells the user; a user reply reopens a resolved ticket.
patched = client.patch(f"/api/admin/support/{ticket['id']}", json={"priority": "high", "status": "resolved"}).json()
assert (patched["priority"], patched["status"]) == ("high", "resolved")
assert "support.status" in kinds(other)
assert client.get("/api/admin/support", params={"priority": "high"}).json()["total"] == 1
again = other.post(f"/api/support/tickets/{ticket['id']}/messages", json={"body": "Vẫn chưa được."}).json()
assert again["status"] == "waiting_support" and kinds().count("support.reply") == 1
# Closing: no more replies from either side; messages were never edited.
closed = other.post(f"/api/support/tickets/{ticket['id']}/close").json()
assert closed["status"] == "closed" and closed["closed_at"]
late = other.post(f"/api/support/tickets/{ticket['id']}/messages", json={"body": "Còn nữa"})
assert late.status_code == 409 and late.json()["detail"]["code"] == "ticket_closed"
assert client.post(f"/api/admin/support/{ticket['id']}/messages", json={"body": "?"}).status_code == 409
assert [m["body"] for m in other.get(f"/api/support/tickets/{ticket['id']}").json()["thread"]] == \
       ["Thẻ bị từ chối.", "Hãy thử lại với VietQR.", "Vẫn chưa được."]
# Validation.
assert other.post("/api/support/tickets", json={"subject": "", "category": "other", "description": "x"}).status_code == 422
assert other.post("/api/support/tickets", json={"subject": "a", "category": "crm", "description": "x"}).status_code == 422
print("support ok")
'''

ADMIN = COMMON + r'''
import app.db as app_db
from app import payment_providers, readiness
os.environ.update({"ONEPAY_MERCHANT_ID": "MERCHANT123", "ONEPAY_ACCESS_CODE": "ACCESS-SENTINEL",
                   "ONEPAY_HASH_KEY": "A1B2C3D4E5F60718293A4B5C6D7E8F90", "ONEPAY_QUERY_USER": "query-user-SENTINEL",
                   "ONEPAY_QUERY_PASSWORD": "query-pass-SENTINEL",
                   "ONEPAY_PAYMENT_URL": "https://mtf.onepay.vn/paygate/vpcpay.op"})
app_db.config["payos"] = {"client_id": "client-1234-5678", "api_key": "payos-key-SENTINEL",
                          "checksum_key": "payos-checksum-SENTINEL"}
LEAKS = ("ACCESS-SENTINEL", "A1B2C3D4E5F60718293A4B5C6D7E8F90", "query-user-SENTINEL", "query-pass-SENTINEL",
         "payos-key-SENTINEL", "payos-checksum-SENTINEL", "client-1234-5678", "MERCHANT123")
def clean(text):
    no_secrets(text)
    for value in LEAKS:
        assert value not in text, value

# Only system admins see provider setup, readiness and the checklist.
for method, path in (("get", "/api/admin/payment-config"), ("get", "/api/admin/readiness"),
                     ("get", "/api/admin/verification")):
    assert getattr(other, method)(path).status_code == 403, path
assert other.post("/api/admin/payment-config/check", json={"provider": "onepay"}).status_code == 403
assert other.put("/api/admin/verification/ffmpeg_verified", json={"verified": True}).status_code == 403
# A studio owner only sees which methods exist.
billing = other.get("/api/billing")
clean(billing.text)
assert {m["id"] for m in billing.json()["methods"]} == {"vietqr", "card"} and "fields" not in billing.text

setup = client.get("/api/admin/payment-config")
clean(setup.text)
by_provider = {item["provider"]: item for item in setup.json()["providers"]}
payos, onepay_setup = by_provider["payos"], by_provider["onepay"]
assert by_provider["bank_qr"]["source"] == "missing" and not by_provider["bank_qr"]["available"]
assert payos["configured"] and {name: f["status"] for name, f in payos["fields"].items()} == {
    "client_id": "configured", "api_key": "configured", "checksum_key": "configured"}
assert payos["source"] == "bootstrap" and payos["fields"]["client_id"]["legacy"] == "payos.client_id"
assert payos["fields"]["client_id"]["masked"] == "clie…5678" and "masked" not in payos["fields"]["api_key"]
assert payos["endpoints"] == [{"key": "webhook", "url": "http://localhost:3000/api/webhooks/payos"}]
fields = onepay_setup["fields"]
assert onepay_setup["mode"] == "sandbox" and onepay_setup["configured"] and onepay_setup["query_configured"]
assert onepay_setup["source"] == "environment" and fields["hash_key"]["legacy"] == "ONEPAY_HASH_KEY"
assert fields["merchant_id"]["masked"] == "MERC…T123" and "masked" not in fields["hash_key"]
assert onepay_setup["urls"]["payment_url"] == "https://mtf.onepay.vn/paygate/vpcpay.op"
assert {e["key"] for e in onepay_setup["endpoints"]} == {"ipn", "return"}

# Checks: local only by default; the remote one is one read-only QueryDR, mocked here.
assert client.post("/api/admin/payment-config/check", json={"provider": "onepay"}).json()["local"] == {"status": "ok"}
asked = []
def answer(request):
    asked.append(dict(httpx.QueryParams(request.content.decode())))
    return httpx.Response(200, text="vpc_DRExists=N&vpc_MerchTxnRef=" + asked[-1]["vpc_MerchTxnRef"])
with patch.object(payment_providers, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(answer))):
    remote = client.post("/api/admin/payment-config/check", json={"provider": "onepay", "remote": True})
clean(remote.text)
assert remote.json()["remote"] == {"status": "ok"} and asked[0]["vpc_Command"] == "queryDR"
assert asked[0]["vpc_MerchTxnRef"].startswith("RFCHECK")
with patch.object(payment_providers, "http_client",
                  lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))):
    down = client.post("/api/admin/payment-config/check", json={"provider": "onepay", "remote": True}).json()
assert down["remote"] == {"status": "error", "code": "unavailable"}
assert client.post("/api/admin/payment-config/check", json={"provider": "payos", "remote": True}).json()["remote"] == \
       {"status": "unsupported"}
assert "check" in client.get("/api/admin/payment-config").json()["providers"][2]["activity"]
os.environ["ONEPAY_HASH_KEY"] = "not-hex"
assert client.post("/api/admin/payment-config/check", json={"provider": "onepay"}).json()["local"]["status"] == "error"
assert client.get("/api/admin/payment-config").json()["providers"][2]["fields"]["hash_key"]["status"] == "invalid"

# Readiness: every section, safe values only.
report = client.get("/api/admin/readiness")
clean(report.text)
sections = {s["key"]: {c["key"]: c for c in s["checks"]} for s in report.json()["sections"]}
assert set(sections) == {"database", "storage", "ffmpeg", "workers", "ai", "publishing", "payments", "realtime", "support", "security", "configuration",
                         "backups", "email", "accounts", "alerts"}
assert sections["database"]["migration"]["status"] == "ok"
assert sections["payments"]["onepay"]["mode"] == "sandbox" and sections["payments"]["payos"]["status"] == "ok"
assert sections["workers"]["render_worker"]["status"] == "missing"
assert sections["ai"]["openai"]["status"] == "ok" and sections["ai"]["runway"]["status"] == "missing"
assert sections["publishing"]["youtube"]["status"] == "ok" and sections["security"]["master_key"]["status"] == "warning"

# The checklist persists who verified what; nothing is ticked by itself.
listed = client.get("/api/admin/verification").json()["items"]
assert len(listed) == len(readiness.CHECKLIST) and not any(item["verified"] for item in listed)
saved = client.put("/api/admin/verification/onepay_sandbox_payment", json={"verified": True, "note": "Thẻ test OK"}).json()
assert saved["verified"] and saved["verified_by"] == "owner@example.com" and saved["note"] == "Thẻ test OK"
again = {item["key"]: item for item in client.get("/api/admin/verification").json()["items"]}
assert again["onepay_sandbox_payment"]["verified"] and again["onepay_sandbox_payment"]["verified_at"]
assert client.put("/api/admin/verification/onepay_sandbox_payment", json={"verified": False}).json()["verified"] is False
assert client.put("/api/admin/verification/made_up", json={"verified": True}).status_code == 404
print("admin ok")
'''


class Phase18Test(unittest.TestCase):
    def run_body(self, body, marker):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stdout[-3000:] + completed.stderr[-6000:])
        self.assertIn(marker, completed.stdout)

    def test_notifications_are_durable_workspace_safe_and_streamed(self):
        self.run_body(NOTIFY, "notifications ok")

    def test_support_tickets_between_users_and_admins(self):
        self.run_body(SUPPORT, "support ok")

    def test_payment_setup_and_verification_are_admin_only_and_secret_free(self):
        self.run_body(ADMIN, "admin ok")


if __name__ == "__main__":
    unittest.main()
