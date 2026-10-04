"""Subtitles: deterministic timing, wrapping and escaping, and the Subtitle node's file asset."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import subtitles
from app.models import Asset, Base, Project, SystemSetting, User, Workflow, WorkflowRun, Workspace
from app.subtitles import Cue, Segment, build_cues, plan_scenes, plan_text, to_srt, to_vtt, wrap
from app.workflow import (ExecutionContext, NodeExecutionResult, NodeHandler, WorkflowExecutor, build_default_registry,
                          default_registry)
from app.workflow.config import ConfigError
from app.workflow.ports import AUDIO_ASSETS, SCENES, TEXT, OutputPort, describe_node_types

SCENES_OUT = [{"index": 1, "text": "Rừng đêm tĩnh lặng.", "duration": 4},
              {"index": 2, "text": "", "duration": 3},
              {"index": 3, "text": "Bình minh lên trên đồi.", "duration": 5}]


class TimingTest(unittest.TestCase):
    def test_scene_durations_give_sequential_srt_timing(self):
        segments, timing = plan_scenes(SCENES_OUT)
        self.assertEqual(timing, "scenes")
        self.assertEqual([(s.scene_index, s.start, s.end) for s in segments], [(1, 0, 4000), (2, 4000, 7000), (3, 7000, 12000)])
        cues = build_cues(segments)
        # The empty scene keeps its time but has no cue.
        self.assertEqual(to_srt(cues),
                         "1\n00:00:00,000 --> 00:00:04,000\nRừng đêm tĩnh lặng.\n\n"
                         "2\n00:00:07,000 --> 00:00:12,000\nBình minh lên trên đồi.\n")
        self.assertEqual([cue.scene_index for cue in cues], [1, 3])

    def test_hours_and_milliseconds(self):
        self.assertEqual(to_srt([Cue(3_723_004, 3_725_500, "x")]), "1\n01:02:03,004 --> 01:02:05,500\nx\n")
        self.assertEqual(to_vtt([Cue(1_500, 2_250, "x")]), "WEBVTT\n\n1\n00:00:01.500 --> 00:00:02.250\nx\n")

    def test_audio_and_video_durations_replace_estimates(self):
        audio = [{"scene_index": 1, "duration": 2.5}, {"scene_index": 3, "duration": 3.25}]
        video = [{"scene_index": 1, "duration": 8}, {"scene_index": 2, "duration": 8}, {"scene_index": 3, "duration": 8}]
        segments, timing = plan_scenes(SCENES_OUT, audio, video)
        self.assertEqual(timing, "audio")
        self.assertEqual([(s.start, s.end, s.source) for s in segments],
                         [(0, 2500, "audio"), (2500, 10500, "video"), (10500, 13750, "audio")])
        segments, timing = plan_scenes(SCENES_OUT, None, video)
        self.assertEqual((timing, [s.end for s in segments]), ("video", [8000, 16000, 24000]))
        # One narration for the whole script is shared in proportion to the scene estimates (4:3:5).
        segments, timing = plan_scenes(SCENES_OUT, [{"scene_index": None, "duration": 24}])
        self.assertEqual((timing, [s.end for s in segments]), ("audio", [8000, 14000, 24000]))
        # Malformed durations are ignored.
        segments, _ = plan_scenes(SCENES_OUT, [{"scene_index": 1, "duration": "9"}, {"scene_index": True, "duration": 9}])
        self.assertEqual(segments[0].end, 4000)

    def test_plain_text_uses_narration_video_or_reading_speed(self):
        text = "Một hai ba bốn năm sáu bảy tám chín mười."  # ten words: four seconds at 2.5 words per second
        self.assertEqual([(s.end, s.source) for s in plan_text(text)[0]], [(4000, "estimate")])
        self.assertEqual(plan_text(text, [{"duration": 6.5}])[0][0].end, 6500)
        self.assertEqual(plan_text(text, None, [{"scene_index": 1, "duration": 8}, {"scene_index": 2, "duration": 8}])[1],
                         "video")
        self.assertEqual(plan_text("   "), ([], "estimate"))

    def test_long_text_is_wrapped_into_timed_cues(self):
        text = "Ánh trăng chiếu qua những tán lá rậm rạp của khu rừng già, một con cú lặng lẽ bay qua bầu trời đêm."
        lines = wrap(text, 30)
        self.assertTrue(all(len(line) <= 30 for line in lines))
        self.assertEqual(" ".join(lines), text)
        cues = build_cues([Segment(2, 1000, 11000, text, "scene")], max_chars=30, max_lines=2)
        self.assertEqual(len(cues), 2)
        self.assertEqual((cues[0].start, cues[-1].end), (1000, 11000))
        self.assertEqual(cues[0].end, cues[1].start)
        self.assertTrue(all(len(cue.text.split("\n")) <= 2 for cue in cues))
        # Japanese has no spaces: it is broken by characters.
        japanese = "静かな夜の森の上を一羽のフクロウが飛んでいきます。夜明けが近づいています。"
        self.assertEqual("".join(wrap(japanese, 12)), japanese)
        self.assertTrue(all(len(line) <= 12 for line in wrap(japanese, 12)))

    def test_escaping_and_line_breaks(self):
        segments, _ = plan_scenes([{"index": 1, "text": "Dòng một\n\nDòng hai\x07 --> <b>&</b> {\\an8}", "duration": 3}])
        cues = build_cues(segments, max_chars=80)
        # A blank line would end an SRT cue early, and "-->" would start a new timing line.
        self.assertEqual(cues[0].text, "Dòng một Dòng hai → <b>&</b> {\\an8}")
        self.assertIn("&lt;b&gt;&amp;&lt;/b&gt;", to_vtt(cues))
        self.assertNotIn("\n\n", to_srt(cues).strip())
        self.assertEqual(subtitles.burn_in_text(cues[0].text), "Dòng một Dòng hai → ‹b›&‹/b› (＼an8)")

    def test_empty_input_gives_no_cues(self):
        self.assertEqual(build_cues(plan_scenes([])[0]), [])
        self.assertEqual(build_cues(plan_scenes([{"index": 1, "text": "  "}])[0]), [])
        self.assertEqual(subtitles.cues_from([{"text": "x", "start": 2, "end": 1}, "junk", {"start": 0, "end": 1}]), [])


class ScenesSource(NodeHandler):
    node_type = "scene_source"
    outputs = (OutputPort("scenes", SCENES),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("scenes", {"scenes": SCENES_OUT})


class TextSource(NodeHandler):
    node_type = "text_source"
    outputs = (OutputPort("text", TEXT),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("text", {"text": "Một hai ba bốn năm."})


class AudioSource(NodeHandler):
    node_type = "audio_source"
    outputs = (OutputPort("audio_assets", AUDIO_ASSETS),)

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("audio", {"audio_assets": [
            {"id": "a1", "scene_index": 1, "duration": 2.0}, {"id": "a3", "scene_index": 3, "duration": 3.0}]})


class Explode(NodeHandler):
    node_type = "explode"

    def execute(self, context, node, inputs):
        raise RuntimeError("boom")


def registry():
    value = build_default_registry()
    for handler in (ScenesSource(), TextSource(), AudioSource(), Explode()):
        value.register(handler)
    return value


def node(node_id, node_type, config=None):
    item = {"id": node_id, "type": node_type, "x": 0, "y": 0}
    if config is not None:
        item["config"] = config
    return item


def edge(source, target, source_handle=None, target_handle=None):
    item = {"source": source, "target": target}
    if source_handle:
        item.update(sourceHandle=source_handle, targetHandle=target_handle)
    return item


class SubtitleSettingsTest(unittest.TestCase):
    def test_schema_keeps_the_placeholder_ports(self):
        described = describe_node_types(default_registry)["subtitle"]
        self.assertEqual([p["name"] for p in described["inputs"]], ["script", "video", "scenes", "audio"])
        self.assertEqual([p["name"] for p in described["outputs"]], ["subtitle_asset"])
        fields = {field["key"]: field for field in described["config"]}
        self.assertEqual(list(fields), ["format", "max_chars", "max_lines", "style", "font_size"])
        self.assertEqual(fields["format"]["options"], ["srt", "vtt"])
        handler = default_registry.resolve("subtitle")
        for config, code in (({"format": "ass"}, "invalid_format"), ({"max_chars": 10}, "invalid_line_length"),
                             ({"max_lines": 4}, "invalid_max_lines"), ({"style": "neon"}, "invalid_style"),
                             ({"font_size": "huge"}, "invalid_font_size")):
            with self.subTest(config=config), self.assertRaises(ConfigError) as caught:
                handler.validate_config(config)
            self.assertEqual(caught.exception.code, code)


class SubtitleRunTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'subtitle.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(SystemSetting(key="storage_dir", value=json.dumps(str(self.root / "media"))))
            db.add(User(id="user-1", email="sub@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Sub", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Rừng đêm"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))

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

    def test_scenes_make_a_stored_srt_with_lineage(self):
        graph = {"nodes": [node("scenes", "scene_source"), node("subtitle", "subtitle", {"style": "boxed"})],
                 "edges": [edge("scenes", "subtitle", "scenes", "scenes")]}
        run_id, steps = self.start(graph)
        step = steps["subtitle"]
        self.assertEqual((step.status, step.detail), ("completed", "Đã tạo phụ đề."))
        output = json.loads(step.output)
        asset = output["subtitle_asset"]
        self.assertEqual((asset["format"], asset["content_type"], asset["cue_count"], asset["timing"], asset["duration"]),
                         ("srt", "application/x-subrip", 2, "scenes", 12.0))
        self.assertEqual(asset["style"], {"preset": "boxed", "font_size": "medium"})
        self.assertEqual([c["scene_index"] for c in asset["cues"]], [1, 3])
        self.assertEqual([s["end"] for s in asset["segments"]], [4.0, 7.0, 12.0])
        self.assertEqual(output["asset_ids"], [asset["id"]])
        with self.Session() as db:
            row = db.get(Asset, asset["id"])
            self.assertEqual((row.workspace_id, row.project_id, row.run_id, row.step_id, row.provider, row.model,
                              row.content_type, row.filename.endswith(".srt")),
                             ("space-1", "project-1", run_id, step.id, "local", "subtitle", "application/x-subrip", True))
        data = (self.root / "media" / "space-1" / asset["id"]).read_bytes()
        self.assertEqual(len(data), row.bytes)
        self.assertTrue(data.decode("utf-8").startswith("1\n00:00:00,000 --> 00:00:04,000\nRừng đêm tĩnh lặng."))
        # The typed port passes the whole value on.
        self.assertEqual(default_registry.resolve("subtitle").outputs[0].read(output)["id"], asset["id"])

    def test_audio_timing_vtt_and_text_mode(self):
        graph = {"nodes": [node("scenes", "scene_source"), node("audio", "audio_source"),
                           node("subtitle", "subtitle", {"format": "vtt"})],
                 "edges": [edge("scenes", "subtitle", "scenes", "scenes"), edge("audio", "subtitle", "audio_assets", "audio")]}
        _, steps = self.start(graph)
        asset = json.loads(steps["subtitle"].output)["subtitle_asset"]
        self.assertEqual((asset["timing"], asset["content_type"], [s["end"] for s in asset["segments"]]),
                         ("audio", "text/vtt", [2.0, 5.0, 8.0]))
        self.assertTrue((self.root / "media" / "space-1" / asset["id"]).read_text(encoding="utf-8").startswith("WEBVTT"))
        _, steps = self.start({"nodes": [node("text", "text_source"), node("subtitle", "subtitle")],
                               "edges": [edge("text", "subtitle", "text", "script")]})
        asset = json.loads(steps["subtitle"].output)["subtitle_asset"]
        self.assertEqual((asset["timing"], asset["cue_count"], asset["cues"][0]["scene_index"]), ("estimate", 1, None))

    def test_legacy_edges_and_missing_input(self):
        # An edge saved before ports existed still reaches the scenes input.
        _, steps = self.start({"nodes": [node("scenes", "scene_source"), node("subtitle", "subtitle")],
                               "edges": [edge("scenes", "subtitle")]})
        self.assertEqual(steps["subtitle"].status, "completed")
        _, steps = self.start({"nodes": [node("subtitle", "subtitle")], "edges": []})
        self.assertEqual(steps["subtitle"].status, "blocked")
        with self.Session() as db:
            self.assertEqual(db.scalar(select(Asset.id).where(Asset.step_id == steps["subtitle"].id)), None)

    def test_rolled_back_run_leaves_no_file(self):
        graph = {"nodes": [node("scenes", "scene_source"), node("subtitle", "subtitle")],
                 "edges": [edge("scenes", "subtitle", "scenes", "scenes")]}
        with self.assertRaises(RuntimeError):
            with self.Session.begin() as db:
                run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1",
                                  project_id="project-1", graph_snapshot=json.dumps(graph), status="running",
                                  created_at=datetime.now(timezone.utc))
                db.add(run)
                db.flush()
                WorkflowExecutor(registry()).start_run(ExecutionContext(
                    db, workspace=db.get(Workspace, "space-1"), project=db.get(Project, "project-1"), run=run, graph=graph))
                self.assertEqual(len(list((self.root / "media" / "space-1").iterdir())), 1)
                raise RuntimeError("the request failed after the subtitle was written")
        self.assertEqual(list((self.root / "media" / "space-1").iterdir()), [])

    def test_full_storage_blocks(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"WORKSPACE_MEDIA_QUOTA_BYTES": "10"}):
            _, steps = self.start({"nodes": [node("scenes", "scene_source"), node("subtitle", "subtitle")],
                                   "edges": [edge("scenes", "subtitle", "scenes", "scenes")]})
        self.assertEqual(steps["subtitle"].status, "blocked")
        self.assertFalse((self.root / "media" / "space-1").exists() and any((self.root / "media" / "space-1").iterdir()))


if __name__ == "__main__":
    unittest.main()
