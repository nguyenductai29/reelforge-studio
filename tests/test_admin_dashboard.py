"""Admin → Overview: GET /api/admin/overview (app/admin_dashboard.py).

* system administrators only: a studio owner gets 403, nobody signed out gets 401;
* every figure is system-wide (every studio), with its exact period: active users signed in within 30 days, paid
  money only from paid orders paid in the 30 days, jobs created in the 24 hours or 30 days, growth per UTC day;
* AI usage by queue with credits by task; publishing by channel; the credit ledger by kind with balances and held
  credits; storage levels in one SQL aggregate equal to the per-studio computation; support with the oldest
  unanswered message;
* the health rows and the system status follow the live alert conditions and the readiness checks: a stopped worker
  or a failed backup is critical, a worker reporting errors a warning; what needs an administrator is listed with
  the tab that handles it;
* nothing secret or personal beyond what an administrator already sees: no checkout URL, provider reference, payload
  or member email.
"""
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program


ADMIN = r'''
import uuid
from app import admin_dashboard, storage
from app.db import engine
from app.heartbeat import WORKERS
from app.models import (BackupRun, CreditAccount, CreditLedger, PaymentOrder, Subscription, SupportMessage,
                        SupportTicket, User, WorkerHeartbeat, Workspace)
ORIGIN = {"Origin": "http://testserver"}
NOW = datetime.now(timezone.utc)
POSTGRES = engine.dialect.name == "postgresql"
# The media disk of the machine running the test is not under test: a fixed one, changed below on purpose.
disk = {"total_bytes": 1000, "used_bytes": 400, "free_bytes": 600, "percent": 40.0}
storage.disk_usage = lambda db: dict(disk)

def ago(**delta):
    return NOW - timedelta(**delta)

def overview(who=None):
    r = (who or client).get("/api/admin/overview")
    assert r.status_code == 200, r.text
    return r.json()

def studio(email, name):
    person = TestClient(app, headers=ORIGIN)
    r = person.post("/api/register", json={"email": email, "password": "studio-password-123", "workspace_name": name,
                                           "accept_terms": True})
    assert r.status_code == 201, r.text
    return person, person.get("/api/dashboard").json()["workspace"]["id"]

def keys(answer):
    return [item["key"] for item in answer["attention"]]

def attention(answer, key):
    found = [item for item in answer["attention"] if item["key"] == key]
    assert len(found) == 1, (key, answer["attention"])
    return found[0]

def row(answer, key):
    return next(item for item in answer["health"]["rows"] if item["key"] == key)

# Only system administrators.
first = overview()
member, member_ws = studio("member@example.com", "Member Studio")
assert member.get("/api/admin/overview").status_code == 403
assert TestClient(app).get("/api/admin/overview").status_code == 401
assert set(first) == {"generated_at", "periods", "overview", "attention", "health", "ai_usage", "credits", "payments",
                      "publishing", "storage", "support", "growth", "activity"}, set(first)
assert first["periods"]["days"] == 30 and first["periods"]["jobs_hours"] == 24 and len(first["growth"]["days"]) == 30
assert first["growth"]["days"][-1] == NOW.date().isoformat()
assert set(first["credits"].values()) == {0} and first["payments"]["paid"] == 0 and first["ai_usage"]["jobs"] == 0
# A new installation: no worker has reported and no backup ran yet; nothing is critical.
assert row(first, "workers")["missing"] == list(WORKERS) and row(first, "workers")["status"] == "warning"
assert row(first, "backups")["status"] == "warning" and row(first, "backups")["detail"] == "never"
assert row(first, "database")["status"] == ("healthy" if POSTGRES else "warning")
assert first["health"]["status"] == first["overview"]["system_status"] != "critical"
assert attention(first, "workers_missing")["count"] == len(WORKERS) and attention(first, "backup_never")["severity"] == "info"

second, second_ws = studio("second@example.com", "Second Studio")
third, third_ws = studio("third@example.com", "Third Studio")
# Sign-ins, sign-ups and studios at known times; a backup and every worker's heartbeat, so they are healthy.
moments = {"owner@example.com": (ago(days=1), NOW, True), "member@example.com": (ago(days=40), ago(days=3), True),
           "second@example.com": (ago(days=2), NOW, False),  # signed in lately, but locked
           "third@example.com": (None, ago(days=45), True)}
with Session.begin() as db:
    for user in db.scalars(select(User)):
        user.last_login_at, user.created_at, user.is_active = moments[user.email]
    for ws in db.scalars(select(Workspace)):
        ws.created_at = {workspace: NOW, member_ws: ago(days=3), second_ws: ago(days=3), third_ws: ago(days=45)}[ws.id]
    db.add(BackupRun(kind="scheduled", status="succeeded", started_at=ago(hours=2), finished_at=ago(hours=2),
                     filename="db.dump", bytes=1000))
    for name in WORKERS:
        db.add(WorkerHeartbeat(worker=name, host="test", pid=1, status="running", started_at=ago(hours=1),
                               last_seen_at=NOW))
    db.add(Plan(code="studio_pro", name="Studio Pro", project_limit=None, workflow_limit=None, monthly_credits=1000,
                is_active=True, price_vnd=199000, storage_limit_bytes=1000))
    db.get(Plan, "trial").storage_limit_bytes = 1000
    db.flush()
    db.get(Subscription, member_ws).plan_code = "studio_pro"
    db.get(Subscription, member_ws).ends_at = NOW + timedelta(days=20)
    db.get(Subscription, second_ws).ends_at = ago(days=1)  # expired

# Payments: money only from paid orders paid in the period.
orders = [("payos", "paid", 199000, ago(days=1), ago(days=1)), ("bank_qr", "paid_unapplied", 99000, ago(days=2), ago(days=2)),
          ("onepay", "paid", 500000, ago(days=41), ago(days=40)), ("bank_qr", "awaiting_confirmation", 199000, ago(days=1), None),
          ("payos", "pending", 199000, ago(days=3), None), ("onepay", "failed", 199000, ago(days=5), None),
          ("payos", "expired", 199000, ago(days=50), None), ("bank_qr", "rejected", 199000, ago(days=2), None)]
with Session.begin() as db:
    for index, (provider, status, amount, created, paid) in enumerate(orders):
        db.add(PaymentOrder(id=str(uuid.uuid4()), workspace_id=member_ws, plan_code="studio_pro", provider=provider,
                            order_code=900000 + index, amount_vnd=amount, credits_award=1000, status=status,
                            created_at=created, paid_at=paid, checkout_url="https://checkout.example/SECRET-URL",
                            provider_reference=f"REF-SECRET-{index}"))

# Jobs of every queue at known times; usage events; publications.
with Session.begin() as db:
    flow = Workflow(id=str(uuid.uuid4()), workspace_id=workspace, name="Jobs", definition=json.dumps({"nodes": [], "edges": []}))
    db.add(flow)
    db.flush()
    runs = []
    for _ in range(2):
        run = WorkflowRun(id=str(uuid.uuid4()), workspace_id=workspace, workflow_id=flow.id, project_id=project,
                          graph_snapshot="{}", status="running", created_at=NOW)
        db.add(run)
        runs.append(run)
    db.flush()
    step = WorkflowRunStep(id=str(uuid.uuid4()), run_id=runs[0].id, node_id="n", node_type="script", position=0,
                           status="running", detail="")
    db.add(step)
    db.flush()

    def job(queue, state, created, **values):
        db.add(WorkflowJob(id=str(uuid.uuid4()), workspace_id=workspace, run_id=runs[0].id, step_id=step.id,
                           logical_key=f"{queue}:{uuid.uuid4()}", payload_json='{"api_key": "SECRET-PAYLOAD"}',
                           state=state, available_at=values.pop("available_at", created), attempt_count=1,
                           created_at=created, updated_at=values.pop("updated_at", created), **values))

    for _ in range(3):
        job("text", "succeeded", ago(hours=1))
    job("text", "failed", ago(days=2))
    job("text", "queued", ago(minutes=20))  # due 20 minutes ago and never claimed: stuck
    job("video", "succeeded", ago(days=3))
    job("video", "succeeded", ago(days=3))
    job("video", "leased", ago(hours=3), lease_expires_at=ago(minutes=5))  # its worker stopped: stuck
    job("image", "failed", ago(hours=5))
    job("render", "succeeded", ago(days=40))  # before the period
    job("publish", "succeeded", ago(hours=2))  # publishing, not generation
    job("publish", "failed", ago(hours=2))
    for tool, credits, when in (("openai/text", 10, ago(days=1)), ("fal/video", 50, ago(days=2)),
                                ("openai/transcription", 7, ago(days=3)), ("ffmpeg/render", 3, ago(days=40)),
                                ("legacy", 5, ago(days=1))):
        db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=workspace, tool=tool, units=1, credits=credits,
                          reference=str(uuid.uuid4()), created_at=when))
    clip = Asset(id=str(uuid.uuid4()), workspace_id=workspace, project_id=project, run_id=runs[0].id, filename="final.mp4",
                 content_type="video/mp4", bytes=100, created_at=NOW)
    db.add(clip)
    db.flush()
    for run, channel, state, when, extra in ((runs[0], "youtube", "succeeded", ago(days=2), {"published_at": ago(days=2)}),
                                             (runs[1], "youtube", "succeeded", ago(days=40), {"published_at": ago(days=40)}),
                                             (runs[0], "tiktok", "failed", ago(days=1), {}),
                                             (runs[1], "tiktok", "needs_attention", ago(days=40), {}),
                                             (runs[0], "facebook", "scheduled", ago(days=1), {"scheduled_for": NOW + timedelta(days=1)})):
        db.add(Publication(id=str(uuid.uuid4()), workspace_id=workspace, run_id=run.id, asset_id=clip.id, channel=channel,
                           title="Clip", description="", state=state, created_at=when, updated_at=when, **extra))
    # Storage against a 1000-byte quota: 85 % (with the clip), 95 %, 75 % and 100 %.
    for ws, size in ((workspace, 750), (member_ws, 950), (second_ws, 750), (third_ws, 1000)):
        db.add(Asset(id=str(uuid.uuid4()), workspace_id=ws, filename="media.bin", content_type="video/mp4", bytes=size,
                     created_at=NOW))
    db.add(Asset(id=str(uuid.uuid4()), workspace_id=member_ws, filename="gone.bin", content_type="video/mp4", bytes=0,
                 created_at=NOW))  # expired: no file any more

# An administrator's credit adjustment (the activity), then the ledger and balances at known values.
assert client.post(f"/api/admin/workspaces/{third_ws}/credits", json={"delta": 25, "reason": "welcome gift"}).status_code == 200
with Session.begin() as db:
    for reason, delta, when in (("subscription", 100, ago(days=1)), ("admin: bonus", 50, ago(days=2)),
                                ("admin: fix", -20, ago(days=2)), ("text_refund", 5, ago(days=1)),
                                ("video_reserve", -30, ago(days=1)), ("usage", -10, ago(days=1)), ("test", 7, ago(days=1)),
                                ("subscription", 1000, ago(days=40))):
        db.add(CreditLedger(id=str(uuid.uuid4()), workspace_id=member_ws, delta=delta, reason=reason,
                            reference=str(uuid.uuid4()), created_at=when))
    for account in db.scalars(select(CreditAccount)):
        account.balance = {workspace: 11, member_ws: 22, second_ws: 33, third_ws: 44}[account.workspace_id]

# Support: two high-priority tickets and one waiting since a user's message 5 hours ago.
with Session.begin() as db:
    users = {user.email: user.id for user in db.scalars(select(User))}

    def ticket(ws, status, priority, messages):
        ticket_id = str(uuid.uuid4())
        db.add(SupportTicket(id=ticket_id, workspace_id=ws, created_by_user_id=users["member@example.com"], subject="Help",
                             category="other", status=status, priority=priority, created_at=ago(hours=10),
                             updated_at=ago(minutes=30)))
        db.flush()
        for kind, when in messages:
            author = users["owner@example.com"] if kind == "admin" else users["member@example.com"]
            db.add(SupportMessage(id=str(uuid.uuid4()), ticket_id=ticket_id, author_user_id=author, author_type=kind,
                                  body="SECRET-MESSAGE", created_at=when))

    ticket(member_ws, "open", "high", [("user", ago(hours=2))])
    ticket(member_ws, "waiting_support", "normal", [("user", ago(hours=9)), ("admin", ago(hours=8)), ("user", ago(hours=5))])
    ticket(member_ws, "waiting_user", "normal", [("user", ago(hours=30)), ("admin", ago(hours=20))])
    ticket(member_ws, "resolved", "high", [("user", ago(days=3))])
    ticket(third_ws, "open", "high", [("user", ago(hours=1))])

data = overview()
o = data["overview"]
assert (o["users"], o["active_users"], o["workspaces"]) == (4, 1, 4), o
assert (o["active_subscriptions"], o["paid_subscriptions"]) == (3, 1), o
assert (o["paid_orders"], o["paid_amount_vnd"], o["jobs_24h"]) == (2, 298000, 6), o

assert data["payments"] == {"paid": 2, "paid_amount_vnd": 298000, "by_provider": [
    {"provider": "bank_qr", "paid": 1, "amount_vnd": 99000}, {"provider": "payos", "paid": 1, "amount_vnd": 199000}],
    "pending": 1, "awaiting_confirmation": 1, "paid_unapplied": 1, "failed": 2}, data["payments"]

usage = data["ai_usage"]
tasks = {task["task"]: task for task in usage["tasks"]}
assert list(tasks) == ["text", "image", "video", "voice", "source", "render"]
assert tasks["text"] == {"task": "text", "jobs": 5, "jobs_24h": 4, "succeeded": 3, "failed": 1, "active": 1, "credits": 10}
assert tasks["image"] == {"task": "image", "jobs": 1, "jobs_24h": 1, "succeeded": 0, "failed": 1, "active": 0, "credits": 0}
assert tasks["video"] == {"task": "video", "jobs": 3, "jobs_24h": 1, "succeeded": 2, "failed": 0, "active": 1, "credits": 50}
assert (tasks["source"]["credits"], tasks["source"]["jobs"], tasks["render"]["jobs"], tasks["render"]["credits"]) == (7, 0, 0, 0)
assert (usage["jobs"], usage["jobs_24h"], usage["succeeded"], usage["failed"], usage["active"], usage["credits"]) == \
    (9, 6, 5, 2, 2, 72), usage

assert data["credits"] == {"added_by_plans": 100, "admin_added": 75, "admin_removed": 20, "admin_adjustments": 3,
                           "refunded": 5, "other_added": 7, "consumed": 72, "available": 110, "held": 0,
                           "held_jobs": 0}, data["credits"]

assert data["publishing"] == {"channels": [
    {"channel": "youtube", "configured": True, "published": 1, "scheduled": 0, "failed": 0},
    {"channel": "tiktok", "configured": True, "published": 0, "scheduled": 0, "failed": 1},
    {"channel": "facebook", "configured": True, "published": 0, "scheduled": 1, "failed": 0}],
    "published": 1, "scheduled": 1, "failed": 1}, data["publishing"]

store = data["storage"]
assert (store["stored_bytes"], store["files"]) == (3550, 5), store
assert store["levels"] == {"full": 1, "critical": 1, "warning": 1, "notice": 1}, store["levels"]
assert store["disk"] == disk
with Session() as db:  # the SQL aggregate counts as the per-studio computation does
    python_levels = {name: 0 for _, name in storage.LEVELS}
    for item in storage.usage_by_workspace(db).values():
        if item["level"] in python_levels:
            python_levels[item["level"]] += 1
    assert storage.level_counts(db) == python_levels, (storage.level_counts(db), python_levels)

support = data["support"]
assert (support["awaiting_support"], support["waiting_user"], support["high_priority"]) == (3, 1, 2), support
assert abs(datetime.fromisoformat(support["oldest_waiting_since"]) - ago(hours=5)) < timedelta(seconds=1), support

growth = data["growth"]
assert growth["days"][-1] == NOW.date().isoformat() and growth["days"][-4] == (NOW - timedelta(days=3)).date().isoformat()
assert (growth["users"][-1], growth["users"][-4], sum(growth["users"])) == (2, 1, 3), growth["users"]
assert (growth["workspaces"][-1], growth["workspaces"][-4], sum(growth["workspaces"])) == (1, 2, 3), growth["workspaces"]

activity = data["activity"]
assert activity[0]["action"] == "admin.credits_adjusted" and activity[0]["actor"] == "owner@example.com"
assert activity[0]["outcome"] == "success" and set(activity[0]) == {"id", "action", "outcome", "actor", "created_at"}

# What needs an administrator, each with the tab (and filter) that handles it.
assert attention(data, "transfers") == {"key": "transfers", "severity": "warning", "tab": "payments",
                                        "filter": "awaiting_confirmation", "count": 1}
assert attention(data, "paid_unapplied")["filter"] == "paid_unapplied"
assert attention(data, "stuck_jobs") == {"key": "stuck_jobs", "severity": "warning", "tab": "operations", "count": 2}
assert attention(data, "failed_jobs")["count"] == 2
assert attention(data, "studios_storage") == {"key": "studios_storage", "severity": "warning", "tab": "operations",
                                              "count": 2, "full": 1}
assert attention(data, "support_high") == {"key": "support_high", "severity": "warning", "tab": "support", "filter": "high",
                                           "count": 2}
waiting = attention(data, "support_waiting")
assert waiting["severity"] == "info" and waiting["count"] == 3 and waiting["since"] == support["oldest_waiting_since"]
assert "workers_missing" not in keys(data) and "backup_never" not in keys(data) and "reconciliation" not in keys(data)
severities = [item["severity"] for item in data["attention"]]
assert severities == sorted(severities, key=["critical", "warning", "info"].index), severities
assert row(data, "workers")["status"] == "healthy" and row(data, "workers")["healthy"] == len(WORKERS)
assert row(data, "backups")["status"] == "healthy" and row(data, "storage") == {
    "key": "storage", "status": "healthy", "detail": None, "percent": 40.0, "used_bytes": 400, "total_bytes": 1000,
    "free_bytes": 600}
assert data["health"]["status"] == admin_dashboard.worst(item["status"] for item in data["health"]["rows"])

# Nothing secret, and no member's personal data beyond the administrator's own activity.
text = json.dumps(data)
for secret in ("SECRET-URL", "REF-SECRET", "SECRET-PAYLOAD", "SECRET-MESSAGE", "api_key", "welcome gift",
               "member@example.com", "second@example.com", "third@example.com", "password", "token"):
    assert secret not in text, secret
no_secrets(text)

# The system status follows the alert conditions: a stopped worker or a failed backup is critical.
with Session.begin() as db:
    db.get(WorkerHeartbeat, "text_worker").last_seen_at = ago(minutes=10)
stopped = overview()
assert stopped["overview"]["system_status"] == stopped["health"]["status"] == "critical"
assert row(stopped, "workers")["status"] == "critical" and row(stopped, "workers")["stale"] == ["text_worker"]
assert stopped["attention"][0] == {"key": "worker_stale", "severity": "critical", "tab": "operations",
                                   "worker": "text_worker"}
with Session.begin() as db:
    db.get(WorkerHeartbeat, "text_worker").last_seen_at = NOW
    db.get(WorkerHeartbeat, "image_worker").status = "error"
erring = overview()
assert row(erring, "workers")["status"] == "warning" and erring["overview"]["system_status"] in ("warning",)
assert attention(erring, "worker_error") == {"key": "worker_error", "severity": "warning", "tab": "operations",
                                             "worker": "image_worker"}
with Session.begin() as db:
    db.get(WorkerHeartbeat, "image_worker").status = "running"
    db.add(BackupRun(kind="scheduled", status="failed", started_at=ago(minutes=30), error="disk full"))
disk.update(percent=95.0, used_bytes=950, free_bytes=50)
failing = overview()
assert row(failing, "backups")["status"] == "critical" and row(failing, "backups")["detail"] == "failed"
assert row(failing, "storage")["status"] == "critical" and failing["overview"]["system_status"] == "critical"
assert attention(failing, "backup_failed") == {"key": "backup_failed", "severity": "critical", "tab": "system",
                                               "filter": "backups", "max_age_hours": None}
assert attention(failing, "disk") == {"key": "disk", "severity": "critical", "tab": "operations", "percent": 95.0}
assert [item["severity"] for item in failing["attention"]][:2] == ["critical", "critical"]
assert admin_dashboard.worst(["healthy", "not_configured"]) == "healthy"
assert admin_dashboard.worst(["healthy", "warning", "not_configured"]) == "warning"
assert admin_dashboard.worst(["warning", "critical"]) == "critical"
print("admin overview ok")
'''


class AdminDashboardTest(unittest.TestCase):
    def test_admin_overview(self):
        result = run_program(ADMIN)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])
        self.assertIn("admin overview ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
