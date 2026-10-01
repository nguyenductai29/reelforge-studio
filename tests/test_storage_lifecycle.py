"""Phase 17 storage lifecycle: quotas per plan, warning levels, retention, safe cleanup and user deletion. Offline."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import storage
from app.media_maintenance import check_asset_file, remove_asset_file

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

WORKSPACE = "11111111-1111-4111-8111-111111111111"
ASSET = "22222222-2222-4222-8222-222222222222"


class LevelsAndQuotaTest(unittest.TestCase):
    def test_warning_levels_at_70_80_90_and_100_percent(self):
        cases = [(0, "ok"), (699, "ok"), (700, "notice"), (799, "notice"), (800, "warning"), (899, "warning"),
                 (900, "critical"), (999, "critical"), (1000, "full"), (1500, "full")]
        for used, expected in cases:
            with self.subTest(used=used):
                self.assertEqual(storage.level(used, 1000), expected)
        self.assertEqual(storage.percent(187, 1000), 18.7)
        self.assertEqual(storage.level(1, 0), "full")

    def test_plan_limit_is_capped_by_the_server_setting(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKSPACE_MEDIA_QUOTA_BYTES", None)
            self.assertEqual(storage.effective_quota(10 * storage.GIB), 10 * storage.GIB)
            self.assertEqual(storage.effective_quota(None), storage.GIB)
        with patch.dict(os.environ, {"WORKSPACE_MEDIA_QUOTA_BYTES": str(5 * storage.GIB)}):
            self.assertEqual(storage.effective_quota(10 * storage.GIB), 5 * storage.GIB)
            self.assertEqual(storage.effective_quota(2 * storage.GIB), 2 * storage.GIB)
            self.assertEqual(storage.effective_quota(None), 5 * storage.GIB)
        with patch.dict(os.environ, {"WORKSPACE_MEDIA_QUOTA_BYTES": "lots"}):
            with self.assertRaises(RuntimeError):
                storage.effective_quota(None)

    def test_retention_policy_defaults_and_validation(self):
        names = ("REELFORGE_RETENTION_PARTIAL_DAYS", "REELFORGE_RETENTION_TEMP_DAYS",
                 "REELFORGE_RETENTION_ORPHAN_DAYS", "REELFORGE_RETENTION_INTERMEDIATE_DAYS")
        with patch.dict(os.environ, {name: "" for name in names}):
            self.assertEqual(storage.RetentionPolicy.from_environment(), storage.RetentionPolicy(1, 3, 3, 30))
        with patch.dict(os.environ, {"REELFORGE_RETENTION_INTERMEDIATE_DAYS": "0"}):
            self.assertEqual(storage.RetentionPolicy.from_environment().intermediate_days, 0)
        for name, value in (("REELFORGE_RETENTION_TEMP_DAYS", "0"), ("REELFORGE_RETENTION_INTERMEDIATE_DAYS", "-1"),
                            ("REELFORGE_RETENTION_PARTIAL_DAYS", "soon")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                with self.assertRaises(RuntimeError):
                    storage.RetentionPolicy.from_environment()

    def test_kinds_come_from_the_step_that_made_the_asset(self):
        self.assertEqual(storage.kind_for_node("render"), storage.FINAL_RENDER)
        self.assertEqual(storage.kind_for_node("extract_clips"), storage.EXTRACTED_CLIP)
        self.assertEqual(storage.kind_for_node("video"), storage.SCENE_VIDEO)
        self.assertEqual(storage.kind_for_node("idea"), storage.OTHER)
        self.assertFalse({storage.FINAL_RENDER, storage.SOURCE, storage.SUBTITLE, storage.OTHER}
                         & storage.INTERMEDIATE_KINDS)


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "media"
        (self.root / WORKSPACE).mkdir(parents=True)

    def test_root_comes_from_the_environment_then_the_setting(self):
        with patch.dict(os.environ, {"REELFORGE_STORAGE_ROOT": str(self.root)}):
            self.assertEqual(storage.resolve_root("instance/other"), self.root)
        with patch.dict(os.environ, {"REELFORGE_STORAGE_ROOT": ""}):
            self.assertEqual(storage.resolve_root("instance/other"), storage.ROOT / "instance" / "other")
            self.assertEqual(storage.resolve_root(None), storage.ROOT / "instance" / "media")

    def test_identifiers_that_could_name_another_path_are_refused(self):
        for value in ("..", "../etc", "a/b", "a\\b", ".hidden", "", "x" * 65, None, "c:"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    storage.safe_id(value)
                self.assertEqual(check_asset_file(self.root, WORKSPACE, value), "unsafe")
                self.assertEqual(check_asset_file(self.root, value, ASSET), "unsafe")
        self.assertEqual(storage.safe_id(ASSET), ASSET)

    def test_only_a_regular_file_inside_the_root_is_removed(self):
        target = self.root / WORKSPACE / ASSET
        self.assertEqual(remove_asset_file(self.root, WORKSPACE, ASSET), "missing")
        target.write_bytes(b"x")
        self.assertEqual(check_asset_file(self.root, WORKSPACE, ASSET), "present")
        self.assertEqual(remove_asset_file(self.root, WORKSPACE, ASSET), "deleted")
        self.assertFalse(target.exists())
        target.mkdir()
        self.assertEqual(remove_asset_file(self.root, WORKSPACE, ASSET), "unsafe")
        self.assertTrue(target.is_dir())

    def test_links_are_never_followed(self):
        outside = Path(self.temp.name) / "outside.bin"
        outside.write_bytes(b"keep")
        link = self.root / WORKSPACE / ASSET
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable on this host: {exc}")
        self.assertEqual(remove_asset_file(self.root, WORKSPACE, ASSET), "unsafe")
        self.assertTrue(outside.exists())
        # A workspace folder that is a link to somewhere else.
        elsewhere = Path(self.temp.name) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ASSET).write_bytes(b"keep")
        linked_workspace = self.root / "33333333-3333-4333-8333-333333333333"
        linked_workspace.symlink_to(elsewhere, target_is_directory=True)
        self.assertEqual(remove_asset_file(self.root, linked_workspace.name, ASSET), "unsafe")
        self.assertTrue((elsewhere / ASSET).exists())


LIFECYCLE = r'''
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from app import media_maintenance, storage
from app.models import Plan, Workflow

NOW = datetime.now(timezone.utc)
OLD, RECENT = NOW - timedelta(days=40), NOW - timedelta(days=5)
TYPES = {"image": "image/png", "video": "video/mp4", "voice": "audio/wav", "subtitle": "application/x-subrip",
         "render": "video/mp4", "extract_clips": "video/mp4"}
root = None

def make_run(steps, project_id=None, ids=None):
    """A completed run with one step per (node_type, [(label, created_at)]); every asset is 100 bytes on disk."""
    global root
    ids = ids or {}
    run_id, workflow_id = str(uuid.uuid4()), str(uuid.uuid4())
    made = {}
    with Session.begin() as db:
        db.add(Workflow(id=workflow_id, workspace_id=workspace, name="Run", definition='{"nodes": [], "edges": []}'))
        db.flush()
        db.add(WorkflowRun(id=run_id, workspace_id=workspace, workflow_id=workflow_id, project_id=project_id or project,
                           graph_snapshot='{"nodes": [], "edges": []}', status="completed", created_at=OLD,
                           finished_at=OLD))
        db.flush()
        for position, (node_type, items) in enumerate(steps):
            step_id = str(uuid.uuid4())
            db.add(WorkflowRunStep(id=step_id, run_id=run_id, node_id=f"n{position}", node_type=node_type,
                                   position=position, status="completed", detail="", output="{}"))
            db.flush()
            for label, created in items:
                asset_id = ids.get(label) or str(uuid.uuid4())
                db.add(Asset(id=asset_id, workspace_id=workspace, project_id=project_id or project, run_id=run_id,
                             step_id=step_id, provider="test", model="test", filename=f"{label}.bin",
                             content_type=TYPES[node_type], bytes=100, kind=storage.kind_for_node(node_type),
                             created_at=created))
                made[label] = asset_id
        root = media_root(db) / workspace
    root.mkdir(parents=True, exist_ok=True)
    for asset_id in made.values():
        (root / asset_id).write_bytes(b"m" * 100)
    return run_id, made

def row(asset_id):
    with Session() as db:
        asset = db.get(Asset, asset_id)
        return {"bytes": asset.bytes, "kind": asset.kind, "expired_at": asset.expired_at, "reason": asset.expired_reason,
                "expired_bytes": asset.expired_bytes}

def used():
    return client.get("/api/storage").json()["used_bytes"]

def publication_for(asset_id, run_id, channel, state):
    with Session.begin() as db:
        db.add(Publication(id=str(uuid.uuid4()), workspace_id=workspace, run_id=run_id, asset_id=asset_id,
                           channel=channel, title="T", description="", state=state, created_at=NOW, updated_at=NOW))

# A run that rendered its final video: its intermediates may expire once they are 30 days old.
run_a, a = make_run([("image", [("img_old", OLD)]), ("video", [("clip_old", OLD), ("clip_new", RECENT)]),
                     ("voice", [("voice_old", OLD)]), ("extract_clips", [("cut_old", OLD)]),
                     ("subtitle", [("srt_old", OLD)]), ("render", [("final_a", OLD)])])
# No final render: the clip may be the deliverable, so it is kept.
run_b, b = make_run([("video", [("lonely_old", OLD)])])
# A publication refers to this clip: kept.
run_c, c = make_run([("video", [("published_old", OLD)]), ("render", [("final_c", OLD)])])
publication_for(c["published_old"], run_c, "youtube", "succeeded")
# An ID the path check refuses: listed but never touched.
run_d, d = make_run([("video", [("odd", OLD)]), ("render", [("final_d", OLD)])], ids={"odd": "odd.clip"})
# An uploaded source, long ago.
source = upload("photo.png", b"\x89PNG\r\n\x1a\n" + b"p" * 92, "image/png").json()["id"]
with Session.begin() as db:
    db.get(Asset, source).created_at = NOW - timedelta(days=400)
assert row(source)["kind"] == "source"

expected = {a["img_old"], a["clip_old"], a["voice_old"], a["cut_old"], "odd.clip"}
before = used()

# Dry run: the candidates, nothing changed.
report = media_maintenance.expire_intermediates(apply=False)
assert {item["id"] for item in report.candidates} == expected, report.candidates
assert report.expired == () and all((root / asset_id).exists() for asset_id in expected)
assert used() == before
output = StringIO()
with redirect_stdout(output):
    assert media_maintenance.main(["--intermediates"]) == 0
assert "would expire" in output.getvalue() and "dry-run" in output.getvalue(), output.getvalue()
assert used() == before

# Retention off: nothing is a candidate.
os.environ["REELFORGE_RETENTION_INTERMEDIATE_DAYS"] = "0"
assert media_maintenance.expire_intermediates(apply=True).candidates == ()
del os.environ["REELFORGE_RETENTION_INTERMEDIATE_DAYS"]

# Apply: expired rows keep their lineage with 0 bytes; their files are gone; everything else stays.
report = media_maintenance.expire_intermediates(apply=True)
assert set(report.expired) == expected - {"odd.clip"} and report.skipped == ("odd.clip",), report
assert report.freed_bytes == 400 and used() == before - 400
for asset_id in expected - {"odd.clip"}:
    state = row(asset_id)
    assert (state["bytes"], state["reason"], state["expired_bytes"]) == (0, "retention", 100), state
    assert state["expired_at"] is not None and not (root / asset_id).exists()
kept = [a["final_a"], a["clip_new"], a["srt_old"], b["lonely_old"], c["published_old"], c["final_c"], d["final_d"],
        "odd.clip", source]
assert all((root / asset_id).exists() and row(asset_id)["bytes"] > 0 for asset_id in kept), \
    [(asset_id, row(asset_id)) for asset_id in kept]

# The final video still plays and downloads; an expired file answers 410, and is no longer listed.
assert client.get(f"/api/assets/{a['final_a']}").status_code == 200
gone = client.get(f"/api/assets/{a['clip_old']}")
assert gone.status_code == 410 and gone.json()["detail"]["code"] == "media_expired", gone.text
listed = {item["id"] for item in client.get("/api/dashboard").json()["assets"]}
assert a["final_a"] in listed and a["clip_old"] not in listed

# Running again changes nothing; an expired row whose file survived (an interrupted run) is swept.
again = media_maintenance.expire_intermediates(apply=True)
assert again.expired == () and {item["id"] for item in again.candidates} == {"odd.clip"}
(root / a["clip_old"]).write_bytes(b"left over")
assert media_maintenance.expire_intermediates(apply=True).swept == (a["clip_old"],)
assert not (root / a["clip_old"]).exists()

# A user deletes media they chose: a source, a published clip (its publication finished).
assert client.delete(f"/api/assets/{source}").json()["deleted"] == [source]
assert row(source)["reason"] == "deleted" and not (root / source).exists()
assert client.get(f"/api/assets/{source}").json()["detail"]["reason"] == "deleted"
assert client.delete(f"/api/assets/{c['published_old']}").status_code == 200
# A publication that has not finished still needs its video.
publication_for(c["final_c"], run_c, "tiktok", "queued")
blocked = client.delete(f"/api/assets/{c['final_c']}")
assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "asset_in_use", blocked.text
assert (root / c["final_c"]).exists()
assert client.delete("/api/assets/does-not-exist").status_code == 404
batch = client.post("/api/assets/delete", json={"asset_ids": [a["clip_new"], a["srt_old"], "nope"]}).json()
assert batch["deleted"] == [a["clip_new"], a["srt_old"]] and batch["skipped"] == [{"id": "nope", "reason": "not_found"}]
assert batch["freed_bytes"] == 200

# Another studio cannot delete this one's media.
other = TestClient(app)
assert other.post("/api/register", json={"email": "other@example.com", "password": "long-password-123",
                                         "workspace_name": "Khác"}).status_code == 201
assert other.delete(f"/api/assets/{a['final_a']}").status_code == 404
assert other.post("/api/assets/delete", json={"asset_ids": [d["final_d"]]}).json()["deleted"] == []
assert (root / a["final_a"]).exists() and (root / d["final_d"]).exists()

# Cleanup of one project's intermediates: preview, then apply; its final render stays.
second = client.post("/api/projects", json={"title": "Dự án 2", "topic": "x"}).json()["id"]
run_e, e = make_run([("video", [("e1", RECENT), ("e2", OLD)]), ("voice", [("e3", RECENT)]),
                     ("render", [("final_e", RECENT)])], project_id=second)
preview = client.post("/api/storage/cleanup", json={"project_id": second}).json()
assert preview == {"assets": 3, "bytes": 300, "applied": False}, preview
assert all((root / e[key]).exists() for key in ("e1", "e2", "e3"))
assert client.post("/api/storage/cleanup", json={"project_id": "missing"}).status_code == 404
done = client.post("/api/storage/cleanup", json={"project_id": second, "apply": True}).json()
assert done == {"assets": 3, "bytes": 300, "applied": True}, done
assert not any((root / e[key]).exists() for key in ("e1", "e2", "e3")) and (root / e["final_e"]).exists()
assert row(e["e1"])["reason"] == "cleanup"
# Only the asset whose path is refused remains eligible, and even a studio-wide cleanup leaves it.
assert client.get("/api/storage").json()["intermediate"]["assets"] == 1
assert client.post("/api/storage/cleanup", json={"apply": True}).json()["assets"] == 0
assert (root / "odd.clip").exists()
print("lifecycle ok")
'''


QUOTA = r'''
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from app import storage

def png(size):
    return b"\x89PNG\r\n\x1a\n" + b"p" * (size - 8)

def limit(code, value):
    with Session.begin() as db:
        db.get(Plan, code).storage_limit_bytes = value

# Plans carry their limit: 1, 10 and 30 GiB after migration 0016.
plans = {plan["code"]: plan for plan in client.get("/api/billing").json()["plans"]}
assert plans["trial"]["storage_limit_bytes"] == storage.GIB and plans["pro"]["storage_quota_bytes"] == 30 * storage.GIB
overview = client.get("/api/storage").json()
assert (overview["quota_bytes"], overview["level"], overview["retention"]["intermediate_days"]) == (storage.GIB, "ok", 30)

# Each warning level, then a full studio refuses new media but still serves what it has.
limit("trial", 1000)
first = upload("a.png", png(700), "image/png").json()["id"]
assert client.get("/api/storage").json()["level"] == "notice"
upload("b.png", png(100), "image/png")
assert client.get("/api/storage").json()["level"] == "warning"
upload("c.png", png(100), "image/png")
state = client.get("/api/storage").json()
assert (state["level"], state["percent"]) == ("critical", 90.0), state
assert upload("too-big.png", png(200), "image/png").status_code == 413
assert upload("d.png", png(100), "image/png").status_code == 201
assert client.get("/api/dashboard").json()["storage"]["level"] == "full"
refused = upload("e.png", png(10), "image/png")
assert refused.status_code == 413 and refused.json()["detail"]["code"] == "storage_full", refused.text
assert client.get(f"/api/assets/{first}").status_code == 200
assert client.delete(f"/api/assets/{first}").status_code == 200
assert client.get("/api/storage").json()["level"] == "ok"

# The server setting caps every plan.
os.environ["WORKSPACE_MEDIA_QUOTA_BYTES"] = "400"
assert client.get("/api/storage").json()["quota_bytes"] == 400
assert upload("f.png", png(150), "image/png").status_code == 413
del os.environ["WORKSPACE_MEDIA_QUOTA_BYTES"]

# Two uploads at once cannot both use the last room: the workspace lock serializes them.
live = [item["id"] for item in client.get("/api/dashboard").json()["assets"]]
client.post("/api/assets/delete", json={"asset_ids": live})
assert client.get("/api/storage").json()["used_bytes"] == 0
original = main.media_signature_matches
def slow(content_type, path):
    time.sleep(0.3)
    return original(content_type, path)
with patch.object(main, "media_signature_matches", slow):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: upload(name, png(600), "image/png"), ["x.png", "y.png"]))
assert sorted(result.status_code for result in results) == [201, 413], [(r.status_code, r.text) for r in results]
assert client.get("/api/storage").json()["used_bytes"] == 600

# Admin: limits per plan (left out, unchanged), studios by storage, warning counts and the disk.
plan = client.get("/api/admin").json()["plans"][1]
body = {key: plan[key] for key in ("name", "project_limit", "workflow_limit", "monthly_credits", "is_active", "price_vnd")}
saved = client.put("/api/admin/plans/standard", json={**body, "storage_limit_bytes": 5 * storage.GIB}).json()
assert saved["storage_limit_bytes"] == 5 * storage.GIB
assert client.put("/api/admin/plans/standard", json=body).json()["storage_limit_bytes"] == 5 * storage.GIB
assert client.put("/api/admin/plans/standard", json={**body, "storage_limit_bytes": 1000}).status_code == 422
limit("trial", 650)
admin = client.get("/api/admin").json()
assert admin["counts"]["storage_alerts"] == 1 and admin["storage_levels"]["critical"] == 1, admin["storage_levels"]
studios = client.get("/api/admin/workspaces").json()["items"]
assert studios[0]["storage"]["level"] == "critical", studios[0]
report = client.get("/api/admin/storage", params={"limit": 5}).json()
assert report["workspaces"][0]["workspace_id"] == workspace and report["workspaces"][0]["level"] == "critical"
assert report["levels"]["critical"] == 1 and report["total"] >= 1 and report["retention"]["temp_days"] == 3
assert report["disk"] is None or report["disk"]["total_bytes"] >= report["disk"]["free_bytes"]
# The disk is described by sizes only, never by its path.
assert report["disk"] is None or set(report["disk"]) == {"total_bytes", "used_bytes", "free_bytes", "percent"}
print("quota ok")
'''


class StorageIntegrationTest(unittest.TestCase):
    def test_retention_cleanup_and_user_deletion(self):
        completed = run_program(LIFECYCLE)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])
        self.assertIn("lifecycle ok", completed.stdout)

    def test_plan_quota_levels_concurrency_and_admin(self):
        completed = run_program(QUOTA)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])
        self.assertIn("quota ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
