"""Shared scaffolding for Phase 10–13 integration tests.

Each program runs in a subprocess against a disposable SQLite database migrated to
head, with the API driven through FastAPI's TestClient. Every provider and
platform is faked or served by ``httpx.MockTransport``: no network call leaves the
process and no real key is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# TestClient talks plain HTTP to http://testserver, where a Secure session cookie is never sent back. The
# disposable app therefore runs as a development machine does: its instance/bootstrap.json overrides the
# production origin (https://studio.imokome-cloud.com, Secure cookies) with http://localhost:3000.
LOCAL_DEVELOPMENT = {"frontend_origin": "http://localhost:3000", "secure_cookies": False}

PRELUDE = r'''
import io, json, os, subprocess, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography.fernet import Fernet
tools = Path("instance/tools")
tools.mkdir(parents=True, exist_ok=True)
for name in ("ffmpeg", "ffprobe"):
    (tools / name).write_text("")
os.environ.update({
    "OPENAI_API_KEY": "sk-SENTINEL", "TEXT_CREDITS_PER_GENERATION": "1", "TRANSCRIPTION_CREDITS_PER_JOB": "2",
    "GEMINI_API_KEY": "gemini-SENTINEL", "VOICE_CREDITS_PER_GENERATION": "1",
    "RENDER_FFMPEG_PATH": str((tools / "ffmpeg").resolve()), "RENDER_FFPROBE_PATH": str((tools / "ffprobe").resolve()),
    "GOOGLE_OAUTH_CLIENT_ID": "test-client.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "google-SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:3000/youtube/callback",
    "TIKTOK_CLIENT_KEY": "tiktok-client", "TIKTOK_CLIENT_SECRET": "tiktok-SECRET",
    "TIKTOK_REDIRECT_URI": "http://localhost:3000/channels/callback/tiktok",
    "FACEBOOK_APP_ID": "1234567890", "FACEBOOK_APP_SECRET": "facebook-SECRET",
    "FACEBOOK_REDIRECT_URI": "http://localhost:3000/channels/callback/facebook",
    "REELFORGE_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode()})
import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update, text
command.upgrade(Config("alembic.ini"), "head")
import app.main as main
from app.main import app
from app import system_config
# The API activates database-backed settings in its lifespan, which TestClient(app) without "with" does not run.
system_config.activate()
from app.db import Session
from app import jobs, publications, render, render_worker, source_worker, text_worker, usage
from app.media_paths import media_root
from app.models import (Asset, CreditLedger, Plan, Project, UsageEvent, Workflow, WorkflowJob, WorkflowRun,
                        WorkflowRunStep)
from app.providers.text import TextResult, TextUsage
from app.providers.transcription import TranscriptResult, TranscriptSegment, TranscriptionProviderError
from app.publications import Publication
from app.publishers import channel_oauth
from app.publishers.google_oauth import YouTubeConnection
from app.publishers.youtube import UPLOAD_SCOPE

render.font_issue = lambda run=None: None
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64
SECRETS = ("sk-SENTINEL", "google-SECRET", "tiktok-SECRET", "facebook-SECRET", "tok-")

client = TestClient(app)
assert client.post("/api/setup", json={"email": "owner@example.com", "password": "long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
with Session.begin() as db:
    db.execute(update(Plan).values(workflow_limit=None, project_limit=None))
project = client.post("/api/projects", json={"title": "Rừng đêm", "topic": "Một đêm trong rừng"}).json()["id"]

def no_secrets(response_text):
    for secret in SECRETS:
        assert secret not in response_text, secret

def add_tool(task, provider, model):
    response = client.post("/api/ai-tools", json={"task": task, "provider": provider, "model": model})
    assert response.status_code == 201, response.text
    return response.json()["id"]

funded = []
def fund(credits):
    funded.append(credits)
    with Session.begin() as db:
        usage.post_credit(db, workspace, credits, "test", f"fund-{len(funded)}")

def balance():
    return client.get("/api/usage").json()["balance"]

def upload(name, data, content_type):
    return client.post("/api/assets", files={"file": (name, data, content_type)})

def save_graph(nodes, edges, name="Test"):
    workflow = client.post("/api/workflows", json={"name": name}).json()["id"]
    response = client.put(f"/api/workflows/{workflow}", json={"nodes": nodes, "edges": edges})
    return workflow, response

def node(node_id, node_type, config=None, x=0):
    item = {"id": node_id, "type": node_type, "x": x, "y": 0}
    if config:
        item["config"] = config
    return item

def edge(source, source_handle, target, target_handle):
    return {"source": source, "target": target, "sourceHandle": source_handle, "targetHandle": target_handle}

def start(workflow):
    response = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
    assert response.status_code == 201, response.text
    return response.json()

def steps(run_id):
    run = client.get(f"/api/workflow-runs/{run_id}").json()
    return run, {step["node_id"]: step for step in run["steps"]}

def drain(module, **kwargs):
    count = 0
    while module.run_one(**kwargs):
        count += 1
        assert count < 80

class Writer:
    """A text provider whose replies depend on the node type named in the prompt's system text."""
    def __init__(self, provider, replies=None):
        self.provider, self.replies = provider, replies or {}
    def generate(self, **kwargs):
        WRITER_PROMPTS.append(kwargs)
        system = kwargs.get("system_prompt") or ""
        text = next((reply for key, reply in self.replies.items() if key in system), "Bản tóm tắt ngắn.")
        return TextResult(text=text, usage=TextUsage.of(10, 20), provider=self.provider, model=kwargs["model"])
    def close(self): pass
WRITER_PROMPTS = []

def writer(replies=None):
    return lambda name: Writer(name, replies)

class Transcriber:
    """A transcription provider returning fixed segments per audio piece."""
    def __init__(self, segments=None, error=None):
        self.segments, self.error, self.calls = segments or [(0.0, 2.0, "Xin chào")], error, []
    def transcribe(self, path, *, model, language=None):
        self.calls.append((path.name, model, language))
        if self.error:
            raise self.error
        return TranscriptResult("openai", model, " ".join(t for _, _, t in self.segments), "vietnamese", 10.0,
                                tuple(TranscriptSegment(s, e, t) for s, e, t in self.segments), "req_1")
    def close(self): pass

def ffmpeg_runner(duration=30.0, parts=1, fail_copy=(), has_audio=True, width=720):
    """Fakes ffprobe/ffmpeg: probes report ``duration``; commands write the file they name."""
    calls = []
    def run(args, **kwargs):
        calls.append(list(args))
        if Path(args[0]).name == "ffprobe":
            streams = [{"codec_type": "video", "width": width, "height": 1280}] + (
                [{"codec_type": "audio"}] if has_audio else [])
            probed = Path(args[-1]).name
            length = duration if not probed.startswith(("clip-", "render")) else 4.0
            return subprocess.CompletedProcess(args, 0, json.dumps({"streams": streams,
                                                                    "format": {"duration": str(length)}}), "")
        folder = Path(kwargs["cwd"])
        output = args[-1]
        if output == "part-%03d.mp3":
            for index in range(parts):
                (folder / f"part-{index:03d}.mp3").write_bytes(MP3)
        elif output.startswith("clip-") and "copy" in args and output in fail_copy:
            return subprocess.CompletedProcess(args, 1, "", "Could not write header for output file")
        else:
            (folder / output).write_bytes(VALID_MP4)
        return subprocess.CompletedProcess(args, 0, "", "")
    run.calls = calls
    return run

def approved_run(filename="final.mp4"):
    """A completed run whose render step made an approved MP4 (the Review step approved it)."""
    now = datetime.now(timezone.utc)
    ids = {key: os.urandom(8).hex() for key in ("workflow", "run", "render", "review")}
    asset_id = str(__import__("uuid").uuid4())
    with Session.begin() as db:
        db.add(Workflow(id=ids["workflow"], workspace_id=workspace, name="Done", definition=json.dumps({"nodes": [], "edges": []})))
        db.flush()
        db.add(WorkflowRun(id=ids["run"], workspace_id=workspace, workflow_id=ids["workflow"], project_id=project,
                           graph_snapshot=json.dumps({"nodes": [], "edges": []}), status="completed", created_at=now,
                           finished_at=now))
        db.flush()
        db.add(WorkflowRunStep(id=ids["render"], run_id=ids["run"], node_id="render", node_type="render", position=0,
                               status="completed", detail="", output=json.dumps({"video_assets": [{"id": asset_id}],
                                                                                   "asset_ids": [asset_id]})))
        db.add(WorkflowRunStep(id=ids["review"], run_id=ids["run"], node_id="review", node_type="review", position=1,
                               status="completed", detail="", output=json.dumps({"approved_by": "owner"})))
        db.flush()
        db.add(Asset(id=asset_id, workspace_id=workspace, project_id=project, run_id=ids["run"], step_id=ids["render"],
                     provider="ffmpeg", model="local", filename=filename, content_type="video/mp4", bytes=len(VALID_MP4),
                     kind="final_render"))
        root = media_root(db) / workspace
    root.mkdir(parents=True, exist_ok=True)
    (root / asset_id).write_bytes(VALID_MP4)
    return ids["run"], asset_id

def connect_youtube():
    now = datetime.now(timezone.utc)
    cipher = Fernet(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"].encode())
    with Session.begin() as db:
        db.add(YouTubeConnection(workspace_id=workspace, access_token_ciphertext=cipher.encrypt(b"tok-yt-access").decode(),
                                 refresh_token_ciphertext=cipher.encrypt(b"tok-yt-refresh").decode(),
                                 expires_at=now + timedelta(hours=1), scope=UPLOAD_SCOPE, connected_at=now, updated_at=now))

def connect_channel(channel, *, expires_in=3600, account="Kênh thử", scope=None, refresh=True):
    """A stored grant, as a completed OAuth callback leaves it."""
    now = datetime.now(timezone.utc)
    cipher = Fernet(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"].encode())
    with Session.begin() as db:
        db.merge(channel_oauth.ChannelConnection(
            workspace_id=workspace, channel=channel, account_id="1111" if channel == "facebook" else "open-1",
            account_name=account, access_token_ciphertext=cipher.encrypt(f"tok-{channel}-access".encode()).decode(),
            refresh_token_ciphertext=cipher.encrypt(b"tok-tiktok-refresh").decode() if refresh and channel == "tiktok" else None,
            expires_at=now + timedelta(seconds=expires_in), refresh_expires_at=now + timedelta(days=300),
            scope=scope if scope is not None else ("user.info.basic,video.upload" if channel == "tiktok" else
                                                   "pages_show_list,pages_read_engagement,pages_manage_posts"),
            connected_at=now, updated_at=now))

def publish(run_id, *targets, scheduled_for=None, status=None):
    response = client.post("/api/publications", json={"run_id": run_id, "scheduled_for": scheduled_for,
                                                       "targets": list(targets)})
    if status is not None:
        assert response.status_code == status, response.text
    return response

def target(channel, **values):
    privacy = {"youtube": "private", "tiktok": "private", "facebook": "public"}[channel]
    return {"channel": channel, "title": "Rừng đêm", "description": "Một đêm yên tĩnh.", "tags": ["rừng"],
            "privacy_status": privacy, **values}

def publication(publication_id):
    response = client.get(f"/api/publications/{publication_id}")
    assert response.status_code == 200, response.text
    return response.json()
'''


def run_program(body: str, env: dict | None = None):
    """Run ``PRELUDE + body`` in a disposable copy of the app; returns the completed process."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("app", "migrations"):
            shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
        (target / "instance").mkdir()
        (target / "instance" / "bootstrap.json").write_text(json.dumps({
            "database_url": f"sqlite:///{target}/instance/test.db", **LOCAL_DEVELOPMENT}))
        clean = {key: value for key, value in os.environ.items()
                 if not key.startswith(("FAL_", "VIDEO_", "GEMINI", "VOICE_", "RENDER_", "OPENAI", "TEXT_", "GOOGLE_",
                                        "REELFORGE_TOKEN", "TIKTOK_", "FACEBOOK_", "TRANSCRIPTION_", "RUNWAY",
                                        "ANTHROPIC", "REPLICATE", "RUNWARE", "DOLA", "ONEPAY_", "WORKSPACE_MEDIA",
                                        "REELFORGE_STORAGE", "REELFORGE_RETENTION"))}
        return subprocess.run([sys.executable, "-c", PRELUDE + body], cwd=target,
                              env={**clean, **(env or {}), "PYTHONPATH": str(target), "PYTHONIOENCODING": "utf-8"},
                              capture_output=True, text=True, encoding="utf-8")
