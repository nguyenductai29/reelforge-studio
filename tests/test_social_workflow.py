"""Phase 9: starter templates, run summary, publishing metadata and the YouTube hand-off, with fake providers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import httpx

from app.publications import MetadataError, fit_metadata, normalize_tags, validate_metadata
from app.publishers import youtube
from app.workflow import default_registry
from app.workflow.nodes.text import parse_metadata
from app.workflow.ports import edge_problems
from app.workflow.templates import TEMPLATES, template_graph

ROOT = Path(__file__).resolve().parents[1]


class TemplateTest(unittest.TestCase):
    def test_templates_are_valid_graphs_without_tool_ids(self):
        social = ["idea", "ai_writer", "scenes", "video", "voice", "subtitle", "render", "review", "metadata", "publish"]
        recap = ["source_media", "transcribe", "story_analysis", "recap_script", "voice", "match_scenes",
                 "extract_clips", "subtitle", "render", "review", "metadata", "publish"]
        slides = ["ai_writer", "scenes", "image", "voice", "subtitle", "render", "review", "metadata", "publish"]
        expected = {"repurpose": ["source_url", *social[1:]], "movie_recap": recap, "movie_review": recap,
                    "article_to_video": ["source_url", *slides], "product_video": ["idea", *slides]}
        for template_id in TEMPLATES:
            with self.subTest(template=template_id):
                graph = template_graph(template_id)
                self.assertEqual([node["type"] for node in graph["nodes"]], expected.get(template_id, social))
                self.assertEqual(edge_problems(graph, default_registry), [])
                for node in graph["nodes"]:
                    default_registry.resolve(node["type"]).validate_config(node.get("config"))
                    self.assertNotIn("tool_id", node.get("config") or {})

    def test_defaults_per_template(self):
        def config(template_id, node_id):
            return next(node for node in template_graph(template_id)["nodes"] if node["id"] == node_id).get("config")

        self.assertEqual(config("youtube_short", "video"), {"aspect_ratio": "9:16", "duration": "6s"})
        self.assertEqual(config("youtube_short", "writer"), {"platform": "youtube_shorts", "duration": 50})
        self.assertEqual(config("youtube_short", "scenes"), {"scene_duration": 5, "max_scenes": 12})
        self.assertEqual(config("youtube_landscape", "video"), {"aspect_ratio": "16:9", "duration": "8s"})
        self.assertEqual(config("youtube_landscape", "writer"), {"platform": "youtube", "duration": 120})
        self.assertEqual(config("youtube_landscape", "scenes")["scene_duration"], 7)


class MetadataTest(unittest.TestCase):
    def test_validation_follows_youtube_limits(self):
        self.assertEqual(validate_metadata("  Rừng đêm  ", "Mô tả", ["#rừng", "Rừng", " đêm  khuya "], "unlisted"),
                         ("Rừng đêm", "Mô tả", ["rừng", "đêm khuya"], "unlisted"))
        cases = [(("", ""), "invalid_title"), (("x" * 101, ""), "invalid_title"), (("a\nb", ""), "invalid_title"),
                 (("<b>", ""), "invalid_title"), (("ok", "ệ" * 1667), "invalid_description"),
                 (("ok", "a > b"), "invalid_description"), (("ok", "", ["a,b"]), "invalid_tags"),
                 (("ok", "", ["x" * 101]), "invalid_tags"), (("ok", "", [f"tag {n}" for n in range(60)]), "invalid_tags"),
                 (("ok", "", [], "friends"), "invalid_privacy")]
        for args, code in cases:
            with self.subTest(code=code, args=str(args)[:40]), self.assertRaises(MetadataError) as caught:
                validate_metadata(*args)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(len(validate_metadata("ok", "ệ" * 1666)[1].encode()), 4998)
        with self.assertRaises(MetadataError):
            normalize_tags("not a list")

    def test_generated_metadata_is_fitted_not_rejected(self):
        fitted = fit_metadata("<b>" + "Tiêu đề rất dài " * 10, "ệ" * 3000 + "<script>", [f"tag {n}" for n in range(80)])
        validate_metadata(fitted["title"], fitted["description"], fitted["tags"])
        self.assertLessEqual(len(fitted["title"]), 100)
        self.assertLessEqual(youtube.tags_length(fitted["tags"]), 500)
        self.assertEqual(parse_metadata('```json\n{"title": "Đêm", "description": "D", "tags": ["#a", "b, c"]}\n```'),
                         {"title": "Đêm", "description": "D", "tags": ["a", "b c"]})
        self.assertEqual(parse_metadata("Chỉ tiêu đề\nPhần mô tả"),
                         {"title": "Chỉ tiêu đề", "description": "Phần mô tả", "tags": []})

    def test_upload_visibility_and_tags(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        video = Path(folder.name) / "final.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40)
        request = youtube.YouTubeUploadRequest(video, "Tiêu đề", "Mô tả", privacy_status="unlisted", tags=("a", "b c"))

        def answer(privacy):
            bodies = []

            def handler(req):
                if req.method == "POST":
                    bodies.append(json.loads(req.content))
                    return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos"
                                                                    "?uploadType=resumable&upload_id=s1"})
                return httpx.Response(200, json={"id": "abcdefghijk", "status": {"privacyStatus": privacy,
                                                                                 "uploadStatus": "uploaded"}})
            return bodies, httpx.Client(transport=httpx.MockTransport(handler))

        bodies, client = answer("private")
        result = youtube.upload_video(request, "token", client=client)
        self.assertEqual(bodies[0]["status"]["privacyStatus"], "unlisted")
        self.assertEqual(bodies[0]["snippet"]["tags"], ["a", "b c"])
        # YouTube may keep a video more private than asked (unverified Google projects): accepted and reported.
        self.assertEqual((result.privacy_status, result.upload_status), ("private", "uploaded"))
        _, client = answer("public")
        with self.assertRaises(youtube.YouTubeUploadError) as caught:
            youtube.upload_video(request, "token", client=client)
        self.assertEqual(caught.exception.code, "unexpected_visibility")
        with self.assertRaises(youtube.YouTubeUploadError):
            youtube.upload_video(youtube.YouTubeUploadRequest(video, "t", privacy_status="friends"), "token",
                                 client=answer("private")[1])


PRELUDE = r'''
import io, json, os, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography.fernet import Fernet
tools = Path("instance/tools")
tools.mkdir(parents=True, exist_ok=True)
for name in ("ffmpeg", "ffprobe"):
    (tools / name).write_text("")
os.environ.update({"FAL_KEY": "fal-SENTINEL", "VIDEO_CREDITS_PER_CLIP": "10", "GEMINI_API_KEY": "gemini-SENTINEL",
                   "VOICE_CREDITS_PER_GENERATION": "1", "OPENAI_API_KEY": "sk-SENTINEL", "TEXT_CREDITS_PER_GENERATION": "1",
                   "RENDER_FFMPEG_PATH": str((tools / "ffmpeg").resolve()), "RENDER_FFPROBE_PATH": str((tools / "ffprobe").resolve()),
                   "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
                   "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
                   "REELFORGE_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode()})
import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import render, render_worker, text_worker, usage, video_worker, voice_worker, youtube_worker
from app.audio_files import pcm_to_wav
from app.models import Asset, CreditLedger, UsageEvent, WorkflowJob, WorkflowRunStep
from app.providers.fal import JobStatus, Submission, VideoResult
from app.providers.text import TextResult, TextUsage
from app.providers.voice import VoiceResult
from app.publications import Publication
from app.publishers.google_oauth import YouTubeConnection
from app.publishers.youtube import UPLOAD_SCOPE

render.font_issue = lambda run=None: None
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
WAV = pcm_to_wav(b"\x00\x00" * 48000, sample_rate=24000)
PROMPTS = []

class Writer:
    """Writes the script, and the metadata when JSON is asked for."""
    def __init__(self, provider): self.provider = provider
    def generate(self, **kwargs):
        PROMPTS.append(kwargs)
        if kwargs.get("response_format") == "json":
            text = json.dumps({"title": "Một đêm trong rừng", "description": "Khám phá khu rừng về đêm.",
                               "tags": ["rừng", "#đêm", "thiên nhiên"]}, ensure_ascii=False)
        else:
            text = chr(10).join(["Rừng đêm.", "", "Con cú bay.", "", "Bình minh."])
        return TextResult(text=text, usage=TextUsage.of(10, 20), provider=self.provider, model=kwargs["model"])
    def close(self): pass

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
    def generate(self, request): return VoiceResult("gemini", request.model, WAV, "audio/wav", 2.0, "resp")
    def close(self): pass

def download(url, target):
    target.write_bytes(VALID_MP4)
    return len(VALID_MP4)

def ffmpeg(args, **kwargs):
    if Path(args[0]).name == "ffprobe":
        data = {"streams": [{"codec_type": "video", "width": 720, "height": 1280}, {"codec_type": "audio"}],
                "format": {"duration": "18.0" if Path(args[-1]).name == "render.mp4" else "6.0"}}
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
    (Path(kwargs["cwd"]) / "render.mp4").write_bytes(VALID_MP4)
    return subprocess.CompletedProcess(args, 0, "", "")

def drain(module, **kwargs):
    count = 0
    while module.run_one(**kwargs):
        count += 1
        assert count < 60

client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email": "a@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
from sqlalchemy import update
from app.models import Plan
with Session.begin() as db:
    db.execute(update(Plan).values(workflow_limit=None))  # the trial plan allows two workflows
project = client.post("/api/projects", json={"title": "Rừng đêm", "topic": "Một đêm yên tĩnh trong rừng già"}).json()["id"]

def add_tool(task, provider, model):
    response = client.post("/api/ai-tools", json={"task": task, "provider": provider, "model": model})
    assert response.status_code == 201, response.text
    return response.json()["id"]

funded = []
def fund(credits):
    funded.append(credits)
    with Session.begin() as db: usage.post_credit(db, workspace, credits, "test", f"fund-{len(funded)}")

def summary(run_id):
    response = client.get(f"/api/workflow-runs/{run_id}/summary")
    assert response.status_code == 200, response.text
    return response.json()

def connect_youtube():
    now = datetime.now(timezone.utc)
    cipher = Fernet(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"].encode())
    with Session.begin() as db:
        db.add(YouTubeConnection(workspace_id=workspace, access_token_ciphertext=cipher.encrypt(b"oauth-access").decode(),
                                 refresh_token_ciphertext=cipher.encrypt(b"oauth-refresh").decode(),
                                 expires_at=now + timedelta(hours=1), scope=UPLOAD_SCOPE, connected_at=now, updated_at=now))

def google(privacy="private", start_status=200):
    seen = []
    def handler(request):
        seen.append(request)
        if request.method == "POST":
            if start_status != 200:
                return httpx.Response(start_status, json={"error": "bad"})
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=s1"})
        if request.headers["Content-Range"].startswith("bytes */"):
            return httpx.Response(308)
        return httpx.Response(200, json={"id": "abcdefghijk", "status": {"privacyStatus": privacy, "uploadStatus": "uploaded"}})
    return seen, httpx.Client(transport=httpx.MockTransport(handler))

def counts():
    with Session() as db:
        return {"jobs": db.scalar(select(func.count()).select_from(WorkflowJob).where(~WorkflowJob.logical_key.like("publish:%"))),
                "ledger": db.scalar(select(func.count()).select_from(CreditLedger)),
                "usage": db.scalar(select(func.count()).select_from(UsageEvent)),
                "assets": db.scalar(select(func.count()).select_from(Asset)),
                "steps": sorted((s.id, s.status, s.output) for s in db.scalars(select(WorkflowRunStep)))}
'''

TEMPLATES_SCENARIO = r'''
listed = client.get("/api/workflow-templates").json()["templates"]
assert [t["id"] for t in listed] == ["youtube_short", "youtube_landscape", "tiktok_short", "facebook_reel",
                                    "repurpose", "movie_recap", "movie_review", "article_to_video",
                                    "product_video"], listed
assert next(t for t in listed if t["id"] == "movie_recap")["notice"] == "Use only content you are authorized to use."
created = client.post("/api/workflows", json={"name": "Short", "template": "youtube_short"})
assert created.status_code == 201, created.text
graph = created.json()["graph"]
types = {node["id"]: node["type"] for node in graph["nodes"]}
assert types["render"] == "render" and types["publish"] == "publish" and types["metadata"] == "metadata"
config = {node["id"]: node.get("config") for node in graph["nodes"]}
assert config["video"] == {"aspect_ratio": "9:16", "duration": "6s"} and config["writer"]["platform"] == "youtube_shorts"
assert all("tool_id" not in (value or {}) for value in config.values())
assert all(edge["sourceHandle"] and edge["targetHandle"] for edge in graph["edges"])
landscape = client.post("/api/workflows", json={"name": "Long", "template": "youtube_landscape"}).json()
assert next(n for n in landscape["graph"]["nodes"] if n["id"] == "video")["config"]["aspect_ratio"] == "16:9"
bad = client.post("/api/workflows", json={"name": "X", "template": "tiktok_dance"})
assert bad.status_code == 422 and bad.json()["detail"]["code"] == "unknown_template", bad.text
# Legacy: a workflow without a template is still idea → video → review.
plain = client.post("/api/workflows", json={"name": "Plain"}).json()
assert [n["type"] for n in plain["graph"]["nodes"]] == ["idea", "video", "review"]

# Models resolve at run time: the first enabled tool per task, unless a step names one.
first = add_tool("video", "fal", "fal-ai/veo3.1/fast")
second = add_tool("video", "runware", "bytedance:seedance@2.5")
text_tool = add_tool("script", "openai", "gpt-4.1-mini")
workflow = created.json()["id"]
steps = {s["node_id"]: s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert (steps["video"]["tool"]["id"], steps["video"]["tool"]["chosen"]) == (first, False), steps["video"]
assert steps["writer"]["tool"]["id"] == text_tool and steps["metadata"]["tool"]["id"] == text_tool
assert steps["voice"]["tool"] is None and steps["idea"]["tool"] is None
graph["nodes"] = [{**n, "config": {**(n.get("config") or {}), "tool_id": second}} if n["id"] == "video" else n
                  for n in graph["nodes"]]
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
steps = {s["node_id"]: s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert (steps["video"]["tool"]["id"], steps["video"]["tool"]["chosen"]) == (second, True), steps["video"]
'''

FULL_RUN = r'''
add_tool("script", "openai", "gpt-4.1-mini")
add_tool("video", "fal", "fal-ai/veo3.1/fast")
add_tool("voice", "gemini", "gemini-2.5-flash-preview-tts")
workflow = client.post("/api/workflows", json={"name": "Short", "template": "youtube_short"}).json()["id"]
fund(35)
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project}).json()
early = summary(run["id"])
assert [c["node_id"] for c in early["current"]] == ["writer"] and early["active_jobs"] == 1, early
assert early["credits"] == {"reserved": 1, "refunded": 0, "consumed": 0, "held": 1}, early["credits"]
assert early["final_video"] is None and not early["publishing"]["ready"]
# The project topic flows to the AI Writer and the Metadata step.
assert text_worker.run_one(provider_factory=Writer)
assert text_worker.run_one(provider_factory=Writer)
assert "Một đêm yên tĩnh trong rừng già" in PROMPTS[0]["prompt"] and PROMPTS[0]["response_format"] == "text"
assert PROMPTS[1]["response_format"] == "json" and "Một đêm yên tĩnh trong rừng già" in PROMPTS[1]["prompt"]
drain(voice_worker, client=FakeVoices())
drain(video_worker, client=FakeVideos(), download=download, poll_seconds=0)
assert render_worker.run_one(runner=ffmpeg)
state = client.get(f"/api/workflow-runs/{run['id']}").json()
steps = {s["node_id"]: s for s in state["steps"]}
assert state["status"] == "awaiting_review" and steps["publish"]["status"] == "skipped", state["status"]
final = steps["render"]["output"]["video_assets"][0]
# Nothing is published until a person approves; then Publish hands the final video over.
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Publication)) == 0
approved = client.post(f"/api/workflow-runs/{run['id']}/approve").json()
steps = {s["node_id"]: s for s in approved["steps"]}
assert approved["status"] == "completed", approved["status"]
handoff = steps["publish"]["output"]
assert (handoff["video"]["asset_id"], handoff["video"]["final"]) == (final["id"], True), handoff
assert handoff["metadata"] == {"title": "Một đêm trong rừng", "description": "Khám phá khu rừng về đêm.",
                               "tags": ["rừng", "đêm", "thiên nhiên"], "privacy_status": "private"}, handoff
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Publication)) == 0
done = summary(run["id"])
assert done["counts"] == {"script_words": 7, "scenes": 3, "images": 0, "clips": 3, "narrations": 3,
                          "subtitle_cues": 3}, done["counts"]
assert done["credits"] == {"reserved": 35, "refunded": 0, "consumed": 35, "held": 0}, done["credits"]
assert (done["final_video"]["asset_id"], done["final_video"]["final"], done["final_video"]["duration"]) == \
       (final["id"], True, 18.0), done["final_video"]
assert done["review"] == {"present": True, "status": "completed", "approved": True}
assert done["publishing"]["ready"] and done["publishing"]["defaults"]["source"] == "publish"
assert done["publishing"]["publication"] is None and done["publishing"]["youtube_connected"] is False
assert done["failed"] == [] and done["needs_attention"] == [] and done["current"] == [] and done["active_jobs"] == 0
assert done["steps"]["completed"] == done["steps"]["total"] == 10
assert client.get(f"/api/assets/{final['id']}").content == VALID_MP4
assert "SENTINEL" not in json.dumps(done)

# Publishing: the final render by default, validated metadata, one publication per run.
connect_youtube()
body = {"run_id": run["id"], "title": "Một đêm trong rừng", "description": "Khám phá khu rừng về đêm.",
        "tags": ["rừng", "đêm"], "privacy_status": "unlisted"}
for bad, code in (({"privacy_status": "friends"}, None), ({"title": "<b>Rừng</b>"}, "invalid_title"),
                  ({"tags": [f"thẻ số {n}" for n in range(60)]}, "invalid_tags"),
                  ({"description": "ệ" * 1700}, "invalid_description")):
    response = client.post("/api/youtube/publications", json={**body, **bad})
    assert response.status_code == 422, response.text
    assert code is None or response.json()["detail"]["code"] == code, response.text
clips = steps["video"]["output"]["video_assets"]
clip_attempt = client.post("/api/youtube/publications", json={**body, "asset_id": clips[0]["id"]})
assert clip_attempt.status_code == 409 and "final render" in clip_attempt.text
created = client.post("/api/youtube/publications", json=body)
assert created.status_code == 201, created.text
publication = created.json()
assert (publication["asset_id"], publication["privacy_status"], publication["tags"]) == (final["id"], "unlisted", ["rừng", "đêm"])
again = client.post("/api/youtube/publications", json=body)
assert again.status_code == 200 and again.json()["id"] == publication["id"]
assert client.post("/api/youtube/publications", json={**body, "privacy_status": "public"}).status_code == 409
with Session() as db:
    assert db.scalar(select(func.count()).select_from(WorkflowJob).where(WorkflowJob.logical_key.like("publish:%"))) == 1
seen, fake_google = google(privacy="private")
with fake_google:
    drain(youtube_worker, client=fake_google, poll_seconds=0)
sent = json.loads(next(request for request in seen if request.method == "POST").content)
assert sent["status"]["privacyStatus"] == "unlisted" and sent["snippet"]["tags"] == ["rừng", "đêm"], sent
result = summary(run["id"])["publishing"]["publication"]
assert (result["state"], result["remote_id"], result["remote_status"], result["remote_privacy"]) == \
       ("succeeded", "abcdefghijk", "uploaded", "private"), result
assert result["youtube_url"] == "https://www.youtube.com/watch?v=abcdefghijk"
assert "oauth" not in json.dumps(result).lower() and "upload_id" not in json.dumps(result)
'''

RETRY = r'''
# A legacy workflow without Voice, Subtitle, Render or Publish still runs and publishes its clip.
add_tool("video", "fal", "fal-ai/veo3.1/fast")
workflow = client.post("/api/workflows", json={"name": "Clip"}).json()["id"]
fund(10)
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project}).json()
drain(video_worker, client=FakeVideos(), download=download, poll_seconds=0)
assert client.post(f"/api/workflow-runs/{run['id']}/approve").json()["status"] == "completed"
legacy = summary(run["id"])
assert legacy["final_video"]["final"] is False and legacy["counts"]["clips"] == 1
assert legacy["publishing"]["ready"] and legacy["publishing"]["defaults"]["source"] == "project"
assert legacy["publishing"]["defaults"]["title"] == "Rừng đêm"
connect_youtube()
created = client.post("/api/youtube/publications", json={"run_id": run["id"], "title": "Rừng đêm"}).json()
assert created["asset_id"] == legacy["final_video"]["asset_id"]
before = counts()
_, rejecting = google(start_status=400)
with rejecting:
    drain(youtube_worker, client=rejecting, poll_seconds=0)
failed = client.get(f"/api/youtube/publications/{created['id']}").json()
assert (failed["state"], failed["can_retry"]) == ("failed", True), failed
assert client.post(f"/api/youtube/publications/{created['id']}/retry", json={"tags": ["a,b"]}).status_code == 422
retried = client.post(f"/api/youtube/publications/{created['id']}/retry",
                      json={"title": "Rừng đêm (bản mới)", "privacy_status": "public"})
assert retried.status_code == 202, retried.text
assert (retried.json()["state"], retried.json()["title"], retried.json()["privacy_status"]) == \
       ("queued", "Rừng đêm (bản mới)", "public")
# Retrying publishes again from the same final video: nothing upstream is generated or charged again.
assert counts() == before
with Session() as db:
    assert db.scalar(select(func.count()).select_from(WorkflowJob).where(WorkflowJob.logical_key.like("publish:%"))) == 2
seen, accepting = google(privacy="public")
with accepting:
    drain(youtube_worker, client=accepting, poll_seconds=0)
done = client.get(f"/api/youtube/publications/{created['id']}").json()
assert (done["state"], done["remote_privacy"]) == ("succeeded", "public"), done
assert json.loads(next(r for r in seen if r.method == "POST").content)["snippet"]["title"] == "Rừng đêm (bản mới)"
assert counts() == before
assert client.post(f"/api/youtube/publications/{created['id']}/retry").status_code == 409
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
               if not key.startswith(("FAL_", "VIDEO_", "GEMINI", "VOICE_", "RENDER_", "OPENAI", "TEXT_", "GOOGLE_",
                                      "REELFORGE_TOKEN"))}
        return subprocess.run([sys.executable, "-c", PRELUDE + body], cwd=target,
                              env={**env, "PYTHONPATH": str(target)}, capture_output=True, text=True)


class SocialWorkflowTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stderr[-5000:])

    def test_templates_and_model_resolution(self):
        self.check(TEMPLATES_SCENARIO)

    def test_short_template_runs_to_a_youtube_upload(self):
        self.check(FULL_RUN)

    def test_publication_retry_reuses_the_video(self):
        self.check(RETRY)


if __name__ == "__main__":
    unittest.main()
