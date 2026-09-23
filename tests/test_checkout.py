"""Integration smoke test with isolated SQLite and simulated signed provider response."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CheckoutTest(unittest.TestCase):
    def test_subscription_changes_only_after_verified_payment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
import json
from types import SimpleNamespace
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.db import engine, Session
from app.models import PaymentOrder, Subscription
from app.usage import consume
@event.listens_for(engine, "connect")
def fk(connection, record): connection.execute("PRAGMA foreign_keys=ON")
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app import billing
client = TestClient(app)
assert client.post("/api/setup", json={"email": "owner@example.com", "password": "secret-pass-1234"}).status_code == 200
assert client.post("/api/billing/checkout", json={"plan_code": "pro"}).status_code == 503
billing.configured = lambda: True
billing.create_link = lambda *args: "https://pay.payos.vn/test"
assert client.post("/api/billing/checkout", json={"plan_code": "pro"}).status_code == 400
price = {"name": "Pro", "project_limit": None, "workflow_limit": None, "monthly_credits": 10, "is_active": True, "price_vnd": 50000}
assert client.put("/api/admin/plans/pro", json=price).status_code == 200
checkout = client.post("/api/billing/checkout", json={"plan_code": "pro"})
assert checkout.status_code == 201, checkout.text
order_id = checkout.json()["order_id"]
assert client.put("/api/admin/plans/pro", json={**price, "monthly_credits": 20}).status_code == 200
with Session() as db:
    order = db.get(PaymentOrder, order_id)
    code, workspace_id = order.order_code, order.workspace_id
assert client.get("/api/dashboard").json()["workspace"]["plan"] == "trial"
def invalid_signature(body): raise ValueError("Bad signature")
billing.verify_webhook = invalid_signature
assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 400
assert client.get("/api/dashboard").json()["workspace"]["plan"] == "trial"
billing.verify_webhook = lambda body: SimpleNamespace(order_code=code, amount=49999, currency="VND", reference="ref")
assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 400
billing.verify_webhook = lambda body: SimpleNamespace(order_code=code, amount=50000, currency="VND", reference="ref")
assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 200
with Session() as db:
    first_end = db.get(Subscription, workspace_id).ends_at
assert client.get("/api/dashboard").json()["workspace"]["plan"] == "pro"
assert client.get("/api/usage").json()["balance"] == 10
assert client.post("/api/webhooks/payos", json={"success": True, "code": "00"}).status_code == 200
with Session() as db:
    assert db.get(Subscription, workspace_id).ends_at == first_end
assert client.get("/api/usage").json()["balance"] == 10
with Session.begin() as db:
    assert consume(db, workspace_id, tool="script", units=2, credits=3, reference="job-1") == 7
with Session.begin() as db:
    assert consume(db, workspace_id, tool="script", units=2, credits=3, reference="job-1") == 7
assert client.get("/api/usage").json()["balance"] == 7
assert client.put("/api/admin/plans/pro", json=price).status_code == 200
renewal = client.post("/api/billing/checkout", json={"plan_code": "pro"})
assert renewal.status_code == 201, renewal.text
with Session() as db:
    renewal_code = db.get(PaymentOrder, renewal.json()["order_id"]).order_code
billing.get_payment = lambda order_code: SimpleNamespace(order_code=renewal_code, amount=50000, amount_paid=50000, status="PAID", id="provider-ref")
assert client.post(f"/api/billing/orders/{renewal.json()['order_id']}/refresh").status_code == 200
assert client.get("/api/usage").json()["balance"] == 17
with Session() as db:
    assert db.get(Subscription, workspace_id).ends_at > first_end
assert client.post(f"/api/billing/orders/{renewal.json()['order_id']}/refresh").status_code == 200
assert client.get("/api/usage").json()["balance"] == 17
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target, env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
