"""PostgreSQL-only integration: what SQLite cannot show (row locks, SKIP LOCKED, ON CONFLICT under concurrency).

Runs when REELFORGE_TEST_DATABASE_URL names an isolated PostgreSQL test database (CI starts one). Every check
starts real concurrent transactions from threads:

* rate limit counters: ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` loses no attempt;
* notifications: ``ON CONFLICT DO NOTHING`` on the dedupe key, one row whatever the races;
* credit ledger: the account row lock and the unique reference credit once;
* payment settlement: concurrent confirmations of one order pay it once, credit once, extend once;
* plan limits: concurrent project creations never exceed the plan (the subscription row lock);
* checkout: concurrent orders get distinct codes and none fails;
* job claiming: ``FOR UPDATE SKIP LOCKED`` hands each job to one worker;
* workspace isolation through the API on PostgreSQL.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
import os, threading
from datetime import datetime, timedelta, timezone
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
config = Config("alembic.ini")
command.downgrade(config, "base")
command.upgrade(config, "head")
os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
import app.main as main
from app import jobs, notifications, payments, ratelimit, system_config, usage
from app.db import Session
from app.models import (CreditAccount, CreditLedger, Notification, PaymentOrder, Plan, Project, RateLimitBucket,
                        Subscription, User, WorkflowJob, WorkflowRun, WorkflowRunStep, Workflow)
system_config.activate()
ORIGIN = {"Origin": "http://testserver"}
owner = TestClient(main.app, headers=ORIGIN)
assert owner.post("/api/setup", json={"email": "owner@example.com", "password": "long-password-123"}).status_code == 200
dashboard = owner.get("/api/dashboard").json()
WS = dashboard["workspace"]["id"]
with Session() as db:
    OWNER = db.scalar(select(User.id).where(User.email == "owner@example.com"))

def parallel(fn, n=12):
    barrier, results, errors = threading.Barrier(n), [], []
    def run(index):
        barrier.wait()
        try:
            results.append(fn(index))
        except Exception as exc:
            errors.append(repr(exc))
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results, errors

# --- rate limit counters --------------------------------------------------------------------------------------------
_, errors = parallel(lambda i: ratelimit.hit("login_ip", "203.0.113.200", limit=1000), 20)
assert not errors, errors
with Session() as db:
    assert db.get(RateLimitBucket, ratelimit._key("login_ip", "203.0.113.200")).count == 20

# --- notifications deduplicated -------------------------------------------------------------------------------------
def notify(i):
    with Session.begin() as db:
        return notifications.notify(db, [OWNER], "support.new", "Hello", dedupe="same-event")
_, errors = parallel(notify)
assert not errors, errors
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.dedupe_key == "same-event")) == 1

# --- the credit ledger --------------------------------------------------------------------------------------------------
def credit(i):
    with Session.begin() as db:
        return usage.post_credit(db, WS, 10, "test", "grant-once")
_, errors = parallel(credit)
assert not errors, errors
with Session() as db:
    assert db.get(CreditAccount, WS).balance == 10
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reference == "grant-once")) == 1

# --- payment settlement -------------------------------------------------------------------------------------------------
with Session.begin() as db:
    db.execute(update(Plan).where(Plan.code == "standard").values(price_vnd=199000, monthly_credits=100))
    db.add(PaymentOrder(id="order-1", workspace_id=WS, plan_code="standard", provider="bank_qr", order_code=1234567890123,
                        amount_vnd=199000, credits_award=100, status="pending",
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
def settle(i):
    with Session.begin() as db:
        return payments.apply_paid(db, 1234567890123, 199000, "ref", provider="bank_qr")
statuses, errors = parallel(settle)
assert not errors and set(statuses) == {"paid"}, (statuses, errors)
with Session() as db:
    assert db.get(CreditAccount, WS).balance == 110
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reference == "payment:order-1")) == 1
    subscription = db.get(Subscription, WS)
    ends = subscription.ends_at if subscription.ends_at.tzinfo else subscription.ends_at.replace(tzinfo=timezone.utc)
    assert subscription.plan_code == "standard" and timedelta(days=29) < ends - datetime.now(timezone.utc) < timedelta(days=31)

# --- plan limits under concurrency ----------------------------------------------------------------------------------------
with Session.begin() as db:
    db.execute(update(Plan).where(Plan.code == "standard").values(project_limit=3))
def create(i):
    client = TestClient(main.app, headers=ORIGIN)
    for name, value in owner.cookies.items():
        client.cookies.set(name, value)
    return client.post("/api/projects", json={"title": f"P{i}", "topic": "t"}).status_code
codes, errors = parallel(create, 8)
assert not errors and sorted(codes).count(201) == 3 and codes.count(403) == 5, (codes, errors)
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Project).where(Project.workspace_id == WS)) == 3

# --- checkout: distinct orders, no failure ------------------------------------------------------------------------------------
with Session.begin() as db:
    system_config.save(db, None, values={"payments.vietqr_mode": "manual", "payments.bank_qr.enabled": True,
                                         "payments.bank_qr.bank_bin": "970436", "payments.bank_qr.account_number": "0123456789",
                                         "payments.bank_qr.account_name": "STUDIO", "payments.bank_qr.transfer_prefix": "RF"},
                       section="payments")
    db.execute(update(Plan).where(Plan.code == "pro").values(price_vnd=499000, is_active=True))
def checkout(i):
    client = TestClient(main.app, headers=ORIGIN)
    for name, value in owner.cookies.items():
        client.cookies.set(name, value)
    response = client.post("/api/billing/checkout", json={"plan_code": "pro", "method": "vietqr"})
    return response.status_code, response.json().get("order_id")
results, errors = parallel(checkout, 6)
assert not errors and all(code in (201, 429) for code, _ in results), (results, errors)
created = [order for code, order in results if code == 201]
assert created and len(set(created)) == len(created)
with Session() as db:
    codes = db.scalars(select(PaymentOrder.order_code).where(PaymentOrder.plan_code == "pro")).all()
    assert len(codes) == len(set(codes)) == len(created)

# --- job claiming: SKIP LOCKED gives each job to one worker ---------------------------------------------------------------------
workflow = owner.post("/api/workflows", json={"name": "Claims"}).json()["id"]
with Session.begin() as db:
    now = datetime.now(timezone.utc)
    db.add(WorkflowRun(id="run-1", workspace_id=WS, workflow_id=workflow, project_id=db.scalar(select(Project.id)),
                       graph_snapshot="{}", status="running", created_at=now))
    db.flush()
    for index in range(10):
        db.add(WorkflowRunStep(id=f"step-{index}", run_id="run-1", node_id=f"n{index}", node_type="video",
                               position=index, status="queued", detail=""))
    db.flush()
    for index in range(10):
        jobs.enqueue_job(db, workspace_id=WS, run_id="run-1", step_id=f"step-{index}",
                         logical_key=f"video:run-1:step-{index}", payload={"kind": "video"}, available_at=now)
def claim(i):
    with Session.begin() as db:
        return [job.id for job in jobs.claim_due_jobs(db, worker_id=f"worker-{i}", limit=2, logical_key_prefix="video:",
                                                      now=datetime.now(timezone.utc))]
claimed, errors = parallel(claim, 8)
flat = [job for batch in claimed for job in batch]
assert not errors and len(flat) == len(set(flat)) == 10, (claimed, errors)

# --- workspace isolation on PostgreSQL ---------------------------------------------------------------------------------------------
other = TestClient(main.app, headers=ORIGIN)
assert other.post("/api/register", json={"email": "other@example.com", "password": "other-password-12",
                                         "workspace_name": "Other", "accept_terms": True}).status_code == 201
mine = owner.get("/api/dashboard").json()["projects"][0]["id"]
assert other.patch(f"/api/projects/{mine}", json={"title": "Taken"}).status_code == 404
assert not other.get("/api/dashboard").json()["projects"]
assert other.post(f"/api/workspaces/{WS}/switch").status_code == 404
print("ok")
'''


class PostgreSQLConcurrencyTest(unittest.TestCase):
    def test_locks_upserts_settlement_quota_checkout_claims_isolation(self):
        url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
        if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({
                "database_url": url, "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            env = {key: value for key, value in os.environ.items() if not key.startswith("REELFORGE_TOKEN")}
            result = subprocess.run([sys.executable, "-c", PROGRAM], cwd=target,
                                    env={**env, "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"},
                                    capture_output=True, text=True, encoding="utf-8", timeout=900)
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])


if __name__ == "__main__":
    unittest.main()
