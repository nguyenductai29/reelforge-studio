"""Voice jobs end to end over HTTP: Idea → Scene Splitter → Voice with the real worker and a fake provider."""
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
import io, json, os
os.environ.update({"GEMINI_API_KEY": "gemini-SENTINEL-key-0001", "VOICE_CREDITS_PER_GENERATION": "1"})
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import usage, voice_worker
from app.audio_files import pcm_to_wav
from app.logs import configure_logging
from app.models import Asset, CreditAccount, CreditLedger, CreditReconciliation, UsageEvent, WorkflowJob, WorkflowRunStep
from app.providers.voice import VoiceProviderError, VoiceResult

MODEL = "gemini-2.5-flash-preview-tts"
WAV = pcm_to_wav(b"\x00\x00" * 24000, sample_rate=24000)
logs = io.StringIO()
configure_logging(logs)

class FakeVoices:
    """Behaves per text: ok, reject (before acceptance), unknown (lost response), html (not audio) or empty."""
    def __init__(self, behavior=None):
        self.behavior, self.requests = behavior or {}, []
    def generate(self, request):
        self.requests.append(request)
        kind = self.behavior.get(request.text, "ok")
        if kind == "reject":
            raise VoiceProviderError("invalid_request", "rejected", http_status=400)
        if kind == "unknown":
            raise VoiceProviderError("submission_unknown", "timed out", category="timeout")
        if kind == "empty":
            raise VoiceProviderError("empty_output", "no audio")
        audio = b"<html><body>Quota exceeded</body></html>" if kind == "html" else WAV
        return VoiceResult("gemini", request.model, audio, "audio/wav", 1.0, f"resp-{len(self.requests)}")
    def close(self):
        pass

def drain(fake):
    count = 0
    while voice_worker.run_one(client=fake):
        count += 1
        assert count < 40, "worker did not settle"
    return count

client = TestClient(app)
assert client.post("/api/setup", json={"email": "a@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title": "Rừng", "topic": "Rừng đêm.\n\nCon cú bay.\n\nBình minh."}).json()["id"]
tool = client.post("/api/ai-tools", json={"task": "voice", "provider": "gemini", "model": MODEL})
assert tool.status_code == 201, tool.text

def save(voice_config=None):
    workflow = client.post("/api/workflows", json={"name": "Narration"}).json()["id"]
    graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0}, {"id": "scenes", "type": "scenes", "x": 1, "y": 0},
                       {"id": "voice", "type": "voice", "x": 2, "y": 0, "config": voice_config or {}}],
             "edges": [{"source": "idea", "target": "scenes", "sourceHandle": "topic", "targetHandle": "script"},
                       {"source": "scenes", "target": "voice", "sourceHandle": "scenes", "targetHandle": "scenes"}]}
    saved = client.put(f"/api/workflows/{workflow}", json=graph)
    assert saved.status_code == 200, saved.text
    return workflow

workflow = save()
funded = []
def fund(credits):
    funded.append(credits)
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
        return db.scalar(select(WorkflowRunStep.id).where(WorkflowRunStep.run_id == run_id, WorkflowRunStep.node_id == node_id))
'''

SUCCESS = r'''
check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "voice")
assert (check["status"], check["credits"]) == ("insufficient_credits", 1), check
fund(3)
check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "voice")
assert (check["status"], check["code"], check["credits"]) == ("ready", "per_scene", 1), check
run = start()
assert [s["status"] for s in run["steps"]] == ["completed", "completed", "queued"], run
assert balance() == 0
step_id = step_id_of(run["id"], "voice")
with Session() as db:
    keys = sorted(db.scalars(select(WorkflowJob.logical_key).where(WorkflowJob.run_id == run["id"])))
assert keys == [f"voice:{step_id}:scene:{n}" for n in (1, 2, 3)], keys

fake = FakeVoices()
drain(fake)
assert [r.text for r in fake.requests] == ["Rừng đêm.", "Con cú bay.", "Bình minh."], fake.requests
assert all(r.voice == "Kore" and r.format == "wav" for r in fake.requests)
voice = step_of(run["id"], "voice")
assert voice["status"] == "completed", voice
audio = voice["output"]["audio_assets"]
assert [a["scene_index"] for a in audio] == [1, 2, 3], audio
for entry in audio:
    assert {"asset_id", "filename", "content_type", "provider", "model", "scene_index", "duration"} <= set(entry), entry
    assert (entry["content_type"], entry["provider"], entry["model"], entry["duration"]) == ("audio/wav", "gemini", MODEL, 1.0)
    assert entry["id"] == entry["asset_id"] and entry["filename"].endswith(".wav")
assert all(set(record) <= {"operation", "scene_index", "index", "status", "assets", "detail", "error", "reconciliation"}
           for record in voice["output"]["jobs"].values()), voice["output"]["jobs"]
with Session() as db:
    rows = db.scalars(select(Asset).where(Asset.run_id == run["id"])).all()
    assert sorted(a.id for a in rows) == sorted(a["id"] for a in audio)
    assert all((a.project_id, a.step_id, a.provider, a.model, a.content_type, a.bytes) ==
               (project, step_id, "gemini", MODEL, "audio/wav", len(WAV)) for a in rows)
    used = sorted(db.scalars(select(UsageEvent.reference).where(UsageEvent.tool == "gemini/voice")))
    assert used == [f"voice:{step_id}:scene:{n}" for n in (1, 2, 3)], used
assert balance() == 0
assert client.get(f"/api/assets/{audio[0]['id']}").content == WAV
# The run settles once the narration is stored (downstream steps are covered with Subtitle and Render).
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "completed"
assert sorted(voice["output"]["asset_ids"]) == sorted(a["id"] for a in audio)
# A finished job is never generated again, even if it is queued by mistake.
assert not voice_worker.run_one(client=fake)
with Session.begin() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
    job.state = "queued"
assert voice_worker.run_one(client=fake)
assert len(fake.requests) == 3
with Session() as db:
    assert db.scalar(select(func.count()).select_from(UsageEvent)) == 3
    assert db.scalar(select(func.count()).select_from(Asset)) == 3
written = logs.getvalue()
assert "SENTINEL" not in written and "Rừng đêm." not in written
events = [json.loads(line) for line in written.splitlines()]
completed = [e for e in events if e["event"] == "job_completed"]
assert sorted(e["scene_index"] for e in completed) == [1, 2, 3], completed
assert {e["provider_job_id"] for e in events if e["event"] == "provider_request_completed"} == {"resp-1", "resp-2", "resp-3"}
'''

SINGLE = r'''
single = save({"text": "Một lời dẫn duy nhất.", "voice": "Puck", "style": "cheerful"})
fund(1)
run = start(single)
step_id = step_id_of(run["id"], "voice")
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run["id"]))
assert (job.logical_key, job.payload["mode"], job.payload["prompt"]) == \
       (f"voice:{step_id}:single", "prompt", "Một lời dẫn duy nhất."), job.payload
fake = FakeVoices()
drain(fake)
assert [(r.text, r.voice, r.style) for r in fake.requests] == [("Một lời dẫn duy nhất.", "Puck", "cheerful")]
voice = step_of(run["id"], "voice")
assert voice["status"] == "completed", voice
assert [(a["scene_index"], a["duration"]) for a in voice["output"]["audio_assets"]] == [(None, 1.0)]
'''

PARTIAL = r'''
fund(3)
run = start()
drain(FakeVoices({"Con cú bay.": "reject", "Bình minh.": "unknown"}))
voice = step_of(run["id"], "voice")
assert voice["status"] == "needs_attention", voice
assert [a["scene_index"] for a in voice["output"]["audio_assets"]] == [1]
statuses = sorted((r["scene_index"], r["status"], r.get("error", {}).get("category")) for r in voice["output"]["jobs"].values())
assert statuses == [(1, "succeeded", None), (2, "failed", "invalid_request"), (3, "needs_attention", "timeout")], statuses
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "needs_attention"
assert balance() == 1  # scene 1 charged, scene 2 refunded, scene 3 held
step_id = step_id_of(run["id"], "voice")
with Session() as db:
    refunds = list(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason == "voice_refund")))
assert refunds == [f"voice-refund:{step_id}:scene:2"], refunds
pending = client.get("/api/admin/reconciliation").json()
assert pending["total"] == 1, pending
item = pending["items"][0]
assert (item["scene_index"], item["operation"], item["node_type"], item["credits"], item["provider"], item["model"]) == \
       (3, "scene:3", "voice", 1, "gemini", MODEL), item
refunded = client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/refund", json={"note": "no request in console"})
assert refunded.status_code == 200, refunded.text
assert balance() == 2
voice = step_of(run["id"], "voice")
assert (voice["status"], voice["output"]["reconciliation"]["status"]) == ("failed", "refunded"), voice
'''

BAD_AUDIO = r'''
fund(3)
run = start()
drain(FakeVoices({"Rừng đêm.": "html", "Con cú bay.": "empty"}))
voice = step_of(run["id"], "voice")
assert voice["status"] == "needs_attention", voice
assert [a["scene_index"] for a in voice["output"]["audio_assets"]] == [3]
items = sorted(client.get("/api/admin/reconciliation").json()["items"], key=lambda item: item["scene_index"])
# The HTML body came from a provider that answered, so its credit is held; so is an empty answer.
assert [(i["scene_index"], i["remote_request_id"], i["error_category"]) for i in items] == \
       [(1, "resp-1", "invalid_response"), (2, None, "empty_output")], items
assert balance() == 0
root = Path("instance")
assert not [p for p in root.rglob("*.part")]
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Asset)) == 1
'''

CONTINUES = r'''
# Scenes → Voice → Subtitle: the subtitle step runs once the narration is stored, timed by it.
workflow_id = client.post("/api/workflows", json={"name": "Captions"}).json()["id"]
graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0}, {"id": "scenes", "type": "scenes", "x": 1, "y": 0},
                   {"id": "voice", "type": "voice", "x": 2, "y": 0}, {"id": "subtitle", "type": "subtitle", "x": 3, "y": 0}],
         "edges": [{"source": "idea", "target": "scenes", "sourceHandle": "topic", "targetHandle": "script"},
                   {"source": "scenes", "target": "voice", "sourceHandle": "scenes", "targetHandle": "scenes"},
                   {"source": "scenes", "target": "subtitle", "sourceHandle": "scenes", "targetHandle": "scenes"},
                   {"source": "voice", "target": "subtitle", "sourceHandle": "audio_assets", "targetHandle": "audio"}]}
assert client.put(f"/api/workflows/{workflow_id}", json=graph).status_code == 200
fund(3)
run = start(workflow_id)
assert [s["status"] for s in run["steps"]] == ["completed", "completed", "queued", "skipped"], run
drain(FakeVoices())
subtitle = step_of(run["id"], "subtitle")
assert subtitle["status"] == "completed", subtitle
asset = subtitle["output"]["subtitle_asset"]
assert (asset["timing"], asset["cue_count"], [s["end"] for s in asset["segments"]]) == ("audio", 3, [1.0, 2.0, 3.0]), asset
assert client.get(f"/api/assets/{asset['id']}").content.decode("utf-8").splitlines()[:3] == \
    ["1", "00:00:00,000 --> 00:00:01,000", "Rừng đêm."]
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "completed"
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
        env = {key: value for key, value in os.environ.items() if not key.startswith(("GEMINI", "VOICE_"))}
        return subprocess.run([sys.executable, "-c", "from pathlib import Path\n" + PRELUDE + body], cwd=target,
                              env={**env, "PYTHONPATH": str(target)}, capture_output=True, text=True)


class VoiceWorkerTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])

    def test_one_narration_per_scene_then_review(self):
        self.check(SUCCESS)

    def test_text_override_makes_one_narration(self):
        self.check(SINGLE)

    def test_partial_failure_keeps_audio_and_reconciles_each_scene(self):
        self.check(PARTIAL)

    def test_invalid_audio_and_empty_answers_hold_credits(self):
        self.check(BAD_AUDIO)

    def test_subtitles_follow_the_narration(self):
        self.check(CONTINUES)


if __name__ == "__main__":
    unittest.main()
