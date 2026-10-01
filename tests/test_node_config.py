"""Node settings: the schema the editor renders, validation, their effect on runs, and snapshots."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.models import (AITool, Base, CreditAccount, Project, User, Workflow, WorkflowJob, WorkflowRun,
                        WorkflowRunStep, Workspace, WorkspaceSetting)
from app.providers.catalog import video_duration_supported
from app.workflow import (ExecutionContext, NodeExecutionResult, NodeHandler, WorkflowExecutor, build_default_registry,
                          default_registry, parse_graph)
from app.workflow.config import ConfigError, check_tools, config_values
from app.workflow.nodes.base import INVALID_CONFIG_DETAIL
from app.workflow.nodes.scenes import split_scenes
from app.workflow.ports import TEXT, OutputPort, describe_node_types

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"OPENAI_API_KEY": "sk-test", "FAL_KEY": "fal-test", "RUNWARE_API_KEY": "rw-test",
        "TEXT_CREDITS_PER_GENERATION": "1", "VIDEO_CREDITS_PER_CLIP": "10"}
SCRIPT = "Cảnh 1: Rừng đêm tĩnh lặng.\n\nCảnh 2: Một con cú bay qua.\n\nCảnh 3: Bình minh lên."
EXECUTABLE = ("ai_writer", "summarize", "rewrite", "translate", "hook", "title", "cta", "scenes", "image", "video",
              "voice", "subtitle")


def node(node_id, node_type, config=None):
    item = {"id": node_id, "type": node_type, "x": 0, "y": 0}
    if config is not None:
        item["config"] = config
    return item


def edge(source, target, source_handle=None, target_handle=None):
    item = {"source": source, "target": target}
    if source_handle:
        item["sourceHandle"] = source_handle
    if target_handle:
        item["targetHandle"] = target_handle
    return item


class ScriptHandler(NodeHandler):
    """Stands in for a finished AI Writer."""

    node_type = "script_source"
    outputs = (OutputPort("script", TEXT),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("script", {"script": SCRIPT})


def with_script_source():
    registry = build_default_registry()
    registry.register(ScriptHandler())
    return registry


class SchemaTest(unittest.TestCase):
    def catalog(self):
        return describe_node_types(default_registry)

    def fields(self, node_type):
        return {field["key"]: field for field in self.catalog()[node_type]["config"]}

    def test_every_executable_node_describes_its_settings(self):
        catalog = self.catalog()
        for node_type in EXECUTABLE:
            with self.subTest(node_type=node_type):
                fields = catalog[node_type]["config"]
                self.assertTrue(fields)
                for field in fields:
                    self.assertTrue({"key", "type", "label", "default", "required", "advanced", "code"} <= set(field))
                    # Every default passes its own validation.
                    default_registry.resolve(node_type).validate_config({field["key"]: field["default"]})
        for node_type in ("idea", "assets", "review", "script"):
            self.assertEqual(catalog[node_type]["config"], [])

    def test_fields_match_the_editor_contract(self):
        writer = self.fields("ai_writer")
        self.assertEqual(list(writer)[:7], ["language", "tone", "platform", "duration", "prompt", "instructions",
                                            "tool_id"])
        self.assertEqual(writer["language"]["options"], ["auto", "vi", "en", "ja"])
        self.assertEqual(writer["tone"]["options"], ["neutral", "casual", "professional", "cinematic", "storytelling",
                                                     "documentary", "dramatic", "funny"])
        self.assertEqual(writer["platform"]["options"], ["generic", "youtube", "youtube_shorts", "tiktok", "facebook"])
        self.assertEqual(writer["duration"]["presets"], [30, 60, 180, 300, 600])
        self.assertEqual((writer["tool_id"]["type"], writer["tool_id"]["task"], writer["tool_id"]["providers"]),
                         ("tool", "script", ["openai", "anthropic", "gemini"]))
        self.assertTrue(writer["max_tokens"]["advanced"] and writer["temperature"]["advanced"])
        self.assertEqual(self.fields("summarize")["length"]["options"], ["short", "medium", "detailed"])
        self.assertEqual(self.fields("translate")["target_language"]["options"], ["auto", "vi", "en", "ja"])
        self.assertEqual([self.fields(t)["count"]["default"] for t in ("hook", "title", "cta")], [3, 5, 3])
        scenes = self.fields("scenes")
        self.assertEqual((scenes["scene_duration"]["default"], scenes["max_scenes"]["default"]), (6, 12))
        self.assertEqual(scenes["visual_style"]["options"],
                         ["generic", "cinematic", "realistic", "anime", "documentary", "minimal"])
        video = self.fields("video")
        self.assertEqual(list(video), ["tool_id", "aspect_ratio", "duration", "prompt"])
        self.assertEqual(video["aspect_ratio"]["options"], ["auto", "9:16", "16:9"])
        self.assertEqual(video["tool_id"]["task"], "video")
        self.assertNotIn("KEY", json.dumps(self.catalog()))

    def test_valid_settings(self):
        valid = {
            "ai_writer": {"language": "vi", "tone": "cinematic", "platform": "youtube", "duration": 45,
                          "prompt": "Rừng đêm", "instructions": "Ngắn gọn", "tool_id": "tool-1",
                          "temperature": 0.5, "max_tokens": 1024},
            "summarize": {"length": "short", "language": "en", "instructions": "x"},
            "rewrite": {"tone": "funny", "platform": "tiktok", "length": "longer", "instructions": "x"},
            "translate": {"target_language": "ja"},
            "hook": {"count": 10, "tone": "dramatic", "platform": "youtube_shorts"},
            "title": {"count": 1, "platform": "facebook", "style": "question"},
            "cta": {"count": 2, "tone": "professional", "platform": "generic"},
            "scenes": {"scene_duration": 10, "max_scenes": 20, "visual_style": "anime"},
            "video": {"tool_id": "video-1", "aspect_ratio": "16:9", "duration": "6s", "prompt": "Mưa"},
        }
        for node_type, config in valid.items():
            with self.subTest(node_type=node_type):
                default_registry.resolve(node_type).validate_config(config)
        # null means "use the default".
        default_registry.resolve("scenes").validate_config({"max_scenes": None})

    def test_invalid_settings_have_stable_codes(self):
        cases = [
            ("ai_writer", {"language": "fr"}, "invalid_language", "language"),
            ("translate", {"target_language": "Vietnamese"}, "invalid_language", "target_language"),
            ("ai_writer", {"duration": 2}, "invalid_duration", "duration"),
            ("ai_writer", {"duration": "60"}, "invalid_duration", "duration"),
            ("ai_writer", {"tone": "calm"}, "invalid_tone", "tone"),
            ("ai_writer", {"prompt": "x" * 3001}, "invalid_prompt", "prompt"),
            ("ai_writer", {"tool_id": "not a tool!"}, "unsupported_model", "tool_id"),
            ("summarize", {"length": "huge"}, "invalid_length", "length"),
            ("hook", {"count": 11}, "invalid_count", "count"),
            ("rewrite", {"temperature": True}, "invalid_temperature", "temperature"),
            ("scenes", {"max_scenes": 21}, "invalid_scene_limit", "max_scenes"),
            ("scenes", {"max_scenes": 0}, "invalid_scene_limit", "max_scenes"),
            ("scenes", {"scene_duration": 1}, "invalid_duration", "scene_duration"),
            ("scenes", {"visual_style": "noir"}, "invalid_style", "visual_style"),
            ("video", {"aspect_ratio": "1:1"}, "invalid_aspect_ratio", "aspect_ratio"),
            ("video", {"duration": "30s"}, "invalid_duration", "duration"),
            ("video", {"prompt": "x" * 1001}, "invalid_prompt", "prompt"),
            ("video", {"resolution": "4k"}, "unknown_setting", "resolution"),
            ("idea", {"prompt": "x"}, "unknown_setting", "prompt"),
            ("scenes", ["max_scenes"], "invalid_config", None),
        ]
        for node_type, config, code, field in cases:
            with self.subTest(node_type=node_type, config=config):
                with self.assertRaises(ConfigError) as caught:
                    default_registry.resolve(node_type).validate_config(config)
                self.assertEqual((caught.exception.code, caught.exception.field), (code, field))
                self.assertIsInstance(caught.exception, ValueError)

    def test_defaults_fill_missing_settings(self):
        scenes = default_registry.resolve("scenes")
        self.assertEqual(scenes.config_values(None), {"scene_duration": 6, "max_scenes": 12, "visual_style": "generic"})
        self.assertEqual(scenes.config_values({"max_scenes": 3, "visual_style": None})["visual_style"], "generic")
        self.assertEqual(config_values(default_registry.resolve("translate").config_fields, {})["max_tokens"], 4096)

    def test_tool_settings_must_name_a_compatible_workspace_tool(self):
        class Tool:
            def __init__(self, task, provider):
                self.task, self.provider = task, provider

        tools = {"text-1": Tool("script", "openai"), "video-1": Tool("video", "fal"), "odd": Tool("script", "acme")}
        writer, video = default_registry.resolve("ai_writer"), default_registry.resolve("video")
        check_tools(writer.config_fields, {"tool_id": "text-1"}, tools)
        check_tools(video.config_fields, {"tool_id": "video-1"}, tools)
        check_tools(writer.config_fields, None, tools)
        for handler, tool_id in ((writer, "video-1"), (writer, "missing"), (writer, "odd"), (video, "text-1")):
            with self.subTest(tool_id=tool_id), self.assertRaises(ConfigError) as caught:
                check_tools(handler.config_fields, {"tool_id": tool_id}, tools)
            self.assertEqual(caught.exception.code, "unsupported_model")

    def test_video_durations_follow_each_model(self):
        self.assertTrue(video_duration_supported("fal", "fal-ai/veo3.1/fast", "6s"))
        self.assertFalse(video_duration_supported("fal", "fal-ai/veo3.1/fast", "5s"))
        self.assertTrue(video_duration_supported("runway", "gen4.5", "4s"))
        self.assertTrue(video_duration_supported("runware", "bytedance:seedance@2.5", "8s"))
        self.assertFalse(video_duration_supported("dola", "seedance-2.5", "8s"))
        self.assertFalse(video_duration_supported("fal", "unknown-model", "8s"))


class SceneSettingsTest(unittest.TestCase):
    def test_target_duration_splits_long_paragraphs(self):
        paragraph = " ".join(f"Câu {i} kể chuyện rừng đêm." for i in range(8))  # 48 words
        self.assertEqual(len(split_scenes(paragraph, scene_seconds=16)), 1)
        # 4 seconds is about 10 words, so 48 words make 5 scenes.
        self.assertEqual(len(split_scenes(paragraph, scene_seconds=4)), 5)
        self.assertEqual(len(split_scenes(paragraph, max_scenes=2, scene_seconds=2)), 2)

    def test_visual_style_is_added_to_visual_prompts(self):
        scenes = split_scenes(SCRIPT, visual_style="cinematic")
        self.assertEqual(scenes[0]["text"], "Rừng đêm tĩnh lặng.")
        self.assertEqual(scenes[0]["visual_prompt"], "Rừng đêm tĩnh lặng. Cinematic style.")
        long = split_scenes("x" * 800, visual_style="anime")[0]["visual_prompt"]
        self.assertLessEqual(len(long), 500)
        self.assertTrue(long.endswith("Anime style."))


@patch.dict(os.environ, KEYS)
class ConfiguredRunTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'config.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="config@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Config", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Rừng đêm"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=100))
            db.add(WorkspaceSetting(workspace_id="space-1", key="video_orientation", value='"vertical"'))
            db.add(WorkspaceSetting(workspace_id="space-1", key="default_language", value='"vi"'))
            db.add(AITool(id="text-1", workspace_id="space-1", task="script", provider="openai", model="gpt-4.1-mini"))
            db.add(AITool(id="text-2", workspace_id="space-1", task="script", provider="openai", model="gpt-4.1"))
            db.add(AITool(id="video-1", workspace_id="space-1", task="video", provider="fal", model="fal-ai/veo3.1/fast"))
            db.add(AITool(id="video-2", workspace_id="space-1", task="video", provider="runware",
                          model="bytedance:seedance@2.5"))

    def start(self, graph, *, registry=None):
        with self.Session.begin() as db:
            run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1", project_id="project-1",
                              graph_snapshot=json.dumps(graph), status="running", created_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), project=db.get(Project, "project-1"),
                                       run=run, graph=graph)
            progress = WorkflowExecutor(registry).start_run(context)
            return run.status, {step.node_id: step for step in progress.steps}

    def payload(self, step):
        with self.Session() as db:
            return db.scalar(select(WorkflowJob).where(WorkflowJob.step_id == step.id)).payload

    def payloads(self, step):
        with self.Session() as db:
            jobs = db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id))
            return sorted((job.payload for job in jobs), key=lambda payload: payload["index"])

    def readiness(self, graph, registry=None):
        with self.Session() as db:
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            return {node["id"]: check for node, check in WorkflowExecutor(registry).readiness(context)}

    def balance(self):
        with self.Session() as db:
            return db.get(CreditAccount, "space-1").balance

    def test_text_settings_reach_the_prompt_and_model(self):
        graph = {"nodes": [node("idea", "idea"),
                           node("writer", "ai_writer", {"language": "en", "tone": "documentary",
                                                        "platform": "youtube_shorts", "duration": 180,
                                                        "tool_id": "text-2", "max_tokens": 900}),
                           node("summary", "summarize", {"length": "short"}),
                           node("title", "title", {"count": 4, "style": "question", "platform": "tiktok"}),
                           node("japanese", "translate", {"target_language": "ja"})],
                 "edges": [edge("idea", "writer"), edge("idea", "summary", "topic", "text"), edge("idea", "title"),
                           edge("idea", "japanese", "topic", "text")]}
        _, steps = self.start(graph)
        writer = self.payload(steps["writer"])
        self.assertEqual((writer["model"], writer["tool_id"], writer["language"], writer["max_tokens"]),
                         ("gpt-4.1", "text-2", "en", 900))
        for expected in ("in English", "Tone: documentary.", "YouTube Shorts", "about 180 seconds"):
            self.assertIn(expected, writer["prompt"])
        summary = self.payload(steps["summary"])
        self.assertIn("in 2 to 3 sentences", summary["prompt"])
        self.assertEqual((summary["model"], summary["language"]), ("gpt-4.1-mini", "vi"))
        title = self.payload(steps["title"])["prompt"]
        for expected in ("Write 4 alternative video titles", "phrased as a question", "TikTok"):
            self.assertIn(expected, title)
        self.assertEqual(self.payload(steps["japanese"])["language"], "ja")

    def test_scene_and_video_settings_shape_the_clip(self):
        graph = {"nodes": [node("writer", "script_source"),
                           node("scenes", "scenes", {"max_scenes": 2, "visual_style": "cinematic"}),
                           node("video", "video", {"tool_id": "video-2", "aspect_ratio": "16:9", "duration": "6s"})],
                 "edges": [edge("writer", "scenes"), edge("scenes", "video", "scenes", "scenes")]}
        _, steps = self.start(graph, registry=with_script_source())
        scenes = json.loads(steps["scenes"].output)["scenes"]
        self.assertEqual(len(scenes), 2)
        self.assertTrue(all(scene["visual_prompt"].endswith("Cinematic style.") for scene in scenes))
        payload = self.payload(steps["video"])
        self.assertEqual((payload["provider"], payload["model_id"], payload["aspect_ratio"], payload["duration"]),
                         ("runware", "bytedance:seedance@2.5", "16:9", "6s"))
        self.assertEqual([item["prompt"] for item in self.payloads(steps["video"])],
                         [scene["visual_prompt"] for scene in scenes])
        self.assertEqual(json.loads(steps["video"].output)["aspect_ratio"], "16:9")

    def test_video_prompt_override_beats_connected_scenes(self):
        graph = {"nodes": [node("writer", "script_source"), node("scenes", "scenes"),
                           node("video", "video", {"prompt": "  Mưa trên mái nhà  "})],
                 "edges": [edge("writer", "scenes"), edge("scenes", "video", "scenes", "scenes")]}
        _, steps = self.start(graph, registry=with_script_source())
        self.assertEqual(self.payload(steps["video"])["prompt"], "Mưa trên mái nhà")

    def test_invalid_settings_block_only_that_node_without_charging(self):
        graph = {"nodes": [node("idea", "idea"), node("writer", "ai_writer", {"language": "fr"}),
                           node("scenes", "scenes", {"max_scenes": 50}), node("assets", "assets")],
                 "edges": [edge("idea", "writer"), edge("idea", "scenes", "title", "script")]}
        status, steps = self.start(graph)
        self.assertEqual(status, "blocked")
        self.assertEqual((steps["idea"].status, steps["assets"].status), ("completed", "completed"))
        for node_id, code, field in (("writer", "invalid_language", "language"),
                                     ("scenes", "invalid_scene_limit", "max_scenes")):
            step = steps[node_id]
            self.assertEqual((step.status, step.detail), ("blocked", INVALID_CONFIG_DETAIL))
            self.assertEqual(json.loads(step.output),
                             {"invalid_setting": field, "error": {"code": code, "retryable": False}})
        self.assertEqual(self.balance(), 100)
        with self.Session() as db:
            self.assertIsNone(db.scalar(select(WorkflowJob)))

    def test_a_selected_model_that_is_no_longer_enabled_blocks(self):
        with self.Session.begin() as db:
            db.get(AITool, "text-2").is_enabled = False
            db.get(AITool, "video-2").is_enabled = False
        graph = {"nodes": [node("writer", "ai_writer", {"tool_id": "text-2"}),
                           node("video", "video", {"tool_id": "video-2"})], "edges": []}
        checks = self.readiness(graph)
        self.assertEqual({key: (c.status, c.field) for key, c in checks.items()},
                         {"writer": ("tool_unavailable", "tool_id"), "video": ("tool_unavailable", "tool_id")})
        _, steps = self.start(graph)
        self.assertEqual([json.loads(steps[key].output)["error"]["code"] for key in ("writer", "video")],
                         ["tool_unavailable", "tool_unavailable"])
        self.assertEqual(self.balance(), 100)

    def test_readiness_reports_settings_inputs_and_credits(self):
        graph = {"nodes": [node("idea", "idea"), node("writer", "ai_writer", {"platform": "myspace"}),
                           node("scenes", "scenes"), node("summary", "summarize"),
                           node("video", "video", {"duration": "4s"})],
                 "edges": [edge("idea", "writer")]}
        checks = self.readiness(graph)
        self.assertEqual((checks["writer"].status, checks["writer"].code, checks["writer"].field),
                         ("invalid_settings", "invalid_platform", "platform"))
        # Scenes needs a script; summarize falls back to the project topic.
        self.assertEqual((checks["scenes"].status, checks["scenes"].code, checks["scenes"].field),
                         ("missing_input", "missing_input", "script"))
        self.assertEqual((checks["summary"].status, checks["video"].status), ("ready", "ready"))
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance = 0
        self.assertEqual(self.readiness(graph)["video"].status, "insufficient_credits")
        with patch("app.workflow.nodes.video.video_duration_supported", return_value=False):
            with self.Session.begin() as db:
                db.get(CreditAccount, "space-1").balance = 100
            video = self.readiness(graph)["video"]
        self.assertEqual((video.status, video.code, video.field), ("invalid_settings", "invalid_duration", "duration"))

    def test_legacy_workflows_without_settings_keep_their_behavior(self):
        # A pre-editor definition: a plain list of node types, no settings, no handles.
        graph = parse_graph('["idea", "scenes", "video", "review"]')
        self.assertTrue(all("config" not in item for item in graph["nodes"]))
        self.assertEqual({c.status for c in self.readiness(graph).values()}, {"configured", "ready"})
        _, steps = self.start(graph)
        self.assertEqual([steps[f"n{i}"].status for i in range(4)], ["completed", "completed", "queued", "skipped"])
        payload = self.payload(steps["n2"])
        # Defaults: first enabled video tool, workspace orientation, provider's clip length.
        self.assertEqual((payload["tool_id"], payload["aspect_ratio"], payload["duration"], payload["prompt"]),
                         ("video-1", "9:16", "8s", "Rừng đêm"))
        # Explicit empty or null settings behave like none at all.
        graph = {"nodes": [node("idea", "idea", {}), node("writer", "ai_writer", {"language": None})],
                 "edges": [edge("idea", "writer")]}
        _, steps = self.start(graph)
        self.assertEqual(self.payload(steps["writer"])["language"], "vi")


class NodeConfigApiTest(unittest.TestCase):
    """Settings over HTTP: catalog, save and reload, validation codes, readiness, snapshots and retries."""

    def test_node_settings_over_http(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            program = r'''
import json, os
os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["FAL_KEY"] = "fal-test"
os.environ["VIDEO_CREDITS_PER_CLIP"] = "10"
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app.models import Workflow, WorkflowJob, WorkflowRun, WorkflowRunStep
from app import usage
from sqlalchemy import select
client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
catalog = client.get("/api/workflow-node-types").json()["node_types"]
assert [f["key"] for f in catalog["scenes"]["config"]] == ["scene_duration", "max_scenes", "visual_style"]
assert catalog["idea"]["config"] == []
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Rừng đêm.\n\nCon cú bay."}).json()["id"]
text = client.post("/api/ai-tools", json={"task":"script","provider":"openai","model":"gpt-4.1-mini","is_enabled":False}).json()
video = client.post("/api/ai-tools", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast"}).json()
workflow = client.post("/api/workflows", json={"name":"Configured"}).json()["id"]
writer = {"language":"vi","tone":"cinematic","platform":"youtube","tool_id":text["id"]}
graph = {"nodes": [{"id":"idea","type":"idea","x":0,"y":0},
                   {"id":"writer","type":"ai_writer","x":1,"y":0,"config":writer},
                   {"id":"scenes","type":"scenes","x":2,"y":0,"config":{"scene_duration":10,"max_scenes":6}},
                   {"id":"video","type":"video","x":3,"y":0,"config":{"tool_id":video["id"],"aspect_ratio":"16:9"}},
                   {"id":"review","type":"review","x":4,"y":0}],
         "edges": [{"source":"idea","target":"writer","sourceHandle":"topic","targetHandle":"prompt"},
                   {"source":"writer","target":"scenes","sourceHandle":"script","targetHandle":"script"},
                   {"source":"scenes","target":"video","sourceHandle":"scenes","targetHandle":"scenes"},
                   {"source":"video","target":"review","sourceHandle":"video_assets","targetHandle":"media"}]}
saved = client.put(f"/api/workflows/{workflow}", json=graph)
assert saved.status_code == 200, saved.text
# Reopening the workflow (a browser refresh) returns exactly what was saved.
reopened = next(w for w in client.get("/api/dashboard").json()["workflows"] if w["id"] == workflow)
assert [n.get("config") for n in reopened["graph"]["nodes"]] == [None, writer, {"scene_duration":10,"max_scenes":6},
                                                               {"tool_id":video["id"],"aspect_ratio":"16:9"}, None]

def rejected(node_id, config, code):
    nodes = [dict(n, config=config) if n["id"] == node_id else n for n in graph["nodes"]]
    response = client.put(f"/api/workflows/{workflow}", json={**graph, "nodes": nodes})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert (detail["code"], detail["node_id"]) == (code, node_id), detail
    assert detail["message"].startswith(f"Invalid settings for step {node_id}")
    return detail

assert rejected("writer", {"language":"fr"}, "invalid_language")["field"] == "language"
rejected("scenes", {"max_scenes":99}, "invalid_scene_limit")
rejected("video", {"duration":"20s"}, "invalid_duration")
rejected("writer", {"tool_id": video["id"]}, "unsupported_model")
rejected("video", {"tool_id": "00000000-0000-0000-0000-000000000000"}, "unsupported_model")
rejected("idea", {"prompt":"x"}, "unknown_setting")

# Readiness sees the saved settings: the chosen text model is disabled.
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
steps = {s["node_id"]: s for s in readiness["steps"]}
assert (steps["writer"]["status"], steps["writer"]["field"]) == ("tool_unavailable", "tool_id"), steps
assert steps["scenes"]["status"] == "configured" and steps["scenes"]["code"] is None
assert not readiness["runnable"]
with Session.begin() as db: usage.post_credit(db, workspace, 20, "test", "fund")

# The run blocks at the writer; its snapshot freezes the settings used.
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
assert run.status_code == 201, run.text
run = run.json()
assert [s["status"] for s in run["steps"]] == ["completed", "blocked", "skipped", "skipped", "skipped"], run
assert run["steps"][1]["output"]["error"]["code"] == "tool_unavailable"

# Editing the workflow afterwards changes neither that run's snapshot nor a retry of it.
changed = json.loads(json.dumps(graph))
changed["nodes"][1]["config"] = {**writer, "language":"en"}
changed["nodes"][2]["config"] = {"visual_style":"anime"}
assert client.put(f"/api/workflows/{workflow}", json=changed).status_code == 200
with Session() as db:
    snapshot = json.loads(db.get(WorkflowRun, run["id"]).graph_snapshot)
assert [n.get("config") for n in snapshot["nodes"]][1:3] == [writer, {"scene_duration":10,"max_scenes":6}]
assert client.put(f"/api/ai-tools/{text['id']}", json={"task":"script","provider":"openai","model":"gpt-4.1-mini","is_enabled":True}).status_code == 200
retry = client.post(f"/api/workflow-runs/{run['id']}/retry")
assert retry.status_code == 201, retry.text
retry = retry.json()
with Session() as db:
    retried = json.loads(db.get(WorkflowRun, retry["id"]).graph_snapshot)
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == retry["id"]))
assert retried["nodes"] == snapshot["nodes"]
assert job.payload["language"] == "vi" and "Tone: cinematic." in job.payload["prompt"], job.payload

# A new run uses the edited settings.
fresh = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project}).json()
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == fresh["id"]))
assert job.payload["language"] == "en", job.payload

# Settings stored before this validation existed never crash a run: that node is blocked with its code.
with Session.begin() as db:
    stored = db.get(Workflow, workflow)
    legacy = json.loads(stored.definition)
    legacy["nodes"][2]["config"] = {"max_scenes": 500}
    stored.definition = json.dumps(legacy)
steps = {s["node_id"]: s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert (steps["scenes"]["status"], steps["scenes"]["code"], steps["scenes"]["field"]) == ("invalid_settings", "invalid_scene_limit", "max_scenes"), steps
legacy_run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
assert legacy_run.status_code == 201, legacy_run.text

# A retry repeats the original video request. With a text branch beside the video, the run's first
# job is a text job; the retry must still freeze the video job, or the video step would block.
branches = client.post("/api/workflows", json={"name":"Branches"}).json()["id"]
assert client.put(f"/api/workflows/{branches}", json={
    "nodes": [{"id":"idea","type":"idea","x":0,"y":0}, {"id":"writer","type":"ai_writer","x":1,"y":0},
              {"id":"video","type":"video","x":1,"y":1,"config":{"aspect_ratio":"16:9","duration":"4s"}}],
    "edges": [{"source":"idea","target":"writer"}, {"source":"idea","target":"video"}]}).status_code == 200
with Session.begin() as db: usage.post_credit(db, workspace, 30, "test", "fund-branches")
first = client.post(f"/api/workflows/{branches}/runs", json={"project_id": project}).json()
assert [s["status"] for s in first["steps"]] == ["completed", "queued", "queued"], first
with Session.begin() as db:
    first_jobs = db.scalars(select(WorkflowJob).where(WorkflowJob.run_id == first["id"]).order_by(WorkflowJob.created_at)).all()
    assert [j.logical_key.split(":")[0] for j in first_jobs] == ["text", "video"]
    video_job = first_jobs[1]
    assert (video_job.payload["aspect_ratio"], video_job.payload["duration"]) == ("16:9", "4s"), video_job.payload
    # The provider rejected the clip.
    for job in first_jobs: job.state = "failed"
    for step in db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == first["id"], WorkflowRunStep.status == "queued")):
        step.status = "failed"
    db.get(WorkflowRun, first["id"]).status = "failed"
retried = client.post(f"/api/workflow-runs/{first['id']}/retry")
assert retried.status_code == 201, retried.text
retried = retried.json()
assert [s["status"] for s in retried["steps"]] == ["completed", "queued", "queued"], retried
with Session() as db:
    again = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == retried["id"], WorkflowJob.logical_key.like("video:%")))
assert {k: again.payload[k] for k in ("provider", "prompt", "aspect_ratio", "duration", "credits")} ==        {k: video_job.payload[k] for k in ("provider", "prompt", "aspect_ratio", "duration", "credits")}

# The run dialog's older prompt and model fields still work for workflows without node settings.
# (Trial plans allow two workflows, so the branches one is reused without settings.)
assert client.put(f"/api/workflows/{branches}", json={
    "nodes": [{"id":"idea","type":"idea","x":0,"y":0}, {"id":"video","type":"video","x":1,"y":0}],
    "edges": [{"source":"idea","target":"video"}]}).status_code == 200
with Session.begin() as db: usage.post_credit(db, workspace, 10, "test", "fund-plain")
started = client.post(f"/api/workflows/{branches}/runs", json={"project_id": project, "prompt": "Mưa đêm", "tool_id": video["id"]})
assert started.status_code == 201, started.text
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == started.json()["id"]))
assert (job.payload["prompt"], job.payload["aspect_ratio"]) == ("Mưa đêm", "9:16"), job.payload
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
