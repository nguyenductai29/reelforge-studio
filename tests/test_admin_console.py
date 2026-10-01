"""Phase 15 admin console: server-side pagination, search and filters, account creation, credits and payments. Offline."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

ADMIN = r'''
from app.models import CreditAccount, CreditLedger, LoginSession, PaymentOrder, Plan, User, Workspace
admin_id = None
with Session() as db:
    admin_id = db.scalar(select(User.id).where(User.email == "owner@example.com"))

def page(path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()

# Account creation (the dialog in the Users tab) reuses POST /api/admin/accounts.
for n in range(25):
    created = client.post("/api/admin/accounts", json={"email": f"User{n:02d}@Example.com", "password": "long-password-123",
                                                       "workspace_name": f"Studio {n:02d}", "plan_code": "trial"})
    assert created.status_code == 201, created.text
assert client.post("/api/admin/accounts", json={"email": "user00@example.com", "password": "long-password-123",
                                                "workspace_name": "Again", "plan_code": "trial"}).status_code == 409

# Users: 20 per page by default, at most 100, total from COUNT.
first = page("/api/admin/users")
assert (first["total"], first["limit"], first["offset"], len(first["items"])) == (26, 20, 0, 20), first
second = page("/api/admin/users", offset=20)
assert len(second["items"]) == 6 and not {u["id"] for u in first["items"]} & {u["id"] for u in second["items"]}
assert client.get("/api/admin/users", params={"limit": 101}).status_code == 422
row = next(u for u in first["items"] + second["items"] if u["email"] == "user07@example.com")
assert (row["workspace"]["name"], row["plan_code"], row["subscription_status"], row["is_admin"]) == \
       ("Studio 07", "trial", "active", False), row
assert row["created_at"]
# Search is server-side and case-insensitive; % and _ are literal.
found = page("/api/admin/users", q="USER1")
assert found["total"] == 10 and all(u["email"].startswith("user1") for u in found["items"]), found
assert page("/api/admin/users", q="%")["total"] == 0
assert page("/api/admin/users", q="_")["total"] == 0
assert [u["email"] for u in page("/api/admin/users", role="admin")["items"]] == ["owner@example.com"]
assert page("/api/admin/users", role="member")["total"] == 25

# Lock and unlock with the existing protections: never yourself, and a locked user is signed out.
assert client.put(f"/api/admin/users/{admin_id}", json={"is_active": False}).status_code == 400
member = TestClient(app, headers={"Origin": "http://testserver"})
assert member.post("/api/login", json={"email": "user03@example.com", "password": "long-password-123"}).status_code == 200
locked_id = next(u["id"] for u in page("/api/admin/users", q="user03")["items"])
assert client.put(f"/api/admin/users/{locked_id}", json={"is_active": False}).status_code == 200
assert member.get("/api/dashboard").status_code == 401
assert [u["email"] for u in page("/api/admin/users", status="locked")["items"]] == ["user03@example.com"]
detail = page(f"/api/admin/users/{locked_id}")
assert (detail["is_active"], detail["active_sessions"], detail["workspaces"][0]["name"]) == (False, 0, "Studio 03"), detail
assert client.put(f"/api/admin/users/{locked_id}", json={"is_active": True}).status_code == 200
assert page("/api/admin/users", status="locked")["total"] == 0
assert client.get("/api/admin/users/missing").status_code == 404

# Studios: paginated, searched by name or owner, filtered by plan and status.
studios = page("/api/admin/workspaces", limit=10)
assert (studios["total"], len(studios["items"])) == (26, 10), studios
assert page("/api/admin/workspaces", q="studio 1")["total"] == 10
assert [w["name"] for w in page("/api/admin/workspaces", q="USER22@")["items"]] == ["Studio 22"]
target = page("/api/admin/workspaces", q="Studio 05")["items"][0]
assert set(target) >= {"id", "name", "owner_email", "plan_code", "credits", "ends_at", "status"}
with Session.begin() as db:
    db.execute(update(Plan).where(Plan.code == "pro").values(is_active=True))
assert client.put(f"/api/admin/workspaces/{target['id']}/subscription",
                  json={"plan_code": "pro", "status": "active"}).status_code == 200
assert [w["name"] for w in page("/api/admin/workspaces", plan="pro")["items"]] == ["Studio 05"]
assert client.put(f"/api/admin/workspaces/{target['id']}/subscription",
                  json={"plan_code": "pro", "status": "paused"}).status_code == 200
assert [w["name"] for w in page("/api/admin/workspaces", status="paused")["items"]] == ["Studio 05"]
expired_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
other = page("/api/admin/workspaces", q="Studio 06")["items"][0]
assert client.put(f"/api/admin/workspaces/{other['id']}/subscription",
                  json={"plan_code": "trial", "status": "active", "ends_at": expired_at}).status_code == 200
assert [w["name"] for w in page("/api/admin/workspaces", status="expired")["items"]] == ["Studio 06"]
assert page("/api/admin/workspaces", status="active")["total"] == 24

# Credits: the dialog posts to the append-only ledger.
assert client.post(f"/api/admin/workspaces/{target['id']}/credits", json={"delta": 250, "reason": "promo"}).status_code == 200
assert client.post(f"/api/admin/workspaces/{target['id']}/credits", json={"delta": -50, "reason": "fix"}).status_code == 200
assert client.post(f"/api/admin/workspaces/{target['id']}/credits", json={"delta": -1000, "reason": "too much"}).status_code == 400
assert page("/api/admin/workspaces", q="Studio 05")["items"][0]["credits"] == 200
studio = page(f"/api/admin/workspaces/{target['id']}")
assert [e["delta"] for e in studio["ledger"]] == [-50, 250] and studio["ledger"][0]["reason"] == "admin: fix", studio
assert studio["members"] == [{"email": "user05@example.com", "role": "owner"}] and studio["counts"]["projects"] == 0

# Payments: every studio's orders, newest first, searched and filtered on the server.
now = datetime.now(timezone.utc)
with Session.begin() as db:
    owner_ws = db.scalar(select(Workspace.id).where(Workspace.name == "Studio 11"))
    for n, (provider, status) in enumerate([("payos", "paid"), ("onepay", "failed"), ("onepay", "paid"),
                                            ("payos", "pending")]):
        db.add(PaymentOrder(id=f"order-{n}", workspace_id=owner_ws if n < 2 else target["id"], plan_code="pro",
                            provider=provider, order_code=1_000_000_000_000 + n, amount_vnd=50000, credits_award=10,
                            status=status, checkout_url="https://secret.example/token", provider_reference=f"REF-{n}",
                            created_at=now + timedelta(seconds=n)))
payments = page("/api/admin/payments")
assert payments["total"] == 4 and [p["id"] for p in payments["items"]] == ["order-3", "order-2", "order-1", "order-0"]
assert payments["items"][0]["owner_email"] == "user05@example.com" and "checkout_url" not in payments["items"][0]
assert "secret.example" not in client.get("/api/admin/payments").text
assert [p["id"] for p in page("/api/admin/payments", provider="onepay")["items"]] == ["order-2", "order-1"]
assert [p["id"] for p in page("/api/admin/payments", status="pending")["items"]] == ["order-3"]
assert [p["id"] for p in page("/api/admin/payments", q="USER11")["items"]] == ["order-1", "order-0"]
assert [p["id"] for p in page("/api/admin/payments", q="1000000000002")["items"]] == ["order-2"]
assert [p["id"] for p in page("/api/admin/payments", q="ref-0")["items"]] == ["order-0"]
assert client.post("/api/admin/payments/order-0/refresh").json()["status"] == "paid"  # paid orders are not asked again

# The header counts come from COUNT queries.
summary = page("/api/admin")
assert summary["counts"]["users"] == 26 and summary["counts"]["workspaces"] == 26 and summary["counts"]["plans"] == 3
assert summary["counts"]["pending_payments"] == 1 and summary["counts"]["admins"] == 1
assert "users" not in summary and "workspaces" not in summary  # no collection in the overview any more
assert {p["provider"] for p in summary["payment_providers"]} == {"payos", "bank_qr", "onepay"}

# Only system admins see any of it.
assert member.post("/api/login", json={"email": "user09@example.com", "password": "long-password-123"}).status_code == 200
for path in ("/api/admin", "/api/admin/users", "/api/admin/workspaces", "/api/admin/payments",
             f"/api/admin/users/{admin_id}", f"/api/admin/workspaces/{target['id']}", "/api/admin/payment-providers"):
    assert member.get(path).status_code == 403, path
assert member.post("/api/admin/payments/order-3/refresh").status_code == 403
print("admin ok")
'''


class AdminConsoleTest(unittest.TestCase):
    def test_paginated_users_studios_payments_and_counts(self):
        completed = run_program(ADMIN)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])


if __name__ == "__main__":
    unittest.main()
