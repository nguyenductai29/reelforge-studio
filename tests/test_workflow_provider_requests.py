"""Node settings reach the provider: Idea → AI Writer → Scene Splitter → Video → Review through the real workers.

Only the provider clients are fakes. The test checks the arguments the providers
receive, the run report, and that worker logs carry identifiers but no keys or prompts.
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


class WorkflowProviderRequestTest(unittest.TestCase):
    def test_settings_in_the_snapshot_are_what_the_providers_receive(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            program = r'''
import io, json, os
TEXT_KEY = "sk-text-SENTINEL-abcdef0123456789"
VIDEO_KEY = "rw-video-SENTINEL-abcdef0123456789"
os.environ.update({"OPENAI_API_KEY": TEXT_KEY, "RUNWARE_API_KEY": VIDEO_KEY, "VIDEO_CREDITS_PER_CLIP": "10",
                   "TEXT_CREDITS_PER_GENERATION": "1"})
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import text_worker, usage, video_worker
from app.logs import configure_logging
from app.providers.runware import JobStatus, Submission, VideoResult
from app.providers.text import TextResult, TextUsage
from app.smoke_test import compare_settings, run_report

logs = io.StringIO()
configure_logging(logs)
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"Hồ", "topic":"Bình minh trên hồ núi, chuyện riêng của khách"}).json()["id"]
text_tool = client.post("/api/ai-tools", json={"task":"script","provider":"openai","model":"gpt-4.1"}).json()["id"]
client.post("/api/ai-tools", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast"})
video_tool = client.post("/api/ai-tools", json={"task":"video","provider":"runware","model":"bytedance:seedance@2.5"}).json()["id"]
with Session.begin() as db: usage.post_credit(db, workspace, 20, "test", "fund")
workflow = client.post("/api/workflows", json={"name":"Live path"}).json()["id"]
writer = {"tool_id": text_tool, "language": "vi", "tone": "cinematic", "platform": "youtube", "duration": 60,
          "instructions": "Câu ngắn"}
video = {"tool_id": video_tool, "aspect_ratio": "16:9", "duration": "6s", "prompt": "Mưa nhẹ trên mặt hồ lúc bình minh"}
graph = {"nodes": [{"id":"idea","type":"idea","x":0,"y":0}, {"id":"writer","type":"ai_writer","x":1,"y":0,"config":writer},
                   {"id":"scenes","type":"scenes","x":2,"y":0,"config":{"scene_duration":10,"visual_style":"cinematic"}},
                   {"id":"video","type":"video","x":3,"y":0,"config":video}, {"id":"review","type":"review","x":4,"y":0}],
         "edges": [{"source":"idea","target":"writer","sourceHandle":"topic","targetHandle":"prompt"},
                   {"source":"writer","target":"scenes","sourceHandle":"script","targetHandle":"script"},
                   {"source":"scenes","target":"video","sourceHandle":"scenes","targetHandle":"scenes"},
                   {"source":"video","target":"review","sourceHandle":"video_assets","targetHandle":"media"}]}
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
assert readiness["runnable"] and {s["status"] for s in readiness["steps"]} <= {"ready", "configured"}, readiness
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project}).json()
assert [s["status"] for s in run["steps"]] == ["completed", "queued", "skipped", "skipped", "skipped"], run

class TextProvider:
    calls = []
    def __init__(self, name): self.name = name
    def generate(self, **kwargs):
        TextProvider.calls.append(kwargs)
        return TextResult(text="Mặt trời lên.\n\nSương tan trên hồ.", usage=TextUsage.of(50, 20),
                          provider=self.name, model=kwargs["model"])
    def close(self): pass
assert text_worker.run_one(provider_factory=TextProvider)
call = TextProvider.calls[0]
assert call["model"] == "gpt-4.1", call
for phrase in ("in Vietnamese", "Tone: cinematic.", "Platform: YouTube (long-form).", "about 60 seconds", "Câu ngắn"):
    assert phrase in call["prompt"], (phrase, call["prompt"])

class VideoClient:
    requests = []
    def submit(self, request):
        VideoClient.requests.append(request)
        return Submission(model_id=request.model_id, request_id="86d241d1-6d39-43c3-b0bc-aaaf94b8ba40")
    def status(self, submission): return JobStatus(state="completed")
    def result(self, submission): return VideoResult(video_url="https://vm.runware.ai/video/test.mp4")
    def close(self): pass
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
def download(url, target):
    target.write_bytes(VALID_MP4)
    return target.stat().st_size
assert video_worker.run_one(client=VideoClient(), download=download, poll_seconds=0)
assert video_worker.run_one(client=VideoClient(), download=download, poll_seconds=0)
request = VideoClient.requests[0]
assert (request.model_id, request.aspect_ratio, request.duration, request.prompt) == \
       ("bytedance:seedance@2.5", "16:9", "6s", "Mưa nhẹ trên mặt hồ lúc bình minh"), request
done = client.get(f"/api/workflow-runs/{run['id']}").json()
assert [s["status"] for s in done["steps"]] == ["completed", "completed", "completed", "completed", "awaiting_review"], done
assert done["steps"][2]["output"]["scenes"][0]["visual_prompt"].endswith("Cinematic style.")
assert client.post(f"/api/workflow-runs/{run['id']}/approve").status_code == 200

report = []
assert run_report(run["id"], out=report.append) == 0, report
assert report.count("  settings -> request: consistent") == 2, report
# A request that lost a setting is reported.
problems = compare_settings("video", video, {"kind": "video.generate", "tool_id": video_tool, "aspect_ratio": "9:16",
                                              "duration": "6s", "prompt": "other"})
assert problems == ["aspect_ratio: setting '16:9', request '9:16'",
                    "prompt override: setting 'Mưa nhẹ trên mặt hồ lúc bình minh', request 'other'"], problems

written = logs.getvalue()
events = [json.loads(line)["event"] for line in written.splitlines()]
for expected in ("workflow_run_started", "credit_reserved", "job_claimed", "provider_request_started",
                 "provider_request_completed", "job_completed", "workflow_step_awaiting_review",
                 "workflow_run_status_changed"):
    assert expected in events, (expected, events)
submit = next(json.loads(line) for line in written.splitlines()
              if '"provider_request_started"' in line and '"submit"' in line)
assert (submit["provider"], submit["model"], submit["request"]["aspect_ratio"], submit["request"]["duration"]) == \
       ("runware", "bytedance:seedance@2.5", "16:9", "6s"), submit
assert submit["request"]["prompt_chars"] == len(video["prompt"])
for private in (TEXT_KEY, VIDEO_KEY, video["prompt"], "chuyện riêng của khách", "Câu ngắn"):
    assert private not in written, private
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
