"""Typed ports: edge mapping, input resolution, required inputs and scene passing."""
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
from app.workflow import (ExecutionContext, NodeExecutionResult, NodeHandler, StepState, WorkflowExecutor,
                          build_default_registry, default_registry, resolve_node_inputs)
from app.workflow.nodes import ReviewNodeHandler
from app.workflow.nodes.scenes import split_scenes
from app.workflow.nodes.video import MAX_CONNECTED_PROMPT_CHARS
from app.workflow.ports import (DATA_TYPES, TEXT, bind_edges, describe_node_types, edge_problems, normalize_edges,
                                OutputPort)

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"OPENAI_API_KEY": "sk-test", "FAL_KEY": "fal-test", "TEXT_CREDITS_PER_GENERATION": "1",
        "VIDEO_CREDITS_PER_CLIP": "10"}
SCRIPT = "Cảnh 1: Rừng đêm tĩnh lặng.\n\nCảnh 2: Một con cú bay qua.\n\nCảnh 3: Bình minh lên."


def node(node_id, node_type, config=None):
    item = {"id": node_id, "type": node_type, "x": 0, "y": 0}
    if config:
        item["config"] = config
    return item


def edge(source, target, source_handle=None, target_handle=None):
    item = {"source": source, "target": target}
    if source_handle:
        item["sourceHandle"] = source_handle
    if target_handle:
        item["targetHandle"] = target_handle
    return item


def completed(node_id, node_type, output):
    return StepState(node_id, node_type, "completed", output)


class ScriptHandler(NodeHandler):
    """Stands in for a finished AI Writer with a fixed script."""

    node_type = "script_source"
    outputs = (OutputPort("script", TEXT),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("script", {"script": SCRIPT})


def with_script_source():
    registry = build_default_registry()
    registry.register(ScriptHandler())
    return registry


class EdgeMappingTest(unittest.TestCase):
    def bindings(self, graph):
        return [(b.source, b.output.name, b.target, b.input.name, b.explicit) if b else None
                for b in bind_edges(graph, default_registry)]

    def test_legacy_edges_connect_default_ports_by_type(self):
        graph = {"nodes": [node("idea", "idea"), node("writer", "ai_writer"), node("summary", "summarize"),
                           node("hook", "hook"), node("assets", "assets"), node("video", "video")],
                 "edges": [edge("idea", "writer"), edge("writer", "summary"), edge("writer", "hook"),
                           edge("idea", "hook"), edge("assets", "video")]}
        self.assertEqual(self.bindings(graph), [
            ("idea", "topic", "writer", "prompt", False),
            ("writer", "script", "summary", "text", False),
            ("writer", "script", "hook", "source", False),
            ("idea", "topic", "hook", "topic", False),
            None,  # studio videos cannot feed a video prompt; the edge only orders the run
        ])

    def test_explicit_handles_win_and_are_kept(self):
        graph = {"nodes": [node("idea", "idea"), node("writer", "ai_writer"), node("hook", "hook"),
                           node("summary", "summarize")],
                 "edges": [edge("idea", "writer", "title", "prompt"), edge("hook", "summary", "text", "text")]}
        self.assertEqual(self.bindings(graph), [("idea", "title", "writer", "prompt", True),
                                                ("hook", "text", "summary", "text", True)])
        states = {"idea": completed("idea", "idea", {"title": "Tên", "topic": "Chủ đề"}),
                  "hook": completed("hook", "hook", {"hook": "Một", "text": "Một\nHai", "options": ["Một", "Hai"]})}
        self.assertEqual(resolve_node_inputs(graph["nodes"][1], graph, states, registry=default_registry).get("prompt"),
                         "Tên")
        self.assertEqual(resolve_node_inputs(graph["nodes"][3], graph, states, registry=default_registry).get("text"),
                         "Một\nHai")

    def test_invalid_mappings_are_rejected_when_editing(self):
        graph = {"nodes": [node("idea", "idea"), node("video", "video"), node("review", "review"),
                           node("publish", "publish"), node("render", "render")],
                 "edges": [edge("idea", "video", "summary", "prompt"), edge("idea", "video", "topic", "voiceover"),
                           edge("idea", "video", "topic", "scenes"), edge("video", "publish", None, "video"),
                           edge("render", "publish", "rendered_video", "video")]}
        problems = edge_problems(graph, default_registry)
        self.assertEqual(len(problems), 4, problems)
        self.assertIn("has no output 'summary'", problems[0])
        self.assertIn("has no input 'voiceover'", problems[1])
        self.assertIn("accepts scenes, not brief", problems[2])
        self.assertIn("accepts only one connection", problems[3])
        self.assertEqual(edge_problems({"nodes": graph["nodes"], "edges": [edge("idea", "video")]}, default_registry), [])

    def test_bad_handles_in_old_snapshots_fall_back_instead_of_failing(self):
        graph = {"nodes": [node("idea", "idea"), node("summary", "summarize")],
                 "edges": [edge("idea", "summary", "renamed_port", "gone")]}
        self.assertEqual(self.bindings(graph), [("idea", "topic", "summary", "text", False)])
        state = {"idea": completed("idea", "idea", {"title": "", "topic": "Rừng"})}
        self.assertEqual(resolve_node_inputs(graph["nodes"][1], graph, state, registry=default_registry).get("text"),
                         "Rừng")

    def test_normalize_names_ports_and_keeps_only_real_handles(self):
        graph = {"nodes": [node("idea", "idea"), node("writer", "ai_writer"), node("assets", "assets"),
                           node("video", "video")],
                 "edges": [edge("idea", "writer"), edge("assets", "video", "video_assets", "missing")]}
        edges = normalize_edges(graph, default_registry)["edges"]
        self.assertEqual(edges[0], {"source": "idea", "target": "writer", "sourceHandle": "topic",
                                    "targetHandle": "prompt"})
        self.assertEqual(edges[1], {"source": "assets", "target": "video", "sourceHandle": "video_assets",
                                    "targetHandle": None})
        self.assertEqual(graph["edges"][0], edge("idea", "writer"))

    def test_catalog_is_consistent(self):
        catalog = describe_node_types(default_registry)
        self.assertEqual(set(catalog), default_registry.node_types)
        for node_type, ports in catalog.items():
            names = [port["name"] for port in ports["inputs"]]
            self.assertEqual(len(names), len(set(names)), node_type)
            for port in ports["outputs"]:
                self.assertIn(port["type"], DATA_TYPES)
            for port in ports["inputs"]:
                self.assertTrue(set(port["accepts"]) <= set(DATA_TYPES), node_type)
            for group in ports["requires"]:
                self.assertTrue(set(group) <= set(names), node_type)
        self.assertEqual([p["name"] for p in catalog["ai_writer"]["outputs"]], ["script"])
        self.assertEqual([p["name"] for p in catalog["scenes"]["inputs"]], ["script"])
        self.assertEqual([p["name"] for p in catalog["video"]["inputs"]], ["prompt", "scenes"])


class InputResolutionTest(unittest.TestCase):
    def test_several_parents_are_combined_in_edge_order(self):
        graph = {"nodes": [node("a", "ai_writer"), node("b", "rewrite"), node("sum", "summarize")],
                 "edges": [edge("a", "sum"), edge("b", "sum")]}
        states = {"a": completed("a", "ai_writer", {"script": "Kịch bản A"}),
                  "b": completed("b", "rewrite", {"text": "Bản viết lại B"})}
        inputs = resolve_node_inputs(graph["nodes"][2], graph, states, registry=default_registry)
        self.assertEqual(inputs.get("text"), "Kịch bản A\n\nBản viết lại B")
        self.assertEqual([(s.node_id, s.output) for s in inputs.sources], [("a", "script"), ("b", "text")])

        scenes = {"nodes": [node("s1", "scenes"), node("s2", "scenes"), node("video", "video")],
                  "edges": [edge("s1", "video", "scenes", "scenes"), edge("s2", "video", "scenes", "scenes")]}
        states = {"s1": completed("s1", "scenes", {"scenes": [{"index": 1, "text": "A"}]}),
                  "s2": completed("s2", "scenes", {"scenes": [{"index": 1, "text": "B"}]})}
        value = resolve_node_inputs(scenes["nodes"][2], scenes, states, registry=default_registry).get("scenes")
        self.assertEqual([scene["text"] for scene in value], ["A", "B"])

    def test_connected_input_beats_config_which_beats_context(self):
        writer = node("writer", "ai_writer", {"prompt": "Từ cấu hình"})
        connected = {"nodes": [node("idea", "idea"), writer], "edges": [edge("idea", "writer")]}
        states = {"idea": completed("idea", "idea", {"title": "T", "topic": "Từ ý tưởng"})}
        inputs = resolve_node_inputs(writer, connected, states, registry=default_registry)
        self.assertEqual((inputs.get("prompt"), inputs.sources[0].origin), ("Từ ý tưởng", "edge"))
        alone = {"nodes": [writer], "edges": []}
        inputs = resolve_node_inputs(writer, alone, {}, registry=default_registry)
        self.assertEqual((inputs.get("prompt"), inputs.sources[0].origin), ("Từ cấu hình", "config"))

    def test_older_outputs_and_malformed_values(self):
        graph = {"nodes": [node("a", "ai_writer"), node("sum", "summarize"), node("s", "scenes"), node("v", "video")],
                 "edges": [edge("a", "sum"), edge("s", "v", "scenes", "scenes")]}
        # Phase 2 stored the writer's text under "text".
        states = {"a": completed("a", "ai_writer", {"text": "Bản cũ"}),
                  "s": completed("s", "scenes", {"scenes": "not a list"})}
        self.assertEqual(resolve_node_inputs(graph["nodes"][1], graph, states, registry=default_registry).get("text"),
                         "Bản cũ")
        self.assertFalse(resolve_node_inputs(graph["nodes"][3], graph, states, registry=default_registry).has("scenes"))

    def test_an_unfinished_parent_passes_nothing(self):
        graph = {"nodes": [node("a", "ai_writer"), node("sum", "summarize")], "edges": [edge("a", "sum")]}
        inputs = resolve_node_inputs(graph["nodes"][1], graph, {"a": StepState("a", "ai_writer", "queued", None)},
                                     registry=default_registry)
        self.assertFalse(inputs.ready)
        self.assertFalse(inputs.has("text"))


class SceneSplitTest(unittest.TestCase):
    def test_paragraphs_become_scenes_without_labels(self):
        scenes = split_scenes(SCRIPT)
        self.assertEqual([s["text"] for s in scenes], ["Rừng đêm tĩnh lặng.", "Một con cú bay qua.", "Bình minh lên."])
        self.assertEqual([s["index"] for s in scenes], [1, 2, 3])
        self.assertEqual(scenes[0]["visual_prompt"], "Rừng đêm tĩnh lặng.")
        self.assertTrue(all(s["duration"] >= 1 for s in scenes))

    def test_one_long_paragraph_is_split_by_sentences(self):
        # 16 words per sentence; about 40 words make a scene, capped by max_scenes.
        sentence = "Câu số {} kể về khu rừng vào ban đêm với nhiều chi tiết thú vị."
        text = " ".join(sentence.format(i) for i in range(12))
        scenes = split_scenes(text, max_scenes=4)
        self.assertEqual(len(scenes), 4)
        self.assertEqual(" ".join(s["text"] for s in scenes), text)
        # 80 words at a 16-second target (40 words) make 2 scenes; the 6-second default makes 5.
        self.assertEqual(len(split_scenes(" ".join(sentence.format(i) for i in range(5)), scene_seconds=16)), 2)
        self.assertEqual(len(split_scenes(" ".join(sentence.format(i) for i in range(5)))), 5)

    def test_too_many_paragraphs_are_merged(self):
        text = "\n\n".join(f"Đoạn {i}." for i in range(10))
        self.assertEqual(len(split_scenes(text, max_scenes=4)), 4)


@patch.dict(os.environ, KEYS)
class PortExecutionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'ports.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="ports@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Ports", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Rừng đêm"))
            db.add(Project(id="empty", workspace_id="space-1", title="", topic=""))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=100))
            db.add(WorkspaceSetting(workspace_id="space-1", key="video_orientation", value='"vertical"'))
            db.add(AITool(id="text-1", workspace_id="space-1", task="script", provider="openai", model="gpt-4.1-mini"))
            db.add(AITool(id="video-1", workspace_id="space-1", task="video", provider="fal", model="fal-ai/veo3.1/fast"))

    def start(self, graph, *, project_id="project-1", registry=None):
        with self.Session.begin() as db:
            run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1", project_id=project_id,
                              graph_snapshot=json.dumps(graph), status="running", created_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"),
                                       project=db.get(Project, project_id), run=run, graph=graph)
            progress = WorkflowExecutor(registry).start_run(context)
            return run.id, {step.node_id: step for step in progress.steps}

    def payload(self, step):
        with self.Session() as db:
            return db.scalar(select(WorkflowJob).where(WorkflowJob.step_id == step.id)).payload

    def payloads(self, step):
        with self.Session() as db:
            jobs = db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id))
            return sorted((job.payload for job in jobs), key=lambda payload: payload["index"])

    def test_structured_scenes_flow_from_script_to_video(self):
        graph = {"nodes": [node("writer", "script_source"), node("scenes", "scenes"), node("video", "video")],
                 "edges": [edge("writer", "scenes", "script", "script"), edge("scenes", "video", "scenes", "scenes")]}
        _, steps = self.start(graph, registry=with_script_source())
        scenes = json.loads(steps["scenes"].output)["scenes"]
        self.assertEqual([s["visual_prompt"] for s in scenes],
                         ["Rừng đêm tĩnh lặng.", "Một con cú bay qua.", "Bình minh lên."])
        self.assertEqual(steps["video"].status, "queued")
        # One clip per scene, each from its own visual prompt; scenes are never joined.
        self.assertEqual([(payload["scene_index"], payload["prompt"]) for payload in self.payloads(steps["video"])],
                         [(1, "Rừng đêm tĩnh lặng."), (2, "Một con cú bay qua."), (3, "Bình minh lên.")])

    def test_connected_script_becomes_a_bounded_video_prompt(self):
        long_script = "Một cảnh quay rất dài. " * 100

        class LongScript(ScriptHandler):
            node_type = "long_script"

            def execute(self, context, node, inputs):
                return NodeExecutionResult.completed("script", {"script": long_script})

        registry = build_default_registry()
        registry.register(LongScript())
        _, steps = self.start({"nodes": [node("writer", "long_script"), node("video", "video")],
                               "edges": [edge("writer", "video", "script", "prompt")]}, registry=registry)
        prompt = self.payload(steps["video"])["prompt"]
        self.assertLessEqual(len(prompt), MAX_CONNECTED_PROMPT_CHARS)
        self.assertTrue(prompt.startswith("Một cảnh quay rất dài."))

    def test_branches_resolve_independently(self):
        graph = {"nodes": [node("idea", "idea"), node("writer", "ai_writer"), node("title", "title"),
                           node("scenes", "scenes")],
                 "edges": [edge("idea", "writer"), edge("idea", "title"), edge("idea", "scenes", "title", "script")]}
        _, steps = self.start(graph)
        self.assertEqual({key: step.status for key, step in steps.items()},
                         {"idea": "completed", "writer": "queued", "title": "queued", "scenes": "completed"})
        self.assertIn("Brief: Rừng đêm", self.payload(steps["writer"])["prompt"])
        self.assertIn("Topic: Rừng đêm", self.payload(steps["title"])["prompt"])
        self.assertEqual(json.loads(steps["scenes"].output)["scenes"][0]["text"], "Rừng")

    def test_missing_required_input_blocks_with_a_reason(self):
        _, steps = self.start({"nodes": [node("scenes", "scenes"), node("summary", "summarize"), node("video", "video")],
                               "edges": []}, project_id="empty")
        self.assertEqual((steps["scenes"].status, steps["scenes"].detail), ("blocked", "Chưa có kịch bản để chia cảnh."))
        self.assertEqual(json.loads(steps["scenes"].output),
                         {"missing_inputs": ["script"], "error": {"code": "missing_input", "retryable": False}})
        self.assertEqual(json.loads(steps["summary"].output)["missing_inputs"], ["text"])
        self.assertEqual((steps["video"].status, steps["video"].detail),
                         ("blocked", "Dự án cần có chủ đề hoặc prompt để tạo video."))
        with self.Session() as db:
            self.assertEqual(db.get(CreditAccount, "space-1").balance, 100)

    def test_review_passes_the_approved_video_on(self):
        graph = {"nodes": [node("idea", "idea"), node("video", "video"), node("review", "review")],
                 "edges": [edge("idea", "video"), edge("video", "review")]}
        run_id, _ = self.start(graph)
        with self.Session.begin() as db:
            run = db.get(WorkflowRun, run_id)
            step = db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id,
                                                           WorkflowRunStep.node_id == "video"))
            WorkflowExecutor().finish_step(ExecutionContext.for_run(db, run), step, NodeExecutionResult.completed(
                "done", {"asset_id": "a1", "video_assets": [{"id": "a1", "content_type": "video/mp4"}]},
                asset_ids=("a1",)))
        with self.Session.begin() as db:
            review = db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id,
                                                             WorkflowRunStep.node_id == "review"))
            self.assertEqual(review.status, "awaiting_review")
            ReviewNodeHandler.approve(review, reviewer_id="user-1", now=datetime.now(timezone.utc))
            output = json.loads(review.output)
        self.assertEqual(output["approved_by"], "user-1")
        port = default_registry.resolve("review").outputs[0]
        self.assertEqual(port.read(output), [{"id": "a1"}])


class PortApiTest(unittest.TestCase):
    """Port catalog, handle validation and a typed run over HTTP."""

    def test_ports_over_http(self):
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
os.environ["FAL_KEY"] = "fal-test"
os.environ["VIDEO_CREDITS_PER_CLIP"] = "10"
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app.models import Workflow, WorkflowJob, WorkflowRun
from app import usage
from sqlalchemy import select
client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.get("/api/workflow-node-types").status_code == 401
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
catalog = client.get("/api/workflow-node-types").json()
assert "scenes" in catalog["data_types"]
assert [p["name"] for p in catalog["node_types"]["scenes"]["outputs"]] == ["scenes"]
assert catalog["node_types"]["video"]["inputs"][1] == {"name": "scenes", "accepts": ["scenes"], "multiple": True}
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Rừng đêm.\n\nCon cú bay."}).json()["id"]
created = client.post("/api/workflows", json={"name":"Typed"}).json()
workflow = created["id"]
# The default graph comes back with its ports named.
assert [(e["sourceHandle"], e["targetHandle"]) for e in created["graph"]["edges"]] == [("topic", "prompt"), ("video_assets", "media")]
graph = {"nodes": [{"id":"idea","type":"idea","x":0,"y":0}, {"id":"scenes","type":"scenes","x":1,"y":0},
                   {"id":"video","type":"video","x":2,"y":0}],
         "edges": [{"source":"idea","target":"scenes","sourceHandle":"topic","targetHandle":"script"},
                   {"source":"scenes","target":"video","source_handle":"scenes","target_handle":"scenes"}]}
saved = client.put(f"/api/workflows/{workflow}", json=graph)
assert saved.status_code == 200, saved.text
assert saved.json()["graph"]["edges"][1] == {"source":"scenes","target":"video","sourceHandle":"scenes","targetHandle":"scenes"}
bad = {**graph, "edges": [{"source":"idea","target":"video","sourceHandle":"topic","targetHandle":"scenes"}]}
response = client.put(f"/api/workflows/{workflow}", json=bad)
assert response.status_code == 422 and "accepts scenes, not brief" in response.json()["detail"], response.text
unknown = {**graph, "edges": [{"source":"idea","target":"video","sourceHandle":"nope"}]}
assert client.put(f"/api/workflows/{workflow}", json=unknown).status_code == 422
twice = {**graph, "edges": [graph["edges"][0], graph["edges"][0]]}
assert client.put(f"/api/workflows/{workflow}", json=twice).status_code == 422
# Two edges between the same nodes through different ports are allowed.
both = {"nodes": [graph["nodes"][0], {"id":"writer","type":"ai_writer","x":1,"y":0}],
        "edges": [{"source":"idea","target":"writer","sourceHandle":"topic","targetHandle":"prompt"},
                  {"source":"idea","target":"writer","sourceHandle":"title","targetHandle":"prompt"}]}
assert client.put(f"/api/workflows/{workflow}", json=both).status_code == 200
# A legacy definition saved before ports reads back with named ports.
with Session.begin() as db:
    db.add(Workflow(id="legacy", workspace_id=workspace, name="Legacy", definition='["idea","scenes","video"]'))
legacy = next(w for w in client.get("/api/dashboard").json()["workflows"] if w["id"] == "legacy")
assert [(e["sourceHandle"], e["targetHandle"]) for e in legacy["graph"]["edges"]] == [("topic","script"), ("scenes","scenes")]
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
assert client.post("/api/ai-tools", json={"task":"video","provider":"fal","model":"fal-ai/veo3.1/fast"}).status_code == 201
with Session.begin() as db: usage.post_credit(db, workspace, 20, "test", "fund")
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
assert run.status_code == 201, run.text
run = run.json()
assert [s["status"] for s in run["steps"]] == ["completed", "completed", "queued"], run
assert [s["text"] for s in run["steps"][1]["output"]["scenes"]] == ["Rừng đêm.", "Con cú bay."]
with Session() as db:
    prompts = sorted((job.payload["scene_index"], job.payload["prompt"])
                     for job in db.scalars(select(WorkflowJob).where(WorkflowJob.run_id == run["id"])))
    assert prompts == [(1, "Rừng đêm."), (2, "Con cú bay.")], prompts
    snapshot = json.loads(db.get(WorkflowRun, run["id"]).graph_snapshot)
    assert snapshot["edges"][0]["targetHandle"] == "script"
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
