"""Render end to end over HTTP with the real workers and a fake FFmpeg:
Idea → AI Writer → Scene Splitter → Video (3 clips) + Voice (3 narrations) + Subtitle → Render → Review."""
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
import io, json, os, subprocess
from pathlib import Path
tools = Path("instance/tools")
tools.mkdir(parents=True, exist_ok=True)
for name in ("ffmpeg", "ffprobe"):
    (tools / name).write_text("")
os.environ.update({"FAL_KEY": "fal-SENTINEL-key", "VIDEO_CREDITS_PER_CLIP": "10", "GEMINI_API_KEY": "gemini-SENTINEL-key",
                   "VOICE_CREDITS_PER_GENERATION": "1", "OPENAI_API_KEY": "sk-SENTINEL", "TEXT_CREDITS_PER_GENERATION": "1",
                   "RENDER_FFMPEG_PATH": str((tools / "ffmpeg").resolve()),
                   "RENDER_FFPROBE_PATH": str((tools / "ffprobe").resolve())})
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import render, render_worker, text_worker, usage, video_worker, voice_worker
from app.providers.text import TextResult, TextUsage
from app.audio_files import pcm_to_wav
from app.logs import configure_logging
from app.models import Asset, CreditAccount, CreditLedger, WorkflowJob, WorkflowRunStep
from app.providers.fal import JobStatus, Submission, VideoResult
from app.providers.voice import VoiceResult

# fontconfig differs between machines; the font check has its own unit tests.
render.font_issue = lambda run=None: None
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
WAV = pcm_to_wav(b"\x00\x00" * 48000, sample_rate=24000)
logs = io.StringIO()
configure_logging(logs)

class FakeVideos:
    def __init__(self): self.count = 0
    def submit(self, request):
        self.count += 1
        return Submission(model_id=request.model_id, request_id=f"req{self.count}",
                          status_url=f"https://queue.fal.run/x/requests/req{self.count}/status",
                          response_url=f"https://queue.fal.run/x/requests/req{self.count}")
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url=f"https://fal.media/files/{submission.request_id}.mp4")
    def close(self): pass

class FakeVoices:
    def generate(self, request):
        return VoiceResult("gemini", request.model, WAV, "audio/wav", 2.0, "resp")
    def close(self): pass

class Writer:
    def __init__(self, provider): self.provider = provider
    def generate(self, **kwargs):
        return TextResult(text=chr(10).join(["Rừng đêm.", "", "Con cú bay.", "", "Bình minh."]), usage=TextUsage.of(10, 20),
                          provider=self.provider, model=kwargs["model"])
    def close(self): pass

def download(url, target):
    target.write_bytes(VALID_MP4)
    return len(VALID_MP4)

class FakeFfmpeg:
    """Answers ffprobe with fixed facts and "renders" by writing a small MP4 into the job folder."""
    def __init__(self, returncode=0, stderr="", error=None):
        self.calls, self.returncode, self.stderr, self.error, self.subtitles = [], returncode, stderr, error, None
    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        if Path(args[0]).name == "ffprobe":
            final = Path(args[-1]).name == "render.mp4"
            data = {"streams": [{"codec_type": "video", "width": 720 if final else 1280, "height": 1280 if final else 720},
                                {"codec_type": "audio"}], "format": {"duration": "24.0" if final else "8.0"}}
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        if self.error:
            raise self.error
        folder = Path(kwargs["cwd"])
        self.folder = folder
        if (folder / "subtitles.srt").exists():
            self.subtitles = (folder / "subtitles.srt").read_text(encoding="utf-8")
        if self.returncode == 0:
            (folder / "render.mp4").write_bytes(VALID_MP4)
        return subprocess.CompletedProcess(args, self.returncode, "", self.stderr)

def drain(module, **kwargs):
    count = 0
    while module.run_one(**kwargs):
        count += 1
        assert count < 60, "worker did not settle"

client = TestClient(app)
assert client.post("/api/setup", json={"email": "a@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title": "Rừng", "topic": "Rừng đêm.\n\nCon cú bay.\n\nBình minh."}).json()["id"]
for task, provider, model in (("video", "fal", "fal-ai/veo3.1/fast"), ("voice", "gemini", "gemini-2.5-flash-preview-tts"),
                              ("script", "openai", "gpt-4.1-mini")):
    assert client.post("/api/ai-tools", json={"task": task, "provider": provider, "model": model}).status_code == 201

def edge(source, target, out, into):
    return {"source": source, "target": target, "sourceHandle": out, "targetHandle": into}

def node(node_id, node_type):
    return {"id": node_id, "type": node_type, "x": 0, "y": 0}

FULL = {"nodes": [node("idea", "idea"), node("writer", "ai_writer"), node("scenes", "scenes"), node("video", "video"),
                  node("voice", "voice"), node("subtitle", "subtitle"), node("render", "render"), node("review", "review")],
        "edges": [edge("idea", "writer", "topic", "prompt"), edge("writer", "scenes", "script", "script"),
                  edge("scenes", "video", "scenes", "scenes"),
                  edge("scenes", "voice", "scenes", "scenes"), edge("scenes", "subtitle", "scenes", "scenes"),
                  edge("voice", "subtitle", "audio_assets", "audio"), edge("video", "render", "video_assets", "media"),
                  edge("voice", "render", "audio_assets", "audio"), edge("subtitle", "render", "subtitle_asset", "subtitle"),
                  edge("render", "review", "rendered_video", "media")]}
CLIPS_ONLY = {"nodes": [node("idea", "idea"), node("scenes", "scenes"), node("video", "video"), node("render", "render"),
                        node("review", "review")],
              "edges": [edge("idea", "scenes", "topic", "script"), edge("scenes", "video", "scenes", "scenes"),
                        edge("video", "render", "video_assets", "media"), edge("render", "review", "rendered_video", "media")]}

def save(graph):
    workflow = client.post("/api/workflows", json={"name": "Final"}).json()["id"]
    saved = client.put(f"/api/workflows/{workflow}", json=graph)
    assert saved.status_code == 200, saved.text
    return workflow

funded = []
def fund(credits):
    funded.append(credits)
    with Session.begin() as db: usage.post_credit(db, workspace, credits, "test", f"fund-{len(funded)}")

def balance():
    with Session() as db:
        return db.get(CreditAccount, workspace).balance

def start(workflow):
    response = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
    assert response.status_code == 201, response.text
    return response.json()

def steps(run_id):
    return {s["node_id"]: s for s in client.get(f"/api/workflow-runs/{run_id}").json()["steps"]}

def upstream(run_id, voice=True):
    if voice:
        drain(voice_worker, client=FakeVoices())
    drain(video_worker, client=FakeVideos(), download=download, poll_seconds=0)
    return steps(run_id)
'''

SUCCESS = r'''
workflow = save(FULL)
check = {s["node_id"]: s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert (check["render"]["status"], check["subtitle"]["status"]) == ("configured", "configured"), check
fund(34)
run = start(workflow)
assert [s["status"] for s in run["steps"]] == ["completed", "queued"] + ["skipped"] * 6, run
assert text_worker.run_one(provider_factory=Writer)
assert [steps(run["id"])[key]["status"] for key in ("scenes", "video", "voice")] == ["completed", "queued", "queued"]
state = upstream(run["id"])
assert state["subtitle"]["status"] == "completed" and state["render"]["status"] == "queued", state
assert (state["render"]["output"]["clip_count"], state["render"]["output"]["audio_count"],
        state["render"]["output"]["subtitles"]) == (3, 3, True), state["render"]
clips = state["video"]["output"]["video_assets"]
voices = state["voice"]["output"]["audio_assets"]
fake = FakeFfmpeg()
assert render_worker.run_one(runner=fake)
state = steps(run["id"])
final = state["render"]
assert final["status"] == "completed", final
entry = final["output"]["video_assets"]
assert len(entry) == 1 and entry[0]["final"] is True, entry
entry = entry[0]
assert (entry["provider"], entry["model"], entry["scene_index"], entry["content_type"], entry["duration"],
        entry["width"], entry["height"]) == ("ffmpeg", "local", None, "video/mp4", 24.0, 720, 1280), entry
assert final["output"]["audio_policy"] == "scenes" and final["output"]["subtitles_burned"] is True
# One ffmpeg command, no shell, in a private folder; clips in scene order, then narration in scene order.
ffmpeg_calls = [(args, kwargs) for args, kwargs in fake.calls if Path(args[0]).name == "ffmpeg"]
assert len(ffmpeg_calls) == 1
args, kwargs = ffmpeg_calls[0]
assert "shell" not in kwargs and isinstance(args, list)
inputs = [Path(args[i + 1]).name for i, value in enumerate(args) if value == "-i"]
assert inputs == [c["id"] for c in clips] + [v["id"] for v in voices], inputs
graph = args[args.index("-filter_complex") + 1]
assert "subtitles=filename=subtitles.srt" in graph and "[0:a]" not in graph
assert Path(kwargs["cwd"]).parent.name == ".render-tmp"
assert not fake.folder.exists(), "temporary files are removed"
# Burned subtitles follow the 8-second clips, narrated 2 seconds each.
assert fake.subtitles.splitlines()[:3] == ["1", "00:00:00,000 --> 00:00:02,000", "Rừng đêm."], fake.subtitles
assert fake.subtitles.splitlines()[5] == "00:00:08,000 --> 00:00:10,000", fake.subtitles
with Session() as db:
    row = db.get(Asset, entry["id"])
    assert (row.project_id, row.run_id, row.step_id, row.provider, row.model, row.content_type, row.bytes) == \
           (project, run["id"], row.step_id, "ffmpeg", "local", "video/mp4", len(VALID_MP4))
    assert db.scalar(select(WorkflowRunStep.node_type).where(WorkflowRunStep.id == row.step_id)) == "render"
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reason.like("render%"))) == 0
assert client.get(f"/api/assets/{entry['id']}").content == VALID_MP4
# Review previews the final render only, and approval makes it the video to publish.
review = state["review"]
assert review["status"] == "awaiting_review" and review["output"]["asset_ids"] == [entry["id"]], review
assert client.post(f"/api/workflow-runs/{run['id']}/approve").json()["status"] == "completed"
from app.publications import _approved_review
with Session.begin() as db:
    assert _approved_review(db, workspace_id=workspace, run_id=run["id"], asset_id=entry["id"]).node_type == "review"
    assert _approved_review(db, workspace_id=workspace, run_id=run["id"], asset_id=clips[0]["id"])
# A finished render is never run again.
with Session.begin() as db:
    db.scalar(select(WorkflowJob).where(WorkflowJob.logical_key.like("render:%"))).state = "queued"
assert render_worker.run_one(runner=fake)
assert len([call for call in fake.calls if Path(call[0][0]).name == "ffmpeg"]) == 1
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Asset).where(Asset.provider == "ffmpeg")) == 1
events = [json.loads(line) for line in logs.getvalue().splitlines()]
done = next(e for e in events if e["event"] == "render_completed")
assert (done["clip_count"], done["audio_count"], done["subtitle"], done["output_bytes"]) == (3, 3, True, len(VALID_MP4))
assert {"workspace_id", "run_id", "step_id", "job_id", "duration_ms"} <= set(done)
assert "filter_complex" not in logs.getvalue() and "SENTINEL" not in logs.getvalue()
'''

FAILURE = r'''
os.environ["RENDER_CREDITS_PER_JOB"] = "2"
workflow = save(CLIPS_ONLY)
fund(32)
run = start(workflow)
state = upstream(run["id"], voice=False)
assert state["render"]["status"] == "queued" and balance() == 0, state["render"]
fake = FakeFfmpeg(returncode=1, stderr="frame=0\n/srv/reelforge/media/secret/render.mp4: Invalid argument\n")
assert render_worker.run_one(runner=fake)
state = steps(run["id"])
render_step = state["render"]
assert render_step["status"] == "failed", render_step
assert (render_step["output"]["error"]["code"], render_step["output"]["error"]["category"]) == ("render_failed", "generation_failed")
assert "Invalid argument" in render_step["output"]["render_error"] and "/srv" not in render_step["output"]["render_error"]
assert state["review"]["status"] == "skipped"
assert client.get(f"/api/workflow-runs/{run['id']}").json()["status"] == "failed"
assert balance() == 2  # the render's price was refunded automatically
with Session() as db:
    step_id = db.scalar(select(WorkflowRunStep.id).where(WorkflowRunStep.run_id == run["id"], WorkflowRunStep.node_id == "render"))
    refs = sorted(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason.like("render%"))))
    assert refs == [f"render-refund:{step_id}:final", f"render-reserve:{step_id}:final"], refs
    assert db.scalar(select(func.count()).select_from(Asset).where(Asset.provider == "ffmpeg")) == 0
assert not fake.folder.exists()
assert client.get("/api/admin/reconciliation").json()["total"] == 0
failed = next(json.loads(line) for line in logs.getvalue().splitlines() if json.loads(line)["event"] == "render_failed")
assert (failed["error_code"], failed["error_category"], failed["clip_count"]) == ("render_failed", "generation_failed", 3)
'''

GUARDS = r'''
workflow = save(CLIPS_ONLY)
# Without FFmpeg, readiness says so and the step blocks before anything is queued.
os.environ["RENDER_FFMPEG_PATH"] = str(tools / "missing-ffmpeg")
check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "render")
assert (check["status"], check["code"]) == ("ffmpeg_missing", "ffmpeg_missing"), check
fund(30)
run = start(workflow)
state = upstream(run["id"], voice=False)
assert state["render"]["status"] == "blocked" and "ffmpeg" in state["render"]["detail"], state["render"]
with Session() as db:
    assert db.scalar(select(func.count()).select_from(WorkflowJob).where(WorkflowJob.logical_key.like("render:%"))) == 0
os.environ["RENDER_FFMPEG_PATH"] = str((tools / "ffmpeg").resolve())
# A clip file that disappeared after queueing fails the render clearly.
fund(30)
run = start(workflow)
state = upstream(run["id"], voice=False)
assert state["render"]["status"] == "queued"
clip_id = state["video"]["output"]["video_assets"][1]["id"]
media = next(Path("instance").rglob(clip_id))
media.unlink()
fake = FakeFfmpeg()
assert render_worker.run_one(runner=fake)
state = steps(run["id"])
assert (state["render"]["status"], state["render"]["output"]["error"]["code"]) == ("failed", "input_missing"), state["render"]
assert not [call for call in fake.calls if Path(call[0][0]).name == "ffmpeg"]
# A render that runs too long is stopped and reported as a timeout.
fund(30)
run = start(workflow)
state = upstream(run["id"], voice=False)
fake = FakeFfmpeg(error=subprocess.TimeoutExpired(["ffmpeg"], 1800))
assert render_worker.run_one(runner=fake)
state = steps(run["id"])
assert (state["render"]["status"], state["render"]["output"]["error"]["code"]) == ("failed", "render_timeout"), state["render"]
assert not list(Path("instance").rglob(".render-tmp/*"))
'''


def run_program(body):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("app", "migrations"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        (target / "instance" / "bootstrap.json").write_text(
            json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("FAL_", "VIDEO_", "GEMINI", "VOICE_", "RENDER_"))}
        return subprocess.run([sys.executable, "-c", PRELUDE + body], cwd=target,
                              env={**env, "PYTHONPATH": str(target)}, capture_output=True, text=True)


class RenderWorkerTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stderr[-5000:])

    def test_clips_narration_and_subtitles_become_one_final_video(self):
        self.check(SUCCESS)

    def test_ffmpeg_failure_refunds_and_reports_without_paths(self):
        self.check(FAILURE)

    def test_missing_tools_missing_inputs_and_timeouts(self):
        self.check(GUARDS)


if __name__ == "__main__":
    unittest.main()
