"""Multi-scene video: one clip per scene, per-clip credits and reconciliation, with a fake provider.

Mode precedence and retries are checked in-process; the worker scenarios run the
real API and worker over HTTP in a throwaway SQLite database.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from app.workflow.context import NodeInputs, RunOptions
from app.workflow.nodes.media import Operation
from app.workflow.nodes.video import VideoNodeHandler

ROOT = Path(__file__).resolve().parents[1]
SCENES = [{"index": 1, "text": "Rừng đêm.", "visual_prompt": "Rừng đêm, ánh trăng."},
          {"index": 2, "text": "Con cú bay."},
          {"index": 3, "text": "Bình minh.", "visual_prompt": "Bình minh trên đồi."}]


def modes(config=None, options=None, **values):
    handler = VideoNodeHandler()
    context = SimpleNamespace(options=options or RunOptions(), project=SimpleNamespace(topic="Chủ đề", title="Tựa"))
    return handler.operations(context, handler.config_values(config or {}), NodeInputs(values=values))


class ModePrecedenceTest(unittest.TestCase):
    def test_prompt_override_makes_one_clip_even_with_scenes(self):
        self.assertEqual(modes({"prompt": "  Mưa  "}, scenes=SCENES, prompt="Kịch bản"),
                         ("single", [Operation("single", "Mưa")]))

    def test_run_prompt_from_older_clients_is_an_override(self):
        self.assertEqual(modes(options=RunOptions(prompt_override="Gió"), scenes=SCENES),
                         ("single", [Operation("single", "Gió")]))

    def test_scenes_with_an_empty_override_make_one_clip_per_scene(self):
        mode, operations = modes({"prompt": "   "}, scenes=SCENES, prompt="Kịch bản")
        self.assertEqual(mode, "scenes")
        # visual_prompt first, text as fallback; never joined; the scene index is kept.
        self.assertEqual(operations, [Operation("scene:1", "Rừng đêm, ánh trăng.", 1),
                                      Operation("scene:2", "Con cú bay.", 2),
                                      Operation("scene:3", "Bình minh trên đồi.", 3)])

    def test_only_text_makes_one_clip(self):
        self.assertEqual(modes(prompt="Kịch bản ngắn"), ("single", [Operation("single", "Kịch bản ngắn")]))
        self.assertEqual(modes(), ("single", [Operation("single", "Chủ đề")]))
        self.assertEqual(modes(scenes=[{"index": 1, "text": "  "}], prompt="Kịch bản"),
                         ("single", [Operation("single", "Kịch bản")]))

    def test_run_report_compares_image_settings(self):
        from app.smoke_test import compare_settings
        config = {"aspect_ratio": "16:9", "quality": "high", "seed": 7}
        payload = {"kind": "image.generate", "aspect_ratio": "16:9", "quality": "standard", "seed": 7}
        self.assertEqual(compare_settings("image", config, payload), ["quality: setting 'high', request 'standard'"])

    def test_retry_freezes_only_this_nodes_single_clip(self):
        node = {"id": "video"}
        legacy = {"kind": "video.generate", "provider": "fal", "prompt": "Mưa", "credits": 10}
        self.assertIs(VideoNodeHandler._frozen(legacy, node), legacy)
        single = {**legacy, "mode": "single", "node_id": "video"}
        self.assertIs(VideoNodeHandler._frozen(single, node), single)
        self.assertIsNone(VideoNodeHandler._frozen({**single, "node_id": "other"}, node))
        self.assertIsNone(VideoNodeHandler._frozen({**legacy, "mode": "scenes", "operation": "scene:1"}, node))
        self.assertIsNone(VideoNodeHandler._frozen(None, node))


PRELUDE = r'''
import io, json, os
os.environ.update({"FAL_KEY": "fal-SENTINEL-key-0001", "VIDEO_CREDITS_PER_CLIP": "10",
                   "RUNWAYML_API_SECRET": "runway-SENTINEL-key-0002", "RUNWAY_OUTPUT_HOSTS": "dnznrvs05pmza.cloudfront.net",
                   "IMAGE_CREDITS_PER_GENERATION": "2", "OPENAI_API_KEY": "sk-SENTINEL-0003",
                   "TEXT_CREDITS_PER_GENERATION": "1"})
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import image_worker, text_worker, usage, video_worker
from app.logs import configure_logging
from app.models import (Asset, CreditAccount, CreditLedger, CreditReconciliation, UsageEvent, WorkflowJob,
                        WorkflowRunStep)
from app.providers.fal import JobFailure, JobStatus, ProviderError, Submission, VideoResult

VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
logs = io.StringIO()
configure_logging(logs)

class FakeVideos:
    """Behaves per prompt: ok, reject (before acceptance), unknown (lost submit),
    failed (after acceptance) or badfile (not an MP4)."""
    def __init__(self, behavior=None):
        self.behavior, self.requests, self.tasks = behavior or {}, [], {}
    def submit(self, request):
        self.requests.append(request)
        kind = self.behavior.get(request.prompt, "ok")
        if kind == "reject":
            raise ProviderError("invalid_request", "rejected", http_status=422)
        if kind == "unknown":
            raise ProviderError("submission_unknown", "timed out", category="timeout")
        request_id = f"req{len(self.requests)}"
        self.tasks[request_id] = kind
        base = f"https://queue.fal.run/{request.model_id}/requests/{request_id}"
        return Submission(model_id=request.model_id, request_id=request_id, status_url=base + "/status",
                          response_url=base)
    def status(self, submission):
        if self.tasks[submission.request_id] == "failed":
            return JobStatus(state="failed", error=JobFailure("provider_failed", "boom", False))
        return JobStatus(state="completed")
    def result(self, submission):
        return VideoResult(video_url=f"https://fal.media/files/{submission.request_id}.mp4")
    def close(self):
        pass

def downloader(fake):
    def download(url, target):
        kind = fake.tasks[url.rsplit("/", 1)[-1].removesuffix(".mp4")]
        data = b"<html>not a video</html>" if kind == "badfile" else VALID_MP4
        target.write_bytes(data)
        return len(data)
    return download

def drain(fake):
    count = 0
    while video_worker.run_one(client=fake, download=downloader(fake), poll_seconds=0):
        count += 1
        assert count < 80, "worker did not settle"
    return count

client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email": "a@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title": "Rừng", "topic": "Rừng đêm.\n\nCon cú bay.\n\nBình minh."}).json()["id"]
tool = client.post("/api/ai-tools", json={"task": "video", "provider": "fal", "model": "fal-ai/veo3.1/fast"})
assert tool.status_code == 201, tool.text

def save(graph):
    workflow = client.post("/api/workflows", json={"name": "Clips"}).json()["id"]
    saved = client.put(f"/api/workflows/{workflow}", json=graph)
    assert saved.status_code == 200, saved.text
    return workflow

def scene_graph(video_config=None):
    return {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0}, {"id": "scenes", "type": "scenes", "x": 1, "y": 0},
                      {"id": "video", "type": "video", "x": 2, "y": 0, "config": video_config or {}},
                      {"id": "review", "type": "review", "x": 3, "y": 0}],
            "edges": [{"source": "idea", "target": "scenes", "sourceHandle": "topic", "targetHandle": "script"},
                      {"source": "scenes", "target": "video", "sourceHandle": "scenes", "targetHandle": "scenes"},
                      {"source": "video", "target": "review", "sourceHandle": "video_assets", "targetHandle": "media"}]}

workflow = save(scene_graph())

funded = []
def fund(credits):
    funded.append(credits)  # each top-up needs its own ledger reference
    with Session.begin() as db: usage.post_credit(db, workspace, credits, "test", f"fund-{len(funded)}")

def balance():
    with Session() as db:
        return db.get(CreditAccount, workspace).balance

def start(workflow_id=None):
    response = client.post(f"/api/workflows/{workflow_id or workflow}/runs", json={"project_id": project})
    assert response.status_code == 201, response.text
    return response.json()

def step_of(run_id, node_id):
    return next(s for s in client.get(f"/api/workflow-runs/{run_id}").json()["steps"] if s["node_id"] == node_id)

def step_id_of(run_id, node_id):
    with Session() as db:
        return db.scalar(select(WorkflowRunStep.id).where(WorkflowRunStep.run_id == run_id,
                                                          WorkflowRunStep.node_id == node_id))
'''

SCENE_SUCCESS = r'''
check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "video")
assert (check["status"], check["credits"]) == ("insufficient_credits", 10), check
fund(30)
check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "video")
assert (check["status"], check["code"], check["credits"]) == ("ready", "per_scene", 10), check

run = start()
assert [s["status"] for s in run["steps"]] == ["completed", "completed", "queued", "skipped"], run
video = step_of(run["id"], "video")
assert (video["output"]["mode"], video["output"]["expected"]) == ("scenes", 3), video
assert balance() == 0
step_id = step_id_of(run["id"], "video")
with Session() as db:
    keys = sorted(db.scalars(select(WorkflowJob.logical_key).where(WorkflowJob.run_id == run["id"])))
    reserves = sorted(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason == "video_reserve")))
assert keys == [f"video:{step_id}:scene:{n}" for n in (1, 2, 3)], keys
assert reserves == [f"video-reserve:{step_id}:scene:{n}" for n in (1, 2, 3)], reserves

fake = FakeVideos()
drain(fake)
# One request per scene, never one joined prompt.
assert [r.prompt for r in fake.requests] == ["Rừng đêm.", "Con cú bay.", "Bình minh."], fake.requests
assert all(r.aspect_ratio == "9:16" and r.model_id == "fal-ai/veo3.1/fast" for r in fake.requests)
video = step_of(run["id"], "video")
assert video["status"] == "completed", video
clips = video["output"]["video_assets"]
assert [c["scene_index"] for c in clips] == [1, 2, 3], clips
for clip in clips:
    assert {"scene_index", "asset_id", "filename", "content_type", "provider", "model", "duration"} <= set(clip), clip
    assert (clip["content_type"], clip["provider"], clip["model"], clip["duration"]) == \
           ("video/mp4", "fal", "fal-ai/veo3.1/fast", 8.0), clip
    assert clip["id"] == clip["asset_id"] and clip["filename"].endswith(".mp4")
assert all(set(record) <= {"operation", "scene_index", "index", "status", "assets", "detail", "error", "reconciliation"}
           for record in video["output"]["jobs"].values()), video["output"]["jobs"]
with Session() as db:
    used = sorted(db.scalars(select(UsageEvent.reference).where(UsageEvent.tool == "fal/video")))
    assert used == [f"video:{step_id}:scene:{n}" for n in (1, 2, 3)], used
    assert db.scalar(select(func.count()).select_from(Asset).where(Asset.run_id == run["id"])) == 3
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reference.like("reserve:%"))) == 0
assert balance() == 0
# Review receives every clip; approving it finishes the run.
review = step_of(run["id"], "review")
assert review["status"] == "awaiting_review", review
assert sorted(review["output"]["asset_ids"]) == sorted(c["id"] for c in clips)
approved = client.post(f"/api/workflow-runs/{run['id']}/approve")
assert approved.status_code == 200 and approved.json()["status"] == "completed", approved.text
assert client.get(f"/api/assets/{clips[1]['id']}").content == VALID_MP4
assert not video_worker.run_one(client=fake, download=downloader(fake), poll_seconds=0)
written = logs.getvalue()
assert "SENTINEL" not in written and "queue.fal.run" not in written
events = [json.loads(line) for line in written.splitlines()]
completed = [e for e in events if e["event"] == "job_completed"]
assert sorted(e["scene_index"] for e in completed) == [1, 2, 3], completed
assert all(e["workflow_id"] == workflow and e["provider"] == "fal" for e in completed)
assert any(e["event"] == "media_step_settled" and e["status"] == "completed" for e in events)
# The run report lists every scene's request.
from app.smoke_test import run_report
report = []
assert run_report(run["id"], out=report.append) == 0, report
assert [line.split(":")[0].split("(")[1] for line in report if line.startswith("  job ") and "(scene " in line] ==        ["scene 1)", "scene 2)", "scene 3)"], report
'''

PARTIAL = r'''
fund(30)
run = start()
fake = FakeVideos({"Con cú bay.": "reject", "Bình minh.": "unknown"})
drain(fake)
video = step_of(run["id"], "video")
assert video["status"] == "needs_attention", video
assert [c["scene_index"] for c in video["output"]["video_assets"]] == [1], video
statuses = sorted((r["scene_index"], r["status"], r.get("error", {}).get("category")) for r in video["output"]["jobs"].values())
assert statuses == [(1, "succeeded", None), (2, "failed", "invalid_request"), (3, "needs_attention", "timeout")], statuses
assert step_of(run["id"], "review")["status"] == "skipped"
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "needs_attention"
assert balance() == 10  # scene 1 charged, scene 2 refunded, scene 3 held
step_id = step_id_of(run["id"], "video")
with Session() as db:
    refunds = list(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason == "video_refund")))
assert refunds == [f"video-refund:{step_id}:scene:2"], refunds
pending = client.get("/api/admin/reconciliation").json()
assert pending["total"] == 1, pending
item = pending["items"][0]
assert (item["scene_index"], item["operation"], item["node_type"], item["credits"], item["provider"], item["model"]) == \
       (3, "scene:3", "video", 10, "fal", "fal-ai/veo3.1/fast"), item
assert (item["run_id"], item["step_id"]) == (run["id"], step_id) and item["job_id"]
assert client.post(f"/api/admin/reconciliation/{step_id}/refund", json={}).status_code == 409
assert client.post(f"/api/workflow-runs/{run['id']}/retry").status_code == 409
refunded = client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/refund", json={"note": "not in dashboard"})
assert refunded.status_code == 200, refunded.text
assert balance() == 20
video = step_of(run["id"], "video")
assert (video["status"], video["output"]["reconciliation"]["status"]) == ("failed", "refunded"), video
assert [c["scene_index"] for c in video["output"]["video_assets"]] == [1]
# The kept clip is not regenerated automatically; a run with stored clips is not retried.
assert client.post(f"/api/workflow-runs/{run['id']}/retry").status_code == 409
'''

UNCERTAIN = r'''
fund(30)
run = start()
fake = FakeVideos({"Rừng đêm.": "failed", "Con cú bay.": "badfile"})
drain(fake)
video = step_of(run["id"], "video")
assert video["status"] == "needs_attention", video
assert [c["scene_index"] for c in video["output"]["video_assets"]] == [3]
assert balance() == 0
items = sorted(client.get("/api/admin/reconciliation").json()["items"], key=lambda item: item["scene_index"])
assert [(i["scene_index"], i["remote_request_id"], i["submission_succeeded"]) for i in items] == \
       [(1, "req1", True), (2, "req2", True)], items
root = Path("instance")
assert not [p for p in root.rglob("*.part")]
# Each scene is decided on its own; the step waits until none is pending.
assert client.post(f"/api/admin/reconciliation/jobs/{items[0]['job_id']}/confirm-charge", json={}).status_code == 200
video = step_of(run["id"], "video")
assert video["status"] == "needs_attention" and "reconciliation" not in video["output"], video
assert client.post(f"/api/admin/reconciliation/jobs/{items[1]['job_id']}/refund", json={}).status_code == 200
video = step_of(run["id"], "video")
assert (video["status"], video["output"]["reconciliation"]["status"]) == ("needs_attention", "confirmed_charge"), video
assert balance() == 10
with Session() as db:
    assert db.scalar(select(func.count()).select_from(CreditReconciliation)) == 2
    assert sorted(db.scalars(select(UsageEvent.reference))) == sorted(
        f"video:{video_step}:scene:{n}" for video_step in [step_id_of(run["id"], "video")] for n in (1, 3))
'''

REJECTED = r'''
fund(30)
run = start()
fake = FakeVideos({"Rừng đêm.": "reject", "Con cú bay.": "reject", "Bình minh.": "reject"})
drain(fake)
video = step_of(run["id"], "video")
assert (video["status"], video["output"]["error"]["code"]) == ("failed", "invalid_request"), video
assert balance() == 30
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "failed"
assert client.get("/api/admin/reconciliation").json()["total"] == 0
# A retry generates every scene again from the current settings.
retried = client.post(f"/api/workflow-runs/{run['id']}/retry")
assert retried.status_code == 201, retried.text
with Session() as db:
    keys = sorted(db.scalars(select(WorkflowJob.logical_key).where(WorkflowJob.run_id == retried.json()["id"])))
new_step = step_id_of(retried.json()["id"], "video")
assert keys == [f"video:{new_step}:scene:{n}" for n in (1, 2, 3)], keys
assert balance() == 0
'''

SINGLE = r'''
# A prompt override makes one clip even though scenes are connected.
single = save(scene_graph({"prompt": "Mưa trên mái nhà"}))
check = next(s for s in client.get(f"/api/workflows/{single}/readiness").json()["steps"] if s["node_id"] == "video")
assert check["code"] is None and check["credits"] == 10, check
fund(10)
run = start(single)
step_id = step_id_of(run["id"], "video")
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
    assert job.logical_key == f"video:{step_id}:single"
    assert (job.payload["mode"], job.payload["prompt"], job.payload["reserve_reference"]) == \
           ("single", "Mưa trên mái nhà", f"video-reserve:{step_id}:single"), job.payload
fake = FakeVideos()
drain(fake)
assert [r.prompt for r in fake.requests] == ["Mưa trên mái nhà"]
video = step_of(run["id"], "video")
assert video["status"] == "completed", video
clip = video["output"]["video_assets"][0]
assert (clip["scene_index"], clip["provider"], clip["duration"], clip["asset_id"]) == \
       (None, "fal", 8.0, video["output"]["asset_id"]), clip
with Session() as db:
    assert db.scalar(select(UsageEvent.reference)) == f"video:{step_id}:single"
# A definite rejection refunds the single clip under its own reference.
fund(10)
run = start(single)
drain(FakeVideos({"Mưa trên mái nhà": "reject"}))
step_id = step_id_of(run["id"], "video")
with Session() as db:
    assert db.scalar(select(CreditLedger.reference).where(CreditLedger.reason == "video_refund")) == \
           f"video-refund:{step_id}:single"
assert balance() == 10
'''

LEGACY = r'''
# A job queued before multi-scene video: no references in its payload, one reserve:<run> per run.
single = save(scene_graph({"prompt": "Mưa"}))
def legacy_run():
    fund(10)
    run = start(single)
    step_id = step_id_of(run["id"], "video")
    with Session.begin() as db:
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
        payload = {key: value for key, value in job.payload.items() if key not in (
            "reserve_reference", "usage_reference", "refund_reference", "mode", "operation", "scene_index", "node_id")}
        job.payload_json = json.dumps(payload)
        job.logical_key = f"video:{run['id']}:{step_id}"
        ledger = db.scalar(select(CreditLedger).where(CreditLedger.reference == f"video-reserve:{step_id}:single"))
        ledger.reference = f"reserve:{run['id']}"
    return run, step_id

run, step_id = legacy_run()
drain(FakeVideos({"Mưa": "reject"}))
with Session() as db:
    assert db.scalar(select(CreditLedger.reference).where(CreditLedger.reason == "video_refund")) == f"refund:{run['id']}"
run, step_id = legacy_run()
drain(FakeVideos())
assert step_of(run["id"], "video")["status"] == "completed"
with Session() as db:
    assert db.scalar(select(UsageEvent.reference)) == f"video:{step_id}"
run, step_id = legacy_run()
drain(FakeVideos({"Mưa": "unknown"}))
item = client.get("/api/admin/reconciliation").json()["items"][0]
assert (item["step_id"], item["scene_index"], item["credits"]) == (step_id, None, 10), item
# The step route still decides a legacy step with one job.
before = balance()
assert client.post(f"/api/admin/reconciliation/{step_id}/refund", json={}).status_code == 200
assert balance() == before + 10
with Session() as db:
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reference == f"refund:{run['id']}")) == 1
'''

END_TO_END = r'''
# Idea → AI Writer → Scene Splitter (3) → Image (3 images) and Video (3 clips) → Review.
from app.providers.text import TextResult, TextUsage
from app.providers.image import GeneratedImage, ImageResult, ImageStatus, ImageSubmission
assert client.post("/api/ai-tools", json={"task": "script", "provider": "openai", "model": "gpt-4.1-mini"}).status_code == 201
assert client.post("/api/ai-tools", json={"task": "image", "provider": "runway", "model": "gen4_image"}).status_code == 201
graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0}, {"id": "writer", "type": "ai_writer", "x": 1, "y": 0},
                   {"id": "scenes", "type": "scenes", "x": 2, "y": 0}, {"id": "image", "type": "image", "x": 3, "y": 0},
                   {"id": "video", "type": "video", "x": 3, "y": 1}, {"id": "review", "type": "review", "x": 4, "y": 1}],
         "edges": [{"source": "idea", "target": "writer", "sourceHandle": "topic", "targetHandle": "prompt"},
                   {"source": "writer", "target": "scenes", "sourceHandle": "script", "targetHandle": "script"},
                   {"source": "scenes", "target": "image", "sourceHandle": "scenes", "targetHandle": "scenes"},
                   {"source": "scenes", "target": "video", "sourceHandle": "scenes", "targetHandle": "scenes"},
                   {"source": "video", "target": "review", "sourceHandle": "video_assets", "targetHandle": "media"}]}
full = save(graph)
fund(1 + 3 * 2 + 3 * 10)
run = start(full)
assert [s["status"] for s in run["steps"]] == ["completed", "queued", "skipped", "skipped", "skipped", "skipped"], run

class Writer:
    def __init__(self, provider): self.provider = provider
    def generate(self, **kwargs):
        return TextResult(text="Rừng đêm tĩnh lặng.\n\nMột con cú bay qua.\n\nBình minh lên.",
                          usage=TextUsage.of(10, 20), provider=self.provider, model=kwargs["model"])
    def close(self): pass
assert text_worker.run_one(provider_factory=Writer)
progress = client.get(f"/api/workflow-runs/{run['id']}").json()
assert [s["status"] for s in progress["steps"]] == ["completed", "completed", "completed", "queued", "queued", "skipped"], progress
assert len(progress["steps"][2]["output"]["scenes"]) == 3

PNG = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (32).to_bytes(4, "big") + (18).to_bytes(4, "big")
       + b"\x08\x02\x00\x00\x00" + b"\x00" * 4 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
class Images:
    def __init__(self): self.prompts = []
    def submit(self, request):
        self.prompts.append(request.prompt)
        return ImageSubmission("runway", request.model, f"00000000-0000-4000-8000-00000000000{len(self.prompts)}")
    def status(self, submission): return ImageStatus("completed")
    def result(self, submission):
        return ImageResult("runway", submission.model, submission.request_id,
                           (GeneratedImage(f"https://dnznrvs05pmza.cloudfront.net/{submission.request_id}.png"),))
    def validate_media_url(self, url): pass
    def close(self): pass
images = Images()
def image_download(url, target):
    target.write_bytes(PNG)
    return len(PNG)
count = 0
while image_worker.run_one(client=images, download=image_download, poll_seconds=0):
    count += 1
    assert count < 40
videos = FakeVideos()
drain(videos)
assert images.prompts == [r.prompt for r in videos.requests] == \
       ["Rừng đêm tĩnh lặng.", "Một con cú bay qua.", "Bình minh lên."], (images.prompts, videos.requests)
done = client.get(f"/api/workflow-runs/{run['id']}").json()
steps = {s["node_id"]: s for s in done["steps"]}
assert [a["scene_index"] for a in steps["image"]["output"]["image_assets"]] == [1, 2, 3], steps["image"]
assert [c["scene_index"] for c in steps["video"]["output"]["video_assets"]] == [1, 2, 3], steps["video"]
assert steps["review"]["status"] == "awaiting_review" and len(steps["review"]["output"]["asset_ids"]) == 3, steps["review"]
assert done["status"] == "awaiting_review", done
assert client.post(f"/api/workflow-runs/{run['id']}/approve").json()["status"] == "completed"
assert balance() == 0
with Session() as db:
    assert sorted(db.scalars(select(UsageEvent.tool))) == ["fal/video"] * 3 + ["openai/text"] + ["runway/image"] * 3
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
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("RUNWAY", "IMAGE_", "VIDEO_", "FAL_", "OPENAI", "TEXT_"))}
        return subprocess.run([sys.executable, "-c", "from pathlib import Path\n" + PRELUDE + body], cwd=target,
                              env={**env, "PYTHONPATH": str(target)}, capture_output=True, text=True)


class MultiSceneVideoTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])

    def test_one_clip_per_scene_then_review(self):
        self.check(SCENE_SUCCESS)

    def test_partial_failure_keeps_clips_and_reconciles_each_scene(self):
        self.check(PARTIAL)

    def test_failures_after_acceptance_are_decided_per_scene(self):
        self.check(UNCERTAIN)

    def test_definite_rejections_refund_and_retry_regenerates_every_scene(self):
        self.check(REJECTED)

    def test_single_clip_uses_its_own_step_references(self):
        self.check(SINGLE)

    def test_legacy_run_reservations_stay_reconcilable(self):
        self.check(LEGACY)

    def test_writer_scenes_images_and_clips_end_to_end(self):
        self.check(END_TO_END)


if __name__ == "__main__":
    unittest.main()
