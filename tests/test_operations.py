"""Phase 13 hardening: default models, worker health, admin job views, stuck-job audit, storage and cleanup."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

from app.media_maintenance import cleanup_orphans, cleanup_temp_folders  # noqa: E402

OPERATIONS = r'''
from app import heartbeat
from app.models import AITool, WorkerHeartbeat

# Default models: a step without its own model uses the workspace default; an explicit choice always wins.
first = add_tool("script", "openai", "gpt-4.1-mini")
second = add_tool("script", "anthropic", "claude-opus-5-5")
voice = add_tool("voice", "gemini", "gemini-2.5-flash-preview-tts")
workflow, _ = save_graph([node("idea", "idea"), node("sum", "summarize", x=300),
                          node("hook", "hook", {"tool_id": first}, x=300)],
                         [edge("idea", "topic", "sum", "text"), edge("idea", "topic", "hook", "topic")])
tools = lambda: {s["node_id"]: (s["tool"] or {}).get("id") for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert tools()["sum"] == first
assert client.get("/api/settings/default-models").json()["default_models"] == \
       {"text": None, "image": None, "video": None, "voice": None, "transcription": None}
saved = client.put("/api/settings/default-models", json={"text": second, "voice": voice})
assert saved.status_code == 200, saved.text
assert client.get("/api/settings/default-models").json()["default_models"]["text"] == second
assert tools() == {"idea": None, "sum": second, "hook": first}
wrong = client.put("/api/settings/default-models", json={"text": voice})
assert wrong.status_code == 422 and wrong.json()["detail"]["field"] == "text", wrong.text
assert client.put("/api/settings/default-models", json={"text": "missing-tool"}).status_code == 422
# A disabled default falls back to the first compatible model.
assert client.put(f"/api/ai-tools/{second}", json={"task": "script", "provider": "anthropic", "model": "claude-opus-5-5",
                                                   "is_enabled": False}).status_code == 200
assert tools()["sum"] == first
assert client.put("/api/settings/default-models", json={"text": second}).status_code == 422  # disabled

# Worker heartbeats: throttled upserts, classified for admins.
assert heartbeat.beat("render_worker", session_factory=Session, force=True)
assert not heartbeat.beat("render_worker", session_factory=Session)
assert heartbeat.beat("scheduler_worker", status="error", detail="pass failed", session_factory=Session, force=True)
with Session.begin() as db:
    db.add(WorkerHeartbeat(worker="voice_worker", host="h", pid=1, status="running",
                           started_at=datetime.now(timezone.utc) - timedelta(hours=1),
                           last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
health = {w["worker"]: w["status"] for w in client.get("/api/admin/workers").json()["workers"]}
assert (health["render_worker"], health["scheduler_worker"], health["voice_worker"], health["source_worker"]) == \
       ("ok", "error", "stale", "missing"), health
assert set(heartbeat.WORKERS) <= set(health)

# Admin job views: safe fields only, counts per queue, and a stuck-work audit.
fund(10)
run = start(workflow)
with Session.begin() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]).limit(1))
    job.state, job.worker_id, job.lease_token = "leased", "text-999", "lease-1"
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    stuck_id = job.id
    other = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"], WorkflowJob.id != stuck_id).limit(1))
    other.state, orphan_step = "failed", other.step_id
view = client.get("/api/admin/jobs")
assert view.status_code == 200, view.text
body = view.json()
assert {"id", "queue", "state", "attempt_count", "last_error"} <= set(body["jobs"][0])
assert "payload" not in view.text and "prompt" not in view.text and "system_prompt" not in view.text
assert [j["id"] for j in body["stuck"]["expired_leases"]] == [stuck_id], body["stuck"]
assert [s["step_id"] for s in body["stuck"]["orphan_steps"]] == [orphan_step], body["stuck"]
assert body["counts"]["text"]["leased"] == 1, body["counts"]
assert client.get("/api/admin/jobs?queue=render").json()["jobs"] == []
# The next worker reclaims the expired lease and the recovery is logged for the audit trail.
records = []
class Capture(logging.Handler):
    def emit(self, record): records.append((record.getMessage(), getattr(record, "fields", {})))
logging.getLogger("app.jobs").addHandler(Capture())
with Session.begin() as db:
    reclaimed = jobs.claim_due_jobs(db, worker_id="text-1000", logical_key_prefix="text:")
assert [j.id for j in reclaimed] == [stuck_id]
assert any(message == "job_lease_reclaimed" and fields["previous_worker"] == "text-999" and fields["job_id"] == stuck_id
           for message, fields in records), records

# Only system admins see operations.
assert client.post("/api/admin/accounts", json={"email": "member@example.com", "password": "long-password-123",
                                                "workspace_name": "Member", "plan_code": "trial"}).status_code == 201
member = TestClient(app, headers={"Origin": "http://testserver"})
assert member.post("/api/login", json={"email": "member@example.com", "password": "long-password-123"}).status_code == 200
for path in ("/api/admin/workers", "/api/admin/jobs", "/api/admin/storage"):
    assert member.get(path).status_code == 403, path

# Storage: per workspace and by kind.
upload("a.mp3", MP3, "audio/mpeg")
upload("n.txt", "ghi chú".encode(), "text/plain")
storage = client.get("/api/storage").json()
assert storage["by_type"]["audio"] == len(MP3) and storage["by_type"]["document"] == len("ghi chú".encode()), storage
assert storage["used_bytes"] == sum(storage["by_type"].values()) and storage["quota_bytes"] > 0
admin_view = client.get("/api/admin/storage").json()
assert next(w for w in admin_view["workspaces"] if w["workspace_id"] == workspace)["bytes"] == storage["used_bytes"]
assert client.get("/api/dashboard").json()["storage"]["used_bytes"] == storage["used_bytes"]
print("operations ok")
'''

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"


class OperationsAPITest(unittest.TestCase):
    def test_default_models_health_jobs_audit_and_storage(self):
        completed = run_program(OPERATIONS)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])


class CleanupTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "media"
        self.root.mkdir()
        self.old = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()

    def age(self, path):
        os.utime(path, (self.old, self.old))

    def test_worker_temp_folders_dry_run_then_apply(self):
        render_tmp = self.root / ".render-tmp" / UUID_A
        render_tmp.mkdir(parents=True)
        (render_tmp / "render.mp4").write_bytes(b"x")
        self.age(render_tmp / "render.mp4")
        self.age(render_tmp)
        publish_tmp = self.root / ".publish-tmp"
        publish_tmp.mkdir()
        (publish_tmp / f"{UUID_B}.mp4").write_bytes(b"x")
        self.age(publish_tmp / f"{UUID_B}.mp4")
        fresh = self.root / ".source-tmp" / UUID_C
        fresh.mkdir(parents=True)
        (fresh / "part-000.mp3").write_bytes(b"x")  # a job still working: too new
        (self.root / ".render-tmp" / "notes.txt").write_text("not ours")
        self.age(self.root / ".render-tmp" / "notes.txt")
        preview = cleanup_temp_folders(self.root)
        self.assertEqual(sorted(path.name for path in preview.candidates), sorted([UUID_A, f"{UUID_B}.mp4"]))
        self.assertTrue(render_tmp.exists())
        applied = cleanup_temp_folders(self.root, apply=True)
        self.assertEqual(len(applied.deleted), 2)
        self.assertFalse(render_tmp.exists())
        self.assertTrue(fresh.exists() and (self.root / ".render-tmp" / "notes.txt").exists())
        with self.assertRaises(ValueError):
            cleanup_temp_folders(self.root, minimum_age_hours=1)

    def test_a_temp_folder_containing_a_link_is_kept_whole(self):
        outside = Path(self.root.parent) / "outside.txt"
        outside.write_text("keep me")
        folder = self.root / ".render-tmp" / UUID_A
        folder.mkdir(parents=True)
        try:
            (folder / "link").symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable on this host: {exc}")
        # Age the link itself too: the cleanup reads the newest lstat() time inside the folder, and a fresh link
        # would make the folder too recent to be a candidate at all (what Linux CI showed).
        os.utime(folder / "link", (self.old, self.old), follow_symlinks=False)
        self.age(folder)
        report = cleanup_temp_folders(self.root, apply=True)
        self.assertEqual((report.deleted, len(report.skipped)), ((), 1))
        self.assertTrue(outside.exists())

    def test_orphans_are_files_without_an_asset_row(self):
        workspace = self.root / UUID_A
        workspace.mkdir()
        for name in (UUID_B, UUID_C):
            (workspace / name).write_bytes(b"x")
            self.age(workspace / name)
        (workspace / "readme").write_text("not a media file")
        known = {UUID_B}
        exists = lambda workspace_id, asset_id: asset_id in known
        preview = cleanup_orphans(self.root, [UUID_A], exists)
        self.assertEqual([path.name for path in preview.candidates], [UUID_C])
        # A row that appears between the scan and the delete keeps the file.
        answers = iter([False, True])
        applied = cleanup_orphans(self.root, [UUID_A], lambda w, a: a in known or (a == UUID_C and next(answers)),
                                  apply=True)
        self.assertEqual(([path.name for path in applied.candidates], applied.deleted), ([UUID_C], ()))
        applied = cleanup_orphans(self.root, [UUID_A], exists, apply=True)
        self.assertEqual([path.name for path in applied.deleted], [UUID_C])
        self.assertTrue((workspace / UUID_B).exists() and (workspace / "readme").exists())


if __name__ == "__main__":
    unittest.main()
