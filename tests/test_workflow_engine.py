"""Node registry, input resolution and the workflow executor, on an isolated SQLite database."""
from contextlib import nullcontext
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

from app.models import (AITool, Base, CreditAccount, CreditLedger, Project, User, Workflow, WorkflowJob,
                        WorkflowRun, WorkflowRunStep, Workspace, WorkspaceSetting)
from app.workflow import (ExecutionContext, NodeError, NodeExecutionResult, NodeHandler, NodeInputs, NodeRegistry,
                          RunOptions, RunRequestError, StepState, WorkflowExecutor, build_default_registry,
                          default_registry, derive_run_status, parse_graph, resolve_inputs)
from app.workflow.executor import HANDLER_FAILED_DETAIL
from app.workflow.nodes import (AssetsNodeHandler, IdeaNodeHandler, PendingAITaskHandler, PendingServiceHandler,
                                ReviewNodeHandler, TextNodeHandler, UnsupportedNodeHandler, VideoNodeHandler)
from app.workflow.nodes.pending import UNSUPPORTED_DETAIL
from app.workflow.results import JobRequest

ROOT = Path(__file__).resolve().parents[1]
TEXT_TYPES = {"ai_writer", "summarize", "rewrite", "translate", "hook", "title", "cta"}
KNOWN_TYPES = {"idea", "script", "scenes", "image", "video", "assets", "voice", "music", "subtitle", "render",
               "review", "publish"} | TEXT_TYPES
VIDEO_ENV = {"FAL_KEY": "test-key", "VIDEO_CREDITS_PER_CLIP": "10"}


def chain(*types):
    """A left-to-right graph whose node IDs are the types."""
    nodes = [{"id": kind, "type": kind, "x": index * 100, "y": 0} for index, kind in enumerate(types)]
    edges = [{"source": a, "target": b} for a, b in zip(types, types[1:])]
    return {"nodes": nodes, "edges": edges}


class SourceHandler(NodeHandler):
    node_type = "source"

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("source", {"text": "hello"})


class EchoHandler(NodeHandler):
    """Copies what it resolved into its output so tests can inspect it."""

    node_type = "echo"

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("echo", {"text": inputs.value("text"), "config": dict(inputs.config),
                                                      "topics": [o["topic"] for o in inputs.outputs("idea")]})


class BoomHandler(NodeHandler):
    node_type = "boom"

    def execute(self, context, node, inputs):
        raise RuntimeError("handler bug")


class RefuseHandler(NodeHandler):
    node_type = "refuse"

    def execute(self, context, node, inputs):
        return NodeExecutionResult.failed(NodeError("provider_rejected", "Provider từ chối."))


def custom_registry():
    registry = build_default_registry()
    for handler in (SourceHandler(), EchoHandler(), BoomHandler(), RefuseHandler()):
        registry.register(handler)
    return registry


class RegistryTest(unittest.TestCase):
    def test_default_registry_maps_each_known_type_to_one_handler(self):
        self.assertEqual(default_registry.node_types, KNOWN_TYPES)
        expected = {"idea": IdeaNodeHandler, "assets": AssetsNodeHandler, "video": VideoNodeHandler,
                    "review": ReviewNodeHandler}
        for node_type, handler_type in expected.items():
            self.assertIsInstance(default_registry.resolve(node_type), handler_type)
        for node_type in ("script", "image", "voice", "music"):
            self.assertIsInstance(default_registry.resolve(node_type), PendingAITaskHandler)
        for node_type in ("scenes", "subtitle", "render", "publish"):
            self.assertIsInstance(default_registry.resolve(node_type), PendingServiceHandler)
        for node_type in TEXT_TYPES:
            self.assertIsInstance(default_registry.resolve(node_type), TextNodeHandler)
        for node_type in KNOWN_TYPES:
            self.assertEqual(default_registry.resolve(node_type).node_type, node_type)

    def test_unknown_type_resolves_to_unsupported_fallback(self):
        self.assertNotIn("hologram", default_registry)
        self.assertIsInstance(default_registry.resolve("hologram"), UnsupportedNodeHandler)

    def test_a_type_has_exactly_one_handler(self):
        registry = NodeRegistry()
        registry.register(SourceHandler())
        with self.assertRaises(ValueError):
            registry.register(SourceHandler())
        with self.assertRaises(ValueError):
            registry.register(NodeHandler())


class ResultAndInputTest(unittest.TestCase):
    def test_result_invariants_and_stored_output(self):
        with self.assertRaises(ValueError):
            NodeExecutionResult("pending")
        with self.assertRaises(ValueError):
            NodeExecutionResult("queued", "no job")
        with self.assertRaises(ValueError):
            NodeExecutionResult.completed("done", job=JobRequest("video", {}))
        with self.assertRaises(ValueError):
            NodeExecutionResult("failed", "no error")
        result = NodeExecutionResult.completed("done", {"a": 1}, asset_ids=["asset-1"],
                                               error=NodeError("partial", "x", retryable=True))
        self.assertEqual(result.stored_output(),
                         {"a": 1, "asset_ids": ["asset-1"], "error": {"code": "partial", "retryable": True}})
        self.assertIsNone(NodeExecutionResult.blocked("waiting").stored_output())
        self.assertEqual(NodeExecutionResult.failed(NodeError("x", "Lỗi.")).detail, "Lỗi.")

    def test_run_status_follows_step_statuses(self):
        cases = [(["completed", "queued", "skipped"], "running"),
                 (["completed", "completed", "awaiting_review"], "awaiting_review"),
                 (["completed", "failed", "skipped"], "failed"),
                 (["completed", "needs_attention", "skipped"], "needs_attention"),
                 (["completed", "completed"], "completed"),
                 (["completed", "blocked", "skipped"], "blocked")]
        for statuses, expected in cases:
            self.assertEqual(derive_run_status(statuses), expected, statuses)

    def test_inputs_resolve_config_and_parent_outputs(self):
        states = {"idea": StepState("idea", "idea", "completed", {"title": "A", "topic": "T"}),
                  "video": StepState("video", "video", "completed", {"asset_id": "asset-1"}),
                  "edit": StepState("edit", "render", "completed", {"asset_ids": ["asset-2", "asset-3"]})}
        node = {"id": "child", "type": "echo", "config": {"tone": "calm"}}
        inputs = resolve_inputs(node, ["idea", "video", "edit"], states)
        self.assertTrue(inputs.ready)
        self.assertEqual(inputs.config, {"tone": "calm"})
        self.assertEqual(inputs.value("topic"), "T")
        self.assertEqual(inputs.outputs("video"), [{"asset_id": "asset-1"}])
        self.assertEqual(inputs.asset_ids, ("asset-1", "asset-2", "asset-3"))
        self.assertEqual(resolve_inputs({"id": "x", "type": "echo", "config": "bad"}, [], states).config, {})

    def test_inputs_are_not_ready_until_every_parent_completed(self):
        states = {"idea": StepState("idea", "idea", "completed", {"topic": "T"}),
                  "video": StepState("video", "video", "queued", {"prompt": "T"})}
        inputs = resolve_inputs({"id": "review", "type": "review"}, ["idea", "video"], states)
        self.assertFalse(inputs.ready)
        self.assertEqual(inputs.outputs(), [{"topic": "T"}])
        self.assertFalse(resolve_inputs({"id": "x", "type": "echo"}, ["missing"], states).ready)
        self.assertTrue(NodeInputs().ready)

    def test_legacy_list_definition_reads_as_a_chain(self):
        graph = parse_graph('["idea","script","review"]')
        self.assertEqual([(n["id"], n["type"]) for n in graph["nodes"]],
                         [("n0", "idea"), ("n1", "script"), ("n2", "review")])
        self.assertEqual(graph["edges"], [{"source": "n0", "target": "n1"}, {"source": "n1", "target": "n2"}])


class DatabaseCase(unittest.TestCase):
    """A workspace with one project and helpers that drive runs the way the API and worker do."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'engine.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="engine@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Engine", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="A", topic="Rừng đêm"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=0))
            db.add(WorkspaceSetting(workspace_id="space-1", key="video_orientation", value='"vertical"'))

    def fund(self, credits):
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance += credits

    def add_video_tool(self):
        with self.Session.begin() as db:
            db.add(AITool(id="tool-1", workspace_id="space-1", task="video", provider="fal",
                          model="fal-ai/veo3.1/fast", is_enabled=True))

    def start(self, graph, *, registry=None, options=None, snapshot=None):
        """Start a run like the API does and return (run, {node_id: step})."""
        with self.Session.begin() as db:
            run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1",
                              project_id="project-1", graph_snapshot=snapshot or json.dumps(graph),
                              status="running", created_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"),
                                       project=db.get(Project, "project-1"), run=run,
                                       graph=graph, options=options)
            progress = WorkflowExecutor(registry).start_run(context)
            return run, {step.node_id: step for step in progress.steps}

    def steps(self, run_id):
        with self.Session() as db:
            return {step.node_id: step for step in db.scalars(
                select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id).order_by(WorkflowRunStep.position))}

    def finish_video(self, run_id, asset_id="asset-1"):
        """What the video worker does after storing the MP4."""
        with self.Session.begin() as db:
            run = db.get(WorkflowRun, run_id)
            step = db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id,
                                                           WorkflowRunStep.node_type == "video"))
            WorkflowExecutor().finish_step(ExecutionContext.for_run(db, run), step, NodeExecutionResult.completed(
                "Đã lưu video vào kho media riêng.", {"asset_id": asset_id}, asset_ids=(asset_id,)))
            return run

    def approve(self, run_id):
        with self.Session.begin() as db:
            run = db.get(WorkflowRun, run_id)
            now = datetime.now(timezone.utc)
            for step in db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id,
                                                                  WorkflowRunStep.status == "awaiting_review")):
                ReviewNodeHandler.approve(step, reviewer_id="user-1", now=now)
            WorkflowExecutor().advance_run(ExecutionContext.for_run(db, run, now=now))
            return run


class ExecutorTest(DatabaseCase):
    def test_simple_local_workflow_completes(self):
        run, steps = self.start(chain("idea", "assets"))
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual([(s.node_id, s.status, s.position) for s in steps.values()],
                         [("idea", "completed", 0), ("assets", "completed", 1)])
        self.assertEqual(json.loads(steps["idea"].output), {"title": "A", "topic": "Rừng đêm"})
        self.assertEqual(json.loads(steps["assets"].output), {"assets": []})
        self.assertTrue(all(step.finished_at for step in steps.values()))

    def test_unsupported_node_blocks_the_run_without_crashing(self):
        graph = chain("idea", "hologram", "review")
        run, steps = self.start(graph)
        self.assertEqual(run.status, "blocked")
        self.assertEqual((steps["hologram"].status, steps["hologram"].detail), ("blocked", UNSUPPORTED_DETAIL))
        self.assertEqual(json.loads(steps["hologram"].output)["error"]["code"], "unsupported_node_type")
        self.assertEqual(steps["review"].status, "skipped")
        self.assertEqual(steps["idea"].status, "completed")

    def test_pending_types_keep_their_existing_messages(self):
        run, steps = self.start({"nodes": [{"id": "script", "type": "script", "x": 0, "y": 0},
                                           {"id": "publish", "type": "publish", "x": 0, "y": 0}], "edges": []})
        self.assertEqual(run.status, "blocked")
        self.assertEqual(steps["script"].detail, "Chưa chọn công cụ AI cho tác vụ này.")
        self.assertEqual(steps["publish"].detail, "Bước này chưa có bộ thực thi.")
        with self.Session.begin() as db:
            db.add(AITool(id="tool-2", workspace_id="space-1", task="script", provider="openai", model="x"))
        _, steps = self.start(chain("script"))
        self.assertEqual(steps["script"].detail, "Đã chọn model, nhưng chưa kết nối API provider.")

    def test_children_receive_parent_outputs_and_their_own_config(self):
        graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0},
                           {"id": "source", "type": "source", "x": 0, "y": 0},
                           {"id": "echo", "type": "echo", "x": 0, "y": 0, "config": {"mode": "loud"}}],
                 "edges": [{"source": "idea", "target": "echo"}, {"source": "source", "target": "echo"}]}
        run, steps = self.start(graph, registry=custom_registry())
        self.assertEqual(run.status, "completed")
        self.assertEqual(json.loads(steps["echo"].output),
                         {"text": "hello", "config": {"mode": "loud"}, "topics": ["Rừng đêm"]})

    def test_failed_parent_skips_dependents_and_keeps_completed_outputs(self):
        for failing in ("boom", "refuse"):
            with self.subTest(failing=failing):
                graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0},
                                   {"id": "bad", "type": failing, "x": 0, "y": 0},
                                   {"id": "echo", "type": "echo", "x": 0, "y": 0},
                                   {"id": "assets", "type": "assets", "x": 0, "y": 0}],
                         "edges": [{"source": "idea", "target": "bad"}, {"source": "bad", "target": "echo"},
                                   {"source": "idea", "target": "assets"}]}
                with self.assertLogs("app.workflow.executor", "ERROR") if failing == "boom" else nullcontext():
                    run, steps = self.start(graph, registry=custom_registry())
                self.assertEqual(run.status, "failed")
                self.assertEqual(steps["bad"].status, "failed")
                self.assertIsNotNone(steps["bad"].finished_at)
                expected = ("handler_error", HANDLER_FAILED_DETAIL) if failing == "boom" else ("provider_rejected", "Provider từ chối.")
                self.assertEqual((json.loads(steps["bad"].output)["error"]["code"], steps["bad"].detail), expected)
                self.assertEqual(steps["echo"].status, "skipped")
                self.assertIsNone(steps["echo"].output)
                self.assertEqual(json.loads(steps["idea"].output)["topic"], "Rừng đêm")
                self.assertEqual(steps["assets"].status, "completed")
                with self.Session.begin() as db:
                    progress = WorkflowExecutor(custom_registry()).advance_run(
                        ExecutionContext.for_run(db, db.get(WorkflowRun, run.id)))
                    self.assertEqual(progress.results, {})
                self.assertEqual(self.steps(run.id)["echo"].status, "skipped")

    @patch.dict(os.environ, VIDEO_ENV)
    def test_idea_video_review_lifecycle(self):
        self.add_video_tool()
        self.fund(10)
        run, steps = self.start(chain("idea", "video", "review"))
        self.assertEqual(run.status, "running")
        self.assertIsNone(run.finished_at)
        self.assertEqual([s.status for s in steps.values()], ["completed", "queued", "skipped"])
        self.assertEqual(steps["video"].detail, "Đã xếp hàng tạo video.")
        self.assertEqual(json.loads(steps["video"].output),
                         {"prompt": "Rừng đêm", "provider": "fal", "model": "fal-ai/veo3.1/fast"})
        with self.Session() as db:
            job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run.id))
            self.assertEqual(job.logical_key, f"video:{run.id}:{steps['video'].id}")
            self.assertEqual((job.payload["prompt"], job.payload["aspect_ratio"], job.payload["credits"]),
                             ("Rừng đêm", "9:16", 10))
            self.assertEqual(db.get(CreditAccount, "space-1").balance, 0)
            self.assertEqual(db.scalar(select(CreditLedger.reference)), f"reserve:{run.id}")

        run = self.finish_video(run.id)
        self.assertEqual((run.status, run.finished_at), ("awaiting_review", None))
        steps = self.steps(run.id)
        self.assertEqual(json.loads(steps["video"].output), {"asset_id": "asset-1", "asset_ids": ["asset-1"]})
        self.assertEqual((steps["review"].status, steps["review"].detail),
                         ("awaiting_review", "Video đã tạo; cần người dùng duyệt."))

        run = self.approve(run.id)
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(json.loads(self.steps(run.id)["review"].output)["approved_by"], "user-1")

    @patch.dict(os.environ, VIDEO_ENV)
    def test_approval_lets_steps_after_review_run(self):
        self.add_video_tool()
        self.fund(10)
        run, _ = self.start(chain("idea", "video", "review", "publish"))
        self.finish_video(run.id)
        self.assertEqual(self.steps(run.id)["publish"].status, "skipped")
        run = self.approve(run.id)
        steps = self.steps(run.id)
        self.assertEqual(run.status, "blocked")
        self.assertEqual((steps["publish"].status, steps["publish"].detail),
                         ("blocked", "Bước này chưa có bộ thực thi."))
        self.assertEqual(steps["review"].status, "completed")

    @patch.dict(os.environ, VIDEO_ENV)
    def test_video_request_errors_reject_the_run(self):
        self.add_video_tool()
        with self.assertRaises(RunRequestError) as caught:
            self.start(chain("idea", "video"))
        self.assertEqual((caught.exception.status_code, caught.exception.detail),
                         (402, "Not enough credits for this video"))
        self.fund(10)
        with self.assertRaises(RunRequestError) as caught:
            self.start(chain("idea", "video"), options=RunOptions(tool_id="missing"))
        self.assertEqual(caught.exception.status_code, 400)
        with self.Session() as db:
            self.assertEqual(db.scalars(select(WorkflowRun)).all(), [])
            self.assertEqual(db.get(CreditAccount, "space-1").balance, 10)

    @patch.dict(os.environ, VIDEO_ENV)
    def test_video_blocks_with_a_reason_instead_of_queueing(self):
        self.add_video_tool()
        self.fund(10)
        _, steps = self.start(chain("video", "review"), options=RunOptions(prompt_override="  "))
        self.assertEqual((steps["video"].status, steps["video"].detail),
                         ("blocked", "Dự án cần có chủ đề hoặc prompt để tạo video."))
        two_videos = {"nodes": [{"id": "a", "type": "video", "x": 0, "y": 0},
                                {"id": "b", "type": "video", "x": 0, "y": 0}], "edges": []}
        run, steps = self.start(two_videos)
        self.assertEqual({s.status for s in steps.values()}, {"blocked"})
        self.assertEqual(run.status, "blocked")
        with self.Session() as db:
            self.assertEqual(db.get(CreditAccount, "space-1").balance, 10)

    @patch.dict(os.environ, VIDEO_ENV)
    def test_retry_repeats_the_frozen_video_request(self):
        self.add_video_tool()
        self.fund(10)
        frozen = {"kind": "video.generate", "provider": "fal", "tool_id": "tool-1", "prompt": "Mưa",
                  "model_id": "fal-ai/veo3.1/fast", "aspect_ratio": "16:9", "duration": "8s",
                  "resolution": "720p", "generate_audio": True, "credits": 7}
        run, _ = self.start(chain("idea", "video"), options=RunOptions(frozen_video=frozen))
        with self.Session() as db:
            job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run.id))
            self.assertEqual({key: job.payload[key] for key in frozen}, frozen)
            self.assertEqual(db.get(CreditAccount, "space-1").balance, 3)

    @patch.dict(os.environ, VIDEO_ENV)
    def test_readiness_comes_from_each_handler(self):
        graph = {"nodes": [{"id": "idea", "type": "idea", "x": 0, "y": 0},
                           {"id": "video", "type": "video", "x": 0, "y": 0},
                           {"id": "script", "type": "script", "x": 0, "y": 0},
                           {"id": "render", "type": "render", "x": 0, "y": 0},
                           {"id": "hologram", "type": "hologram", "x": 0, "y": 0}], "edges": []}
        with self.Session() as db:
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            checks = {node["id"]: check for node, check in WorkflowExecutor().readiness(context)}
        self.assertEqual({key: check.status for key, check in checks.items()},
                         {"idea": "configured", "video": "missing_tool", "script": "missing_tool",
                          "render": "needs_connection", "hologram": "unsupported_node"})
        self.assertEqual(sum(check.credits for check in checks.values()), 10)


class SnapshotCompatibilityTest(DatabaseCase):
    """Runs and definitions saved before the executor existed still execute."""

    def test_legacy_list_definition_runs(self):
        definition = '["idea","script","scenes","assets","voice","render","review","publish"]'
        run, steps = self.start(parse_graph(definition), snapshot=definition)
        self.assertEqual(run.status, "blocked")
        self.assertEqual([(s.node_id, s.status) for s in steps.values()],
                         [("n0", "completed"), ("n1", "blocked")] + [(f"n{i}", "skipped") for i in range(2, 8)])

    def test_run_in_flight_from_before_the_executor_finishes(self):
        """A snapshot without labels or config, with steps written by the previous engine."""
        snapshot = json.dumps({"nodes": [{"id": "n0", "type": "idea", "x": 40, "y": 210},
                                         {"id": "n1", "type": "video", "x": 340, "y": 210},
                                         {"id": "n2", "type": "review", "x": 640, "y": 210}],
                               "edges": [{"source": "n0", "target": "n1"}, {"source": "n1", "target": "n2"}]})
        run_id = str(uuid4())
        with self.Session.begin() as db:
            db.add(WorkflowRun(id=run_id, workspace_id="space-1", workflow_id="workflow-1", project_id="project-1",
                               graph_snapshot=snapshot, status="running", created_at=datetime.now(timezone.utc)))
            db.flush()
            for position, (node_id, node_type, status, output) in enumerate([
                    ("n0", "idea", "completed", {"title": "A", "topic": "Rừng đêm"}),
                    ("n1", "video", "running", {"prompt": "Rừng đêm", "provider": "fal", "model": "m",
                                                "submission": {"request_id": "r1"}}),
                    ("n2", "review", "skipped", None)]):
                db.add(WorkflowRunStep(id=str(uuid4()), run_id=run_id, node_id=node_id, node_type=node_type,
                                       position=position, status=status,
                                       detail="Chờ bước phía trước hoàn thành." if status == "skipped" else "",
                                       output=json.dumps(output) if output else None))
        run = self.finish_video(run_id)
        self.assertEqual(run.status, "awaiting_review")
        self.assertEqual(self.steps(run_id)["n2"].status, "awaiting_review")
        self.assertEqual(self.approve(run_id).status, "completed")


class ApiCompatibilityTest(unittest.TestCase):
    """Legacy definitions and old run snapshots through the HTTP API."""

    def test_legacy_workflow_and_old_snapshot_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
import json
from datetime import datetime, timezone
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app.models import Workflow, WorkflowRun, WorkflowRunStep
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Chủ đề"}).json()["id"]
# Saved before the visual editor: a list of node types.
with Session.begin() as db:
    db.add(Workflow(id="legacy", workspace_id=workspace, name="Legacy", definition='["idea","script","review"]'))
graph = next(w for w in client.get("/api/dashboard").json()["workflows"] if w["id"] == "legacy")["graph"]
assert [n["id"] for n in graph["nodes"]] == ["n0", "n1", "n2"], graph
run = client.post("/api/workflows/legacy/runs", json={"project_id": project})
assert run.status_code == 201, run.text
assert [(s["node_id"], s["status"]) for s in run.json()["steps"]] == [("n0","completed"), ("n1","blocked"), ("n2","skipped")], run.json()
assert run.json()["status"] == "blocked"
readiness = client.get("/api/workflows/legacy/readiness").json()
assert [s["status"] for s in readiness["steps"]] == ["configured", "missing_tool", "configured"], readiness
assert readiness["credits_required"] == 0 and not readiness["runnable"]
# A blocked run written by the previous engine, with a snapshot that predates labels.
snapshot = json.dumps({"nodes": [{"id":"n0","type":"idea","x":0,"y":0}, {"id":"n1","type":"script","x":1,"y":0}],
                       "edges": [{"source":"n0","target":"n1"}]})
now = datetime.now(timezone.utc)
with Session.begin() as db:
    db.add(WorkflowRun(id="old-run", workspace_id=workspace, workflow_id="legacy", project_id=project,
                       graph_snapshot=snapshot, status="blocked", created_at=now, finished_at=now))
    db.flush()
    db.add(WorkflowRunStep(id="old-0", run_id="old-run", node_id="n0", node_type="idea", position=0, status="completed",
                           detail="Đã lấy ý tưởng từ dự án.", output=json.dumps({"title":"A", "topic":"Chủ đề"}), finished_at=now))
    db.add(WorkflowRunStep(id="old-1", run_id="old-run", node_id="n1", node_type="script", position=1, status="blocked",
                           detail="Chưa chọn công cụ AI cho tác vụ này.", finished_at=now))
assert [s["status"] for s in client.get("/api/workflow-runs/old-run").json()["steps"]] == ["completed", "blocked"]
retried = client.post("/api/workflow-runs/old-run/retry")
assert retried.status_code == 201, retried.text
assert retried.json()["retry_of_id"] == "old-run"
assert [(s["node_id"], s["status"]) for s in retried.json()["steps"]] == [("n0","completed"), ("n1","blocked")]
# Only registered node types can be saved.
assert client.put("/api/workflows/legacy", json={"nodes":[{"id":"x","type":"hologram","x":0,"y":0}],"edges":[]}).status_code == 422
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
