"""Image jobs end to end over HTTP: Idea → Scene Splitter → Image → Review with the real worker and a fake provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
import io, json, os, uuid
os.environ.update({"RUNWAYML_API_SECRET": "runway-SENTINEL-key-0001", "RUNWAY_OUTPUT_HOSTS": "dnznrvs05pmza.cloudfront.net",
                   "IMAGE_CREDITS_PER_GENERATION": "2"})
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import image_worker, usage, jobs
from app.logs import configure_logging
from app.models import (Asset, CreditAccount, CreditLedger, CreditReconciliation, UsageEvent, WorkflowJob, WorkflowRun,
                        WorkflowRunStep)
from app.providers.image import GeneratedImage, ImageProviderError, ImageResult, ImageStatus, ImageSubmission
from app.providers.image.runway import validate_media_url

HOST = "dnznrvs05pmza.cloudfront.net"
IHDR = b"IHDR" + (64).to_bytes(4, "big") + (36).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + IHDR + b"\x00" * 4 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
logs = io.StringIO()
configure_logging(logs)

class FakeImages:
    """Behaves per prompt: ok, reject (before acceptance), unknown (lost submit), failed (after acceptance),
    svg (wrong file type) or huge (over the size limit)."""
    def __init__(self, behavior):
        self.behavior, self.requests, self.tasks = behavior, [], {}
    def submit(self, request):
        self.requests.append(request)
        kind = self.behavior.get(request.prompt, "ok")
        if kind == "reject":
            raise ImageProviderError("invalid_request", "rejected", http_status=400)
        if kind == "unknown":
            raise ImageProviderError("submission_unknown", "timed out", category="timeout")
        task = str(uuid.uuid4())
        self.tasks[task] = kind
        return ImageSubmission("runway", request.model, task)
    def status(self, submission):
        if self.tasks[submission.request_id] == "failed":
            from app.providers.image import ImageFailure
            return ImageStatus("failed", ImageFailure("SAFETY.OUTPUT", "flagged"))
        return ImageStatus("completed")
    def result(self, submission):
        return ImageResult("runway", submission.model, submission.request_id,
                           (GeneratedImage(f"https://{HOST}/{submission.request_id}.png?_jwt=signed"),))
    def validate_media_url(self, url):
        validate_media_url(url)
    def close(self):
        pass

def downloader(fake):
    def download(url, target):
        kind = fake.tasks[url.split("/")[-1].split(".")[0]]
        data = {"svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>", "huge": PNG + b"\x00" * (21 * 1024 * 1024)}.get(kind, PNG)
        target.write_bytes(data)
        return len(data)
    return download

def drain(fake):
    download, count = downloader(fake), 0
    while image_worker.run_one(client=fake, download=download, poll_seconds=0):
        count += 1
        assert count < 60, "worker did not settle"
    return count

client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email": "a@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title": "Rừng", "topic": "Rừng đêm.\n\nCon cú bay.\n\nBình minh."}).json()["id"]
tool = client.post("/api/ai-tools", json={"task": "image", "provider": "runway", "model": "gen4_image"})
assert tool.status_code == 201, tool.text
workflow = client.post("/api/workflows", json={"name": "Images"}).json()["id"]
graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0}, {"id": "scenes", "type": "scenes", "x": 1, "y": 0},
                   {"id": "image", "type": "image", "x": 2, "y": 0, "config": {"aspect_ratio": "16:9"}},
                   {"id": "review", "type": "review", "x": 3, "y": 0}],
         "edges": [{"source": "idea", "target": "scenes", "sourceHandle": "topic", "targetHandle": "script"},
                   {"source": "scenes", "target": "image", "sourceHandle": "scenes", "targetHandle": "scenes"},
                   {"source": "image", "target": "review", "sourceHandle": "image_assets", "targetHandle": "media"}]}
saved = client.put(f"/api/workflows/{workflow}", json=graph)
assert saved.status_code == 200, saved.text

def balance():
    with Session() as db:
        return db.get(CreditAccount, workspace).balance

def start():
    response = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
    assert response.status_code == 201, response.text
    return response.json()

def step_of(run_id, node_id):
    return next(s for s in client.get(f"/api/workflow-runs/{run_id}").json()["steps"] if s["node_id"] == node_id)
'''

SUCCESS = r'''
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
image_check = next(s for s in readiness["steps"] if s["node_id"] == "image")
assert (image_check["status"], image_check["code"]) == ("insufficient_credits", None), image_check
with Session.begin() as db: usage.post_credit(db, workspace, 20, "test", "fund")
image_check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "image")
assert (image_check["status"], image_check["code"], image_check["credits"]) == ("ready", "per_scene", 2), image_check

run = start()
assert [s["status"] for s in run["steps"]] == ["completed", "completed", "queued", "skipped"], run
assert balance() == 14
with Session() as db:
    keys = sorted(db.scalars(select(WorkflowJob.logical_key).where(WorkflowJob.run_id == run["id"])))
    step_id = db.scalar(select(WorkflowRunStep.id).where(WorkflowRunStep.run_id == run["id"], WorkflowRunStep.node_id == "image"))
assert keys == [f"image:{step_id}:scene:{n}" for n in (1, 2, 3)], keys

fake = FakeImages({})
drain(fake)
assert [r.prompt for r in fake.requests] == ["Rừng đêm.", "Con cú bay.", "Bình minh."]
assert all(r.aspect_ratio == "16:9" for r in fake.requests)
image = step_of(run["id"], "image")
assert image["status"] == "completed", image
assets = image["output"]["image_assets"]
assert [a["scene_index"] for a in assets] == [1, 2, 3], assets
assert all(a["content_type"] == "image/png" and a["provider"] == "runway" and a["model"] == "gen4_image"
           and a["width"] == 64 and a["height"] == 36 and a["id"] == a["asset_id"] for a in assets), assets
assert all(set(record) <= {"operation", "scene_index", "index", "status", "assets", "detail", "error", "reconciliation"}
           for record in image["output"]["jobs"].values()), image["output"]["jobs"]
with Session() as db:
    rows = db.scalars(select(Asset).where(Asset.run_id == run["id"])).all()
    assert sorted(a.id for a in rows) == sorted(a["id"] for a in assets)
    assert all((a.project_id, a.step_id, a.provider, a.model, a.content_type) ==
               (project, step_id, "runway", "gen4_image", "image/png") for a in rows)
    usage_refs = sorted(db.scalars(select(UsageEvent.reference).where(UsageEvent.tool == "runway/image")))
    assert usage_refs == [f"image:{step_id}:scene:{n}" for n in (1, 2, 3)], usage_refs
assert balance() == 14
# The review sees every image and can be approved; its output passes them on.
review = step_of(run["id"], "review")
assert review["status"] == "awaiting_review", review
assert sorted(review["output"]["asset_ids"]) == sorted(a["id"] for a in assets)
assert client.post(f"/api/workflow-runs/{run['id']}/approve").status_code == 200
assert client.get(f"/api/assets/{assets[0]['id']}").content == PNG
# Nothing is left to do, and a completed job cannot be processed again.
assert not image_worker.run_one(client=fake, download=downloader(fake), poll_seconds=0)
with Session.begin() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
    job.state = "queued"
assert image_worker.run_one(client=fake, download=downloader(fake), poll_seconds=0)
with Session() as db:
    assert db.get(WorkflowJob, job.id).state == "failed"
    assert db.scalar(select(func.count()).select_from(UsageEvent)) == 3
    assert db.scalar(select(func.count()).select_from(Asset)) == 3
assert step_of(run["id"], "image")["status"] == "completed"
written = logs.getvalue()
assert "runway-SENTINEL-key-0001" not in written and "_jwt=signed" not in written
events = [json.loads(line) for line in written.splitlines()]
completed = [e for e in events if e["event"] == "job_completed"]
assert len(completed) == 3 and {e["scene_index"] for e in completed} == {1, 2, 3}
assert all(e["workflow_id"] == workflow and e["provider"] == "runway" for e in completed)
'''

PARTIAL = r'''
with Session.begin() as db: usage.post_credit(db, workspace, 6, "test", "fund")
run = start()
assert balance() == 0
fake = FakeImages({"Con cú bay.": "reject", "Bình minh.": "unknown"})
drain(fake)
image = step_of(run["id"], "image")
assert image["status"] == "needs_attention", image
assert [a["scene_index"] for a in image["output"]["image_assets"]] == [1]
statuses = sorted((r["scene_index"], r["status"], r["error"]["category"] if "error" in r else None)
                  for r in image["output"]["jobs"].values())
assert statuses == [(1, "succeeded", None), (2, "failed", "invalid_request"), (3, "needs_attention", "timeout")], statuses
assert step_of(run["id"], "review")["status"] == "skipped"
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "needs_attention"
assert balance() == 2  # scene 2 refunded; scene 3 held; scene 1 charged
with Session() as db:
    step_id = db.scalar(select(WorkflowRunStep.id).where(WorkflowRunStep.run_id == run["id"], WorkflowRunStep.node_id == "image"))
    refunds = list(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason == "image_refund")))
    assert refunds == [f"image-refund:{step_id}:scene:2"], refunds
# Only the uncertain scene waits for a decision, with its scene index.
pending = client.get("/api/admin/reconciliation").json()
assert pending["total"] == 1, pending
item = pending["items"][0]
assert (item["scene_index"], item["operation"], item["node_type"], item["credits"], item["error_category"]) == \
       (3, "scene:3", "image", 2, "timeout"), item
assert client.post(f"/api/admin/reconciliation/{step_id}/refund", json={}).status_code == 409
refunded = client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/refund", json={"note": "no task in dashboard"})
assert refunded.status_code == 200, refunded.text
assert refunded.json()["reconciliation_status"] == "refunded"
assert balance() == 4
image = step_of(run["id"], "image")
assert image["status"] == "failed", image
assert image["output"]["reconciliation"]["status"] == "refunded"
assert [a["scene_index"] for a in image["output"]["image_assets"]] == [1]
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Asset).where(Asset.run_id == run["id"])) == 1
    assert db.scalar(select(func.count()).select_from(CreditReconciliation)) == 1
assert client.get("/api/admin/reconciliation").json()["total"] == 0
assert client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/refund", json={}).status_code == 200
assert client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/confirm-charge", json={}).status_code == 409
assert balance() == 4
'''

BAD_FILES = r'''
with Session.begin() as db: usage.post_credit(db, workspace, 6, "test", "fund")
run = start()
fake = FakeImages({"Rừng đêm.": "svg", "Con cú bay.": "huge", "Bình minh.": "failed"})
drain(fake)
image = step_of(run["id"], "image")
assert image["status"] == "needs_attention", image
assert image["output"]["image_assets"] == []
assert sorted(r["status"] for r in image["output"]["jobs"].values()) == ["needs_attention"] * 3
assert client.get("/api/admin/reconciliation").json()["total"] == 3
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Asset)) == 0
root = Path("instance")
leftovers = [p for p in root.rglob("*") if p.is_file() and p.suffix in ("", ".part") and p.parent.name == workspace]
assert leftovers == [], leftovers
# Confirming one uncertain scene keeps the step waiting for the others.
items = client.get("/api/admin/reconciliation").json()["items"]
assert client.post(f"/api/admin/reconciliation/jobs/{items[0]['job_id']}/confirm-charge", json={}).status_code == 200
assert step_of(run["id"], "image")["status"] == "needs_attention"
assert "reconciliation" not in step_of(run["id"], "image")["output"]
for item in items[1:]:
    assert client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/refund", json={}).status_code == 200
image = step_of(run["id"], "image")
assert (image["status"], image["output"]["reconciliation"]["status"]) == ("needs_attention", "confirmed_charge"), image
assert client.post(f"/api/workflow-runs/{run['id']}/retry").status_code == 409
'''

REJECTED = r'''
with Session.begin() as db: usage.post_credit(db, workspace, 6, "test", "fund")
run = start()
fake = FakeImages({"Rừng đêm.": "reject", "Con cú bay.": "reject", "Bình minh.": "reject"})
drain(fake)
image = step_of(run["id"], "image")
assert (image["status"], image["output"]["error"]["code"]) == ("failed", "invalid_request"), image
assert balance() == 6
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "failed"
assert client.get("/api/admin/reconciliation").json()["total"] == 0
retried = client.post(f"/api/workflow-runs/{run['id']}/retry")
assert retried.status_code == 201, retried.text
with Session() as db:
    keys = list(db.scalars(select(WorkflowJob.logical_key).where(WorkflowJob.run_id == retried.json()["id"])))
assert len(keys) == 3 and all(key.startswith("image:") for key in keys)
assert balance() == 0
'''


def run_program(body):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("app", "migrations"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        (target / "instance" / "bootstrap.json").write_text(
            json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
        env = {key: value for key, value in os.environ.items() if not key.startswith(("RUNWAY", "IMAGE_"))}
        completed = subprocess.run([sys.executable, "-c", "from pathlib import Path\n" + PRELUDE + body], cwd=target,
                                   env={**env, "PYTHONPATH": str(target)}, capture_output=True, text=True)
        return completed


class ImageWorkerTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])

    def test_one_image_per_scene_then_review(self):
        self.check(SUCCESS)

    def test_partial_failure_keeps_images_and_reconciles_each_scene(self):
        self.check(PARTIAL)

    def test_invalid_files_and_failed_tasks_hold_credits(self):
        self.check(BAD_FILES)

    def test_definite_rejections_refund_and_allow_retry(self):
        self.check(REJECTED)


if __name__ == "__main__":
    unittest.main()
