"""Voice node: settings, readiness, modes and per-narration jobs and credits, through the executor."""
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
                        Workspace)
from app.workflow import (ExecutionContext, NodeExecutionResult, NodeHandler, WorkflowExecutor,
                          build_default_registry, default_registry)
from app.workflow.config import ConfigError
from app.workflow.ports import SCENES, TEXT, OutputPort, describe_node_types

KEYS = {"GEMINI_API_KEY": "gemini-test", "VOICE_CREDITS_PER_GENERATION": "1"}
MODEL = "gemini-2.5-flash-preview-tts"
SCENES_OUT = [{"index": 1, "text": "Rừng   đêm tĩnh lặng.", "visual_prompt": "Night forest, cinematic", "duration": 4},
              {"index": 2, "text": "Con cú bay qua.", "visual_prompt": "Owl", "duration": 3},
              {"index": 3, "text": "", "visual_prompt": "Only a picture", "duration": 2},
              {"index": 4, "text": "Bình minh lên.", "visual_prompt": "Sunrise", "duration": 5}]


class ScenesSource(NodeHandler):
    node_type = "scene_source"
    outputs = (OutputPort("scenes", SCENES),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("scenes", {"scenes": SCENES_OUT})


class TextSource(NodeHandler):
    node_type = "text_source"
    outputs = (OutputPort("text", TEXT),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("text", {"text": "Kịch bản đầy đủ.\n\nĐoạn hai."})


def registry():
    value = build_default_registry()
    value.register(ScenesSource())
    value.register(TextSource())
    return value


def node(node_id, node_type, config=None):
    item = {"id": node_id, "type": node_type, "x": 0, "y": 0}
    if config is not None:
        item["config"] = config
    return item


def edge(source, target, source_handle, target_handle):
    return {"source": source, "target": target, "sourceHandle": source_handle, "targetHandle": target_handle}


class VoiceSettingsTest(unittest.TestCase):
    def test_schema_keeps_the_placeholder_ports(self):
        described = describe_node_types(default_registry)["voice"]
        fields = {field["key"]: field for field in described["config"]}
        self.assertEqual(list(fields), ["tool_id", "voice", "style", "text"])
        self.assertEqual((fields["tool_id"]["task"], fields["tool_id"]["providers"]), ("voice", ["gemini"]))
        self.assertEqual(fields["voice"]["default"], "Kore")
        self.assertEqual(fields["style"]["options"], ["neutral", "calm", "cheerful", "energetic", "serious", "slow"])
        self.assertEqual(([p["name"] for p in described["inputs"]], [p["name"] for p in described["outputs"]]),
                         (["script", "scenes"], ["audio_assets"]))
        self.assertEqual(described["requires"], [["script", "scenes"]])

    def test_invalid_settings(self):
        handler = default_registry.resolve("voice")
        handler.validate_config({"voice": "Puck", "style": "calm", "text": "x"})
        for config, code in (({"voice": "Alloy"}, "invalid_voice"), ({"style": "whisper"}, "invalid_style"),
                             ({"text": "x" * 5001}, "invalid_text"), ({"speed": 1.2}, "unknown_setting")):
            with self.subTest(config=config), self.assertRaises(ConfigError) as caught:
                handler.validate_config(config)
            self.assertEqual(caught.exception.code, code)


@patch.dict(os.environ, KEYS)
class VoiceRunTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'voice.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="voice@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Voice", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Rừng đêm"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=10))
            db.add(AITool(id="voice-1", workspace_id="space-1", task="voice", provider="gemini", model=MODEL))

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
            return list(db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id)
                                   .order_by(WorkflowJob.logical_key)))

    def readiness(self, graph):
        with self.Session() as db:
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            return {n["id"]: check for n, check in WorkflowExecutor(registry()).readiness(context)}

    def balance(self):
        with self.Session() as db:
            return db.get(CreditAccount, "space-1").balance

    def scene_graph(self, config=None):
        return {"nodes": [node("scenes", "scene_source"), node("voice", "voice", config)],
                "edges": [edge("scenes", "voice", "scenes", "scenes")]}

    def test_scene_mode_reads_each_scene_text_once(self):
        _, steps = self.start(self.scene_graph({"voice": "Puck", "style": "calm"}))
        step = steps["voice"]
        self.assertEqual(step.status, "queued")
        jobs = self.jobs(step)
        # scene.text, not visual_prompt; the scene without text is skipped; whitespace is collapsed, never cut.
        self.assertEqual([(job.payload["scene_index"], job.payload["prompt"]) for job in jobs],
                         [(1, "Rừng đêm tĩnh lặng."), (2, "Con cú bay qua."), (4, "Bình minh lên.")])
        self.assertEqual([job.logical_key for job in jobs], [f"voice:{step.id}:scene:{n}" for n in (1, 2, 4)])
        payload = jobs[0].payload
        self.assertEqual({key: payload[key] for key in ("kind", "provider", "model", "voice", "style", "format", "mode",
                                                         "credits", "reserve_reference", "usage_reference",
                                                         "refund_reference")},
                         {"kind": "voice.generate", "provider": "gemini", "model": MODEL, "voice": "Puck",
                          "style": "calm", "format": "wav", "mode": "scenes", "credits": 1,
                          "reserve_reference": f"voice-reserve:{step.id}:scene:1",
                          "usage_reference": f"voice:{step.id}:scene:1",
                          "refund_reference": f"voice-refund:{step.id}:scene:1"})
        with self.Session() as db:
            ledger = sorted(db.scalars(select(CreditLedger.reference).where(CreditLedger.reason == "voice_reserve")))
        self.assertEqual(ledger, [f"voice-reserve:{step.id}:scene:{n}" for n in (1, 2, 4)])
        self.assertEqual(self.balance(), 7)
        self.assertEqual(json.loads(step.output)["expected"], 3)

    def test_input_precedence(self):
        cases = [
            # The text override beats connected text and scenes.
            ({"text": "  Lời   dẫn riêng "}, True, True, ("prompt", ["Lời dẫn riêng"])),
            # Connected text is read as one narration, even with scenes connected.
            ({}, True, True, ("prompt", ["Kịch bản đầy đủ. Đoạn hai."])),
            # Scenes: one narration per scene.
            ({}, False, True, ("scenes", ["Rừng đêm tĩnh lặng.", "Con cú bay qua.", "Bình minh lên."])),
        ]
        for config, with_text, with_scenes, expected in cases:
            nodes, edges = [node("voice", "voice", config)], []
            if with_text:
                nodes.append(node("text", "text_source"))
                edges.append(edge("text", "voice", "text", "script"))
            if with_scenes:
                nodes.append(node("scenes", "scene_source"))
                edges.append(edge("scenes", "voice", "scenes", "scenes"))
            with self.subTest(config=config, text=with_text, scenes=with_scenes):
                _, steps = self.start({"nodes": nodes, "edges": edges})
                jobs = self.jobs(steps["voice"])
                self.assertEqual((jobs[0].payload["mode"], [job.payload["prompt"] for job in jobs]), expected)
                if expected[0] == "prompt":
                    self.assertEqual(jobs[0].logical_key, f"voice:{steps['voice'].id}:single")

    def test_nothing_to_read_blocks_without_charging(self):
        # The project topic is never narrated.
        _, steps = self.start({"nodes": [node("voice", "voice")], "edges": []})
        self.assertEqual(steps["voice"].status, "blocked")
        self.assertEqual(self.balance(), 10)
        _, steps = self.start({"nodes": [node("voice", "voice", {"text": "x" * 5000})], "edges": []})
        self.assertEqual(steps["voice"].status, "queued")
        self.assertEqual(self.balance(), 9)

    def test_too_long_text_and_missing_key_block_without_charging(self):
        long_text = {"nodes": [node("text", "text_source"), node("voice", "voice")],
                     "edges": [edge("text", "voice", "text", "script")]}
        with patch("app.workflow.nodes.voice.normalize", side_effect=lambda text: "y" * 5001 if text else ""):
            _, steps = self.start(long_text)
        self.assertEqual(steps["voice"].status, "blocked")
        self.assertIn("5000", steps["voice"].detail)
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            _, steps = self.start(self.scene_graph())
        self.assertEqual((steps["voice"].status, steps["voice"].detail), ("blocked", "Server cần GEMINI_API_KEY."))
        self.assertEqual(self.balance(), 10)
        self.assertEqual(self.jobs(steps["voice"]), [])

    def test_insufficient_credits_reserve_nothing(self):
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance = 2
        with self.assertRaises(Exception) as caught:
            self.start(self.scene_graph())
        self.assertEqual(getattr(caught.exception, "code", None), "insufficient_credits")
        self.assertEqual(self.balance(), 2)

    def test_readiness(self):
        # Readiness binds edges with the default registry, so it uses the real Scene Splitter.
        scenes = {"nodes": [node("scenes", "scenes"), node("voice", "voice")],
                  "edges": [edge("scenes", "voice", "scenes", "scenes")]}
        check = self.readiness(scenes)["voice"]
        self.assertEqual((check.status, check.code, check.credits), ("ready", "per_scene", 1))
        both = {"nodes": [*scenes["nodes"], node("idea", "idea")],
                "edges": [*scenes["edges"], edge("idea", "voice", "topic", "script")]}
        self.assertIsNone(self.readiness(both)["voice"].code)
        single = {"nodes": [node("voice", "voice", {"text": "Xin chào"})], "edges": []}
        check = self.readiness(single)["voice"]
        self.assertEqual((check.status, check.code, check.credits), ("ready", None, 1))
        check = self.readiness({"nodes": [node("voice", "voice")], "edges": []})["voice"]
        self.assertEqual((check.status, check.field), ("missing_input", "script"))
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertEqual(self.readiness(single)["voice"].status, "missing_key")
        with self.Session.begin() as db:
            db.get(AITool, "voice-1").model = "gemini-9-tts"
        check = self.readiness(single)["voice"]
        self.assertEqual((check.status, check.field), ("unsupported_model", "tool_id"))
        with self.Session.begin() as db:
            db.get(AITool, "voice-1").is_enabled = False
        self.assertEqual(self.readiness(single)["voice"].status, "missing_tool")
        check = self.readiness({"nodes": [node("voice", "voice", {"tool_id": "voice-1", "text": "x"})], "edges": []})["voice"]
        self.assertEqual((check.status, check.field), ("tool_unavailable", "tool_id"))
        with self.Session.begin() as db:
            tool = db.get(AITool, "voice-1")
            tool.is_enabled, tool.model = True, MODEL
            db.get(CreditAccount, "space-1").balance = 0
        self.assertEqual(self.readiness(single)["voice"].status, "insufficient_credits")


if __name__ == "__main__":
    unittest.main()
