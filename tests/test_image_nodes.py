"""Image node: settings, readiness, modes and per-image jobs and credits, through the executor."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.models import (AITool, Base, CreditAccount, CreditLedger, Project, User, Workflow, WorkflowJob, WorkflowRun,
                        Workspace, WorkspaceSetting)
from app.providers.image import IMAGE_PROVIDERS, ImageModel
from app.workflow import (ExecutionContext, NodeExecutionResult, NodeHandler, RunRequestError, WorkflowExecutor,
                          build_default_registry, default_registry)
from app.workflow.config import ConfigError
from app.workflow.ports import SCENES, TEXT, OutputPort, describe_node_types

KEYS = {"RUNWAYML_API_SECRET": "runway-test", "RUNWAY_OUTPUT_HOSTS": "dnznrvs05pmza.cloudfront.net",
        "IMAGE_CREDITS_PER_GENERATION": "2"}
SCENES_OUT = [{"index": 1, "text": "Rừng đêm.", "visual_prompt": "Night forest, fireflies", "duration": 4},
              {"index": 2, "text": "Con cú bay.", "visual_prompt": "", "duration": 3},
              {"index": 3, "text": "Bình minh.", "visual_prompt": "Sunrise over the canopy", "duration": 5}]


class ScenesSource(NodeHandler):
    node_type = "scene_source"
    outputs = (OutputPort("scenes", SCENES),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("scenes", {"scenes": SCENES_OUT})


class TextSource(NodeHandler):
    node_type = "text_source"
    outputs = (OutputPort("text", TEXT),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("text", {"text": "A lighthouse in a storm"})


class Gate(NodeHandler):
    """Stays pending until a test completes it, like a step waiting on a worker."""

    node_type = "gate"
    outputs = (OutputPort("scenes", SCENES),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.waiting()


def registry():
    value = build_default_registry()
    value.register(ScenesSource())
    value.register(TextSource())
    value.register(Gate())
    return value


def node(node_id, node_type, config=None):
    item = {"id": node_id, "type": node_type, "x": 0, "y": 0}
    if config is not None:
        item["config"] = config
    return item


def edge(source, target, source_handle, target_handle):
    return {"source": source, "target": target, "sourceHandle": source_handle, "targetHandle": target_handle}


class ImageSettingsTest(unittest.TestCase):
    def test_schema(self):
        fields = {field["key"]: field for field in describe_node_types(default_registry)["image"]["config"]}
        self.assertEqual(list(fields), ["tool_id", "aspect_ratio", "count", "quality", "prompt", "seed"])
        self.assertEqual(fields["aspect_ratio"]["options"], ["auto", "1:1", "16:9", "9:16"])
        self.assertEqual((fields["count"]["minimum"], fields["count"]["maximum"], fields["count"]["default"]), (1, 4, 1))
        self.assertEqual(fields["tool_id"]["task"], "image")
        self.assertEqual(fields["tool_id"]["providers"], ["runway"])
        self.assertTrue(fields["seed"]["advanced"])
        ports = describe_node_types(default_registry)["image"]
        self.assertEqual(([p["name"] for p in ports["inputs"]], [p["name"] for p in ports["outputs"]]),
                         (["prompt", "scenes"], ["image_assets"]))

    def test_invalid_settings(self):
        handler = default_registry.resolve("image")
        handler.validate_config({"aspect_ratio": "1:1", "count": 4, "quality": "high", "seed": 0, "prompt": "x"})
        for config, code in (({"aspect_ratio": "4:3"}, "invalid_aspect_ratio"), ({"count": 5}, "invalid_count"),
                             ({"quality": "ultra"}, "invalid_quality"), ({"seed": -1}, "invalid_seed"),
                             ({"prompt": "x" * 1001}, "invalid_prompt"), ({"negative_prompt": "x"}, "unknown_setting")):
            with self.subTest(config=config), self.assertRaises(ConfigError) as caught:
                handler.validate_config(config)
            self.assertEqual(caught.exception.code, code)


@patch.dict(os.environ, KEYS)
class ImageRunTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'image.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="image@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Image", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Rừng đêm"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=100))
            db.add(WorkspaceSetting(workspace_id="space-1", key="video_orientation", value='"horizontal"'))
            db.add(AITool(id="image-1", workspace_id="space-1", task="image", provider="runway", model="gen4_image"))

    def start(self, graph):
        with self.Session.begin() as db:
            run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1", project_id="project-1",
                              graph_snapshot=json.dumps(graph), status="running", created_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), project=db.get(Project, "project-1"),
                                       run=run, graph=graph)
            progress = WorkflowExecutor(registry()).start_run(context)
            return run.id, {step.node_id: step for step in progress.steps}

    def jobs(self, step):
        with self.Session() as db:
            return list(db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id).order_by(WorkflowJob.logical_key)))

    def readiness(self, graph):
        with self.Session() as db:
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            return {n["id"]: check for n, check in WorkflowExecutor(registry()).readiness(context)}

    def balance(self):
        with self.Session() as db:
            return db.get(CreditAccount, "space-1").balance

    def test_single_prompt_mode_queues_one_job_per_image(self):
        _, steps = self.start({"nodes": [node("image", "image", {"prompt": "A red boat", "count": 2})], "edges": []})
        step = steps["image"]
        self.assertEqual(step.status, "queued")
        jobs = self.jobs(step)
        self.assertEqual([job.logical_key for job in jobs], [f"image:{step.id}:image:1", f"image:{step.id}:image:2"])
        payload = jobs[0].payload
        self.assertEqual({key: payload[key] for key in ("kind", "provider", "model", "prompt", "aspect_ratio", "quality",
                                                         "mode", "operation", "scene_index", "credits")},
                         {"kind": "image.generate", "provider": "runway", "model": "gen4_image", "prompt": "A red boat",
                          "aspect_ratio": "16:9", "quality": "standard", "mode": "prompt", "operation": "image:1",
                          "scene_index": None, "credits": 2})
        self.assertEqual((payload["reserve_reference"], payload["usage_reference"], payload["refund_reference"]),
                         (f"image-reserve:{step.id}:image:1", f"image:{step.id}:image:1",
                          f"image-refund:{step.id}:image:1"))
        with self.Session() as db:
            ledger = sorted(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason == "image_reserve")))
        self.assertEqual(ledger, [f"image-reserve:{step.id}:image:1", f"image-reserve:{step.id}:image:2"])
        self.assertEqual(self.balance(), 96)
        self.assertEqual(json.loads(step.output)["expected"], 2)

    def test_scene_mode_makes_one_image_per_scene_without_joining_prompts(self):
        graph = {"nodes": [node("scenes", "scene_source"), node("image", "image", {"count": 4, "aspect_ratio": "1:1"})],
                 "edges": [edge("scenes", "image", "scenes", "scenes")]}
        _, steps = self.start(graph)
        jobs = self.jobs(steps["image"])
        self.assertEqual([(job.payload["scene_index"], job.payload["prompt"]) for job in jobs],
                         [(1, "Night forest, fireflies"), (2, "Con cú bay."), (3, "Sunrise over the canopy")])
        self.assertTrue(all(job.payload["mode"] == "scenes" and job.payload["aspect_ratio"] == "1:1" for job in jobs))
        self.assertEqual(self.balance(), 94)

    def test_input_precedence(self):
        cases = [
            # Prompt override beats connected text and scenes.
            ({"prompt": "Override"}, True, True, ("prompt", ["Override"])),
            # Connected text beats scenes.
            ({}, True, True, ("prompt", ["A lighthouse in a storm"])),
            # Scenes beat the project topic.
            ({}, False, True, ("scenes", ["Night forest, fireflies", "Con cú bay.", "Sunrise over the canopy"])),
            # The project topic is the last resort.
            ({}, False, False, ("prompt", ["Rừng đêm"])),
        ]
        for config, with_text, with_scenes, expected in cases:
            nodes, edges = [node("image", "image", config)], []
            if with_text:
                nodes.append(node("text", "text_source"))
                edges.append(edge("text", "image", "text", "prompt"))
            if with_scenes:
                nodes.append(node("scenes", "scene_source"))
                edges.append(edge("scenes", "image", "scenes", "scenes"))
            with self.subTest(config=config, text=with_text, scenes=with_scenes):
                _, steps = self.start({"nodes": nodes, "edges": edges})
                jobs = self.jobs(steps["image"])
                self.assertEqual((jobs[0].payload["mode"], [job.payload["prompt"] for job in jobs]), expected)

    def test_readiness(self):
        graph = {"nodes": [node("image", "image", {"count": 3})], "edges": []}
        check = self.readiness(graph)["image"]
        self.assertEqual((check.status, check.credits), ("ready", 6))
        scenes = {"nodes": [node("scenes", "scenes"), node("image", "image")],
                  "edges": [edge("scenes", "image", "scenes", "scenes")]}
        check = self.readiness(scenes)["image"]
        self.assertEqual((check.status, check.code, check.credits), ("ready", "per_scene", 2))
        # Connected prompt text comes before scenes, so the estimate is per image again.
        both = {"nodes": [node("idea", "idea"), node("scenes", "scenes"), node("image", "image", {"count": 2})],
                "edges": [edge("scenes", "image", "scenes", "scenes"), edge("idea", "image", "topic", "prompt")]}
        check = self.readiness(both)["image"]
        self.assertEqual((check.status, check.code, check.credits), ("ready", None, 4))
        with patch.dict(os.environ, {"RUNWAYML_API_SECRET": ""}):
            self.assertEqual(self.readiness(graph)["image"].status, "missing_key")
        with patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": ""}):
            self.assertEqual(self.readiness(graph)["image"].status, "invalid_config")
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance = 5
        self.assertEqual(self.readiness(graph)["image"].status, "insufficient_credits")
        with self.Session.begin() as db:
            db.get(AITool, "image-1").model = "gen5_image"
        check = self.readiness(graph)["image"]
        self.assertEqual((check.status, check.field), ("unsupported_model", "tool_id"))
        with self.Session.begin() as db:
            db.get(AITool, "image-1").is_enabled = False
        self.assertEqual(self.readiness(graph)["image"].status, "missing_tool")
        check = self.readiness({"nodes": [node("image", "image", {"tool_id": "image-1"})], "edges": []})["image"]
        self.assertEqual((check.status, check.field), ("tool_unavailable", "tool_id"))

    def test_model_capabilities_decide_aspect_ratios(self):
        square_only = ImageModel("gen4_image", aspect_ratios=("1:1",), qualities=("standard",))
        with patch.dict(IMAGE_PROVIDERS["runway"].provider_type.models, {"gen4_image": square_only}):
            check = self.readiness({"nodes": [node("image", "image", {"aspect_ratio": "16:9"})], "edges": []})["image"]
            self.assertEqual((check.status, check.code, check.field), ("invalid_settings", "invalid_aspect_ratio",
                                                                       "aspect_ratio"))
            check = self.readiness({"nodes": [node("image", "image", {"quality": "high", "aspect_ratio": "1:1"})],
                                    "edges": []})["image"]
            self.assertEqual(check.code, "invalid_quality")
            _, steps = self.start({"nodes": [node("image", "image", {"aspect_ratio": "16:9"})], "edges": []})
            self.assertEqual(steps["image"].status, "blocked")
            self.assertEqual(self.balance(), 100)

    def test_insufficient_credits_reserve_nothing(self):
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance = 5
        with self.assertRaises(RunRequestError) as caught:
            self.start({"nodes": [node("image", "image", {"count": 3})], "edges": []})
        self.assertEqual(caught.exception.code, "insufficient_credits")
        # Found later, when the scenes arrive: the step blocks without holding part of its cost.
        run_id, steps = self.start({"nodes": [node("gate", "gate"), node("image", "image")],
                                    "edges": [edge("gate", "image", "scenes", "scenes")]})
        with self.Session.begin() as db:
            gate = db.get(type(steps["gate"]), steps["gate"].id)
            gate.status, gate.output = "completed", json.dumps({"scenes": SCENES_OUT})
            run = db.get(WorkflowRun, run_id)
            WorkflowExecutor(registry()).advance_run(ExecutionContext.for_run(db, run))
        with self.Session() as db:
            image = db.get(type(steps["image"]), steps["image"].id)
            self.assertEqual((image.status, json.loads(image.output)["error"]["code"]), ("blocked", "insufficient_credits"))
            self.assertIsNone(db.scalar(select(CreditLedger).where(CreditLedger.reason == "image_reserve")))
            self.assertIsNone(db.scalar(select(WorkflowJob)))
        self.assertEqual(self.balance(), 5)

    def test_missing_tool_and_input_block_without_charging(self):
        with self.Session.begin() as db:
            db.get(AITool, "image-1").is_enabled = False
        _, steps = self.start({"nodes": [node("image", "image")], "edges": []})
        self.assertEqual((steps["image"].status, json.loads(steps["image"].output)["error"]["code"]),
                         ("blocked", "missing_tool"))
        self.assertEqual(self.balance(), 100)


if __name__ == "__main__":
    unittest.main()
