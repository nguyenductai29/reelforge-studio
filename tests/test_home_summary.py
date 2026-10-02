"""Home, the user dashboard: GET /api/home (app/home.py).

* the overview counts the studio's own credits, projects, runs and publications of the last 30 days, storage;
* "Needs attention": each kind once, with its count and the newest item to open, and only what the member's role
  can act on (a viewer gets none of the warnings; editors neither reconnect channels nor, when the studio says
  so, publish);
* recent workflows (by their last run, then the ones never run), projects (by their last activity, with the board
  status, videos, channels reached and a cover) and runs;
* AI usage of the period by task; publishing by channel, only for channels connected or used;
* another studio's data never appears; anonymous requests are refused; nothing secret or system-wide is sent.
"""
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program


HOME = r'''
import uuid
from app import storage
from app.models import Subscription, SupportTicket, User, WorkspaceSetting
ORIGIN = {"Origin": "http://testserver"}
NOW = datetime.now(timezone.utc)

def ago(**delta):
    return NOW - timedelta(**delta)

def browser():
    return TestClient(app, headers=ORIGIN)

def studio(email, name):
    person = browser()
    r = person.post("/api/register", json={"email": email, "password": "studio-password-123", "workspace_name": name,
                                           "accept_terms": True})
    assert r.status_code == 201, r.text
    return person, person.get("/api/dashboard").json()["workspace"]["id"]

def join(email, role):
    link = client.post("/api/workspace/invites", json={"email": email, "role": role}).json()["link"]
    person = browser()
    r = person.post("/api/register", json={"email": email, "password": f"{role}-password-123", "accept_terms": True,
                                           "invite_token": link.split("#token=", 1)[1]})
    assert r.status_code == 201, r.text
    return person

def home(who=client):
    r = who.get("/api/home")
    assert r.status_code == 200, r.text
    return r.json()

def kinds(answer):
    return [item["kind"] for item in answer["attention"]]

def attention(answer, kind):
    found = [item for item in answer["attention"] if item["kind"] == kind]
    assert len(found) == 1, (kind, answer["attention"])
    return found[0]

def credit(ws, amount, reference):
    with Session.begin() as db:
        usage.post_credit(db, ws, amount, "test", reference)

def add_workflow(ws, name):
    workflow_id = str(uuid.uuid4())
    with Session.begin() as db:
        db.add(Workflow(id=workflow_id, workspace_id=ws, name=name, definition=json.dumps({"nodes": [], "edges": []})))
    return workflow_id

def add_project(ws, title, created_at, status="draft"):
    project_id = str(uuid.uuid4())
    with Session.begin() as db:
        db.add(Project(id=project_id, workspace_id=ws, title=title, topic=f"About {title}", status=status,
                       created_at=created_at))
    return project_id

def add_run(ws, workflow_id, project_id, status, created_at, retry_of=None):
    run_id = str(uuid.uuid4())
    with Session.begin() as db:
        db.add(WorkflowRun(id=run_id, workspace_id=ws, workflow_id=workflow_id, project_id=project_id,
                           retry_of_id=retry_of, graph_snapshot=json.dumps({"nodes": [], "edges": []}), status=status,
                           created_at=created_at,
                           finished_at=None if status in ("running", "queued", "awaiting_review") else created_at))
    return run_id

def add_asset(ws, project_id, run_id, content_type, size, created_at):
    asset_id = str(uuid.uuid4())
    with Session.begin() as db:
        db.add(Asset(id=asset_id, workspace_id=ws, project_id=project_id, run_id=run_id, filename=asset_id[:8],
                     content_type=content_type, bytes=size, created_at=created_at))
    return asset_id

def add_publication(ws, run_id, asset_id, channel, state, updated_at, **times):
    with Session.begin() as db:
        db.add(Publication(id=str(uuid.uuid4()), workspace_id=ws, run_id=run_id, asset_id=asset_id, channel=channel,
                           title="Clip", description="", state=state, created_at=updated_at, updated_at=updated_at,
                           **times))

def add_usage(ws, tool, credits, created_at):
    with Session.begin() as db:
        db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=ws, tool=tool, units=1, credits=credits,
                          reference=str(uuid.uuid4()), created_at=created_at))

def ticket(ws, author, status, updated_at):
    ticket_id = str(uuid.uuid4())
    with Session.begin() as db:
        user_id = db.scalar(select(User.id).where(User.email == author))
        db.add(SupportTicket(id=ticket_id, workspace_id=ws, created_by_user_id=user_id, subject="Help", category="other",
                             status=status, priority="normal", created_at=updated_at, updated_at=updated_at))
    return ticket_id

# A new studio: its one project, nothing run or published, no credits (low, for its owner).
assert balance() == 0
fresh = home()
assert fresh["period_days"] == 30
overview = fresh["overview"]
assert (overview["projects"], overview["runs_30d"], overview["runs_active"], overview["published_30d"]) == (1, 0, 0, 0)
assert overview["credits"] == 0 and overview["credits_low_threshold"] == 20, overview
assert overview["storage_used_bytes"] == 0 and overview["storage_level"] == "ok" and overview["storage_quota_bytes"] > 0
assert fresh["attention"] == [{"kind": "credits_low", "severity": "warning", "balance": 0, "threshold": 20}]
assert fresh["recent_workflows"] == [] and fresh["recent_runs"] == []
assert [(item["title"], item["status"], item["videos"]) for item in fresh["recent_projects"]] == [("Rừng đêm", "draft", 0)]
assert fresh["usage"] == {"credits_used": 0, "by_task": []}
# Channels the server could use but the studio never connected nor used: no metric.
assert fresh["publishing"] == {"channels": [], "published": 0, "scheduled": 0, "failed": 0}, fresh["publishing"]

# Runs: what waits (review, failures nobody retried, stopped runs, held credits) and what is generating.
runs_owner, runs_ws = studio("runs@example.com", "Runs Studio")
credit(runs_ws, 100, "fund-runs")
forest = add_project(runs_ws, "Night forest", ago(days=60))
alpha, beta, gamma, delta, echo = (add_workflow(runs_ws, name) for name in ("Alpha", "Beta", "Gamma", "Delta", "Echo"))
review = add_run(runs_ws, alpha, forest, "awaiting_review", ago(hours=1))
active = add_run(runs_ws, beta, forest, "running", ago(minutes=5))
queued = add_run(runs_ws, beta, forest, "queued", ago(minutes=10))
failed = add_run(runs_ws, echo, forest, "failed", ago(days=2))
add_run(runs_ws, echo, forest, "failed", ago(days=40))  # before the period
retried = add_run(runs_ws, alpha, forest, "failed", ago(days=3))
add_run(runs_ws, alpha, forest, "completed", ago(days=3) + timedelta(hours=1), retry_of=retried)
blocked = add_run(runs_ws, beta, forest, "blocked", ago(days=1))
held = add_run(runs_ws, alpha, forest, "needs_attention", ago(hours=6))
runs = home(runs_owner)
assert kinds(runs) == ["review", "failed_runs", "blocked_runs", "reconciliation", "generating"], runs["attention"]
assert attention(runs, "review") == {"kind": "review", "severity": "warning", "count": 1, "run_id": review,
                                     "workflow_id": alpha}
assert attention(runs, "failed_runs") == {"kind": "failed_runs", "severity": "warning", "count": 1, "run_id": failed,
                                          "workflow_id": echo}
assert attention(runs, "blocked_runs")["run_id"] == blocked
assert attention(runs, "reconciliation") == {"kind": "reconciliation", "severity": "info", "count": 1, "run_id": held,
                                             "workflow_id": alpha}
assert attention(runs, "generating") == {"kind": "generating", "severity": "info", "count": 2, "run_id": active,
                                         "workflow_id": beta}
assert (runs["overview"]["runs_30d"], runs["overview"]["runs_active"]) == (8, 2), runs["overview"]
# Workflows by their last run, then the ones never run, by name; four of them.
assert [item["name"] for item in runs["recent_workflows"]] == ["Beta", "Alpha", "Echo", "Delta"]
latest = runs["recent_workflows"][0]["last_run"]
assert (latest["id"], latest["status"], latest["project_id"], latest["project_title"]) == \
    (active, "running", forest, "Night forest"), latest
assert latest["created_at"] and latest["finished_at"] is None
assert runs["recent_workflows"][3] == {"id": delta, "name": "Delta", "last_run": None}
assert [item["id"] for item in runs["recent_runs"]] == [active, queued, review, held, blocked, failed]
first = runs["recent_runs"][0]
assert (first["workflow_id"], first["workflow_name"], first["project_id"], first["project_title"], first["status"]) == \
    (beta, "Beta", forest, "Night forest", "running"), first
assert [item["status"] for item in runs["recent_projects"]] == ["review"]  # awaiting review outranks running

# Projects: by last activity, with the board's status, their videos, the channels they reached and a cover.
board_owner, board_ws = studio("board@example.com", "Board Studio")
flow = add_workflow(board_ws, "Board flow")
shipped = add_project(board_ws, "Published one", ago(days=20))
add_project(board_ws, "Ready one", ago(hours=12), status="approved")
waiting = add_project(board_ws, "Waiting one", ago(days=10))
busy = add_project(board_ws, "Busy one", ago(days=5))
add_project(board_ws, "Quiet one", ago(days=1))
add_project(board_ws, "Older one", ago(days=2))
add_project(board_ws, "Oldest one", ago(days=3))  # the seventh by activity: not on Home
done = add_run(board_ws, flow, shipped, "completed", ago(hours=3))
add_run(board_ws, flow, waiting, "awaiting_review", ago(hours=2))
add_run(board_ws, flow, busy, "running", ago(minutes=1))
video = add_asset(board_ws, shipped, done, "video/mp4", 100, ago(hours=3))
add_asset(board_ws, shipped, done, "video/mp4", 100, ago(hours=3))
add_asset(board_ws, shipped, done, "video/mp4", 0, ago(hours=3))  # expired: its file is gone
add_asset(board_ws, shipped, done, "image/png", 50, ago(hours=4))
cover = add_asset(board_ws, shipped, done, "image/jpeg", 50, ago(hours=3))
add_publication(board_ws, done, video, "youtube", "succeeded", ago(hours=2), published_at=ago(hours=2))
add_publication(board_ws, done, video, "tiktok", "failed", ago(hours=2))
projects = home(board_owner)["recent_projects"]
assert [item["title"] for item in projects] == ["Busy one", "Waiting one", "Published one", "Ready one", "Quiet one",
                                                "Older one"], projects
assert [item["status"] for item in projects] == ["generating", "review", "published", "ready", "draft", "draft"]
shown = projects[2]
assert (shown["id"], shown["videos"], shown["channels"], shown["cover_asset_id"]) == (shipped, 2, ["youtube"], cover)
assert shown["topic"] == "About Published one" and shown["created_at"] and shown["last_activity_at"]
assert shown["last_activity_at"] > shown["created_at"]
assert (projects[3]["videos"], projects[3]["channels"], projects[3]["cover_asset_id"]) == (0, [], None)

# Publishing: per channel, published and failed in the period, and what is scheduled.
connect_youtube()
connect_channel("tiktok", scope="user.info.basic")  # the grant lacks the upload scope: reconnect
publisher = add_workflow(workspace, "Publisher")
clips = []
for days in (1, 40, 2, 1, 45, 3):
    run_id = add_run(workspace, publisher, project, "completed", ago(days=days, hours=1))
    clips.append((run_id, add_asset(workspace, project, run_id, "video/mp4", 100, ago(days=days, hours=1))))
add_publication(workspace, *clips[0], "youtube", "succeeded", ago(days=1), published_at=ago(days=1))
add_publication(workspace, *clips[1], "youtube", "succeeded", ago(days=40), published_at=ago(days=40))
add_publication(workspace, *clips[2], "tiktok", "failed", ago(days=2))
add_publication(workspace, *clips[3], "tiktok", "needs_attention", ago(days=1))
add_publication(workspace, *clips[4], "tiktok", "failed", ago(days=45))  # long forgotten
add_publication(workspace, *clips[5], "facebook", "scheduled", ago(days=3), scheduled_for=NOW + timedelta(days=1))
owner_home = home()
assert owner_home["publishing"] == {
    "channels": [{"channel": "youtube", "status": "connected", "published": 1, "scheduled": 0, "failed": 0},
                 {"channel": "tiktok", "status": "authorization_required", "published": 0, "scheduled": 0, "failed": 2},
                 {"channel": "facebook", "status": "not_connected", "published": 0, "scheduled": 1, "failed": 0}],
    "published": 1, "scheduled": 1, "failed": 2}, owner_home["publishing"]
assert owner_home["overview"]["published_30d"] == 1 and owner_home["overview"]["runs_30d"] == 4
assert attention(owner_home, "publish_failed") == {"kind": "publish_failed", "severity": "warning", "count": 2}
assert [item for item in owner_home["attention"] if item["kind"] == "channel_reconnect"] == [
    {"kind": "channel_reconnect", "severity": "warning", "channel": "tiktok", "scheduled": 0},
    {"kind": "channel_reconnect", "severity": "warning", "channel": "facebook", "scheduled": 1}], owner_home["attention"]
assert owner_home["recent_projects"][0]["status"] == "published" and owner_home["recent_projects"][0]["channels"] == ["youtube"]

# AI usage of the period by task (the tool's suffix), most credits first; older usage is left out.
for tool, credits, when in (("openai/text", 2, ago(days=1)), ("openai/text", 1, ago(days=2)), ("fal/video", 10, ago(days=3)),
                            ("ffmpeg/render", 1, ago(hours=1)), ("gemini/image", 5, ago(days=40)), ("legacy", 2, ago(days=1))):
    add_usage(workspace, tool, credits, when)
assert home()["usage"] == {"credits_used": 16, "by_task": [
    {"task": "video", "credits": 10, "events": 1}, {"task": "text", "credits": 3, "events": 2},
    {"task": "other", "credits": 2, "events": 1}, {"task": "render", "credits": 1, "events": 1}]}, home()["usage"]

# Credits: low below the threshold, and no longer once topped up.
credit(workspace, 100, "fund-prelude")
assert "credits_low" not in kinds(home()) and home()["overview"]["credits"] == 100

# Storage at 85 % of the plan's quota.
with Session.begin() as db:
    plan = db.get(Plan, db.get(Subscription, workspace).plan_code)
    stored, previous_limit = storage.stored_bytes(db, workspace), plan.storage_limit_bytes
    plan.storage_limit_bytes = stored * 100 // 85
crowded = home()
assert crowded["overview"]["storage_level"] == "warning" and crowded["overview"]["storage_used_bytes"] == stored
item = attention(crowded, "storage")
assert item["level"] == "warning" and 84 <= item["percent"] <= 86, item

# Roles: each member sees the same studio, but only the attention items their role can act on.
editor, viewer = join("editor@example.com", "editor"), join("viewer@example.com", "viewer")
mine = ticket(workspace, "owner@example.com", "waiting_user", ago(hours=2))
theirs = ticket(workspace, "editor@example.com", "waiting_user", ago(hours=1))
ticket(workspace, "editor@example.com", "waiting_support", ago(minutes=30))  # waiting for support, not for them
ticket(runs_ws, "runs@example.com", "waiting_user", ago(minutes=1))  # another studio's
owner_home, editor_home, viewer_home = home(), home(editor), home(viewer)
assert kinds(owner_home) == ["publish_failed", "channel_reconnect", "channel_reconnect", "storage", "support_reply"], \
    owner_home["attention"]
assert attention(owner_home, "support_reply") == {"kind": "support_reply", "severity": "warning", "count": 2,
                                                  "ticket_id": theirs}
assert kinds(editor_home) == ["publish_failed", "storage", "support_reply"], editor_home["attention"]
assert attention(editor_home, "support_reply")["count"] == 1 and attention(editor_home, "support_reply")["ticket_id"] == theirs
assert viewer_home["attention"] == [], viewer_home["attention"]
for key in ("overview", "recent_workflows", "recent_projects", "recent_runs", "usage", "publishing"):
    assert viewer_home[key] == owner_home[key], key
# Editors who may not publish: no publishing failure to fix.
with Session.begin() as db:
    db.merge(WorkspaceSetting(workspace_id=workspace, key="editors_can_publish", value="false"))
assert kinds(home(editor)) == ["storage", "support_reply"]
# An expired plan stops creation: owners and editors hear of it, viewers do not.
with Session.begin() as db:
    db.get(Subscription, workspace).ends_at = ago(days=1)
assert attention(home(), "plan_inactive") == {"kind": "plan_inactive", "severity": "warning", "status": "expired"}
assert kinds(home(editor))[0] == "plan_inactive" and home(viewer)["attention"] == []
# Low credits block the people who run workflows, not viewers.
credit(workspace, -95, "spend-prelude")
assert attention(home(editor), "credits_low")["balance"] == 5 and home(viewer)["attention"] == []

# Another studio sees none of it; nobody signed out sees anything; nothing secret or system-wide.
stranger, stranger_ws = studio("stranger@example.com", "Stranger Studio")
alone = home(stranger)
assert (alone["overview"]["projects"], alone["overview"]["runs_30d"], alone["overview"]["published_30d"]) == (0, 0, 0)
assert alone["overview"]["storage_used_bytes"] == 0 and alone["overview"]["credits"] == 0
assert alone["recent_projects"] == [] and alone["recent_workflows"] == [] and alone["recent_runs"] == []
assert alone["usage"] == {"credits_used": 0, "by_task": []} and alone["publishing"]["channels"] == []
assert kinds(alone) == ["credits_low"], alone["attention"]
text = json.dumps(alone)
for foreign in (workspace, project, runs_ws, board_ws, review, cover, theirs, "Night forest", "Board flow"):
    assert foreign not in text, foreign
assert TestClient(app).get("/api/home").status_code == 401
for who in (client, editor, viewer):
    answer = home(who)
    no_secrets(json.dumps(answer))
    assert set(answer) == {"period_days", "overview", "attention", "recent_workflows", "recent_projects", "recent_runs",
                           "usage", "publishing"}, set(answer)
with Session.begin() as db:
    db.get(Plan, db.get(Subscription, workspace).plan_code).storage_limit_bytes = previous_limit
print("home ok")
'''


class HomeSummaryTest(unittest.TestCase):
    def test_home_summary(self):
        result = run_program(HOME)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-6000:])
        self.assertIn("home ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
