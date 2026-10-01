"""FFmpeg render building blocks: tools, probing, the command, subtitle timing and safe subprocess use."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app import render
from app.render import Clip, RenderError, Track, audio_mode, build_command, retime
from app.subtitles import Cue
from app.video_files import valid_mp4


def clip(name, scene, seconds=8.0, audio=True, width=1280, height=720):
    return Clip(Path("/media/ws") / name, scene, seconds, audio, width, height)


def graph(args):
    return args[args.index("-filter_complex") + 1].split(";")


class Recorder:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls, self.result = [], subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class CommandTest(unittest.TestCase):
    def test_clips_are_joined_in_the_order_given(self):
        clips = [clip("c1", 1, 8.0), clip("c2", 2, 6.5), clip("c3", 3, 4.0)]
        args = build_command("ffmpeg", clips, [], subtitles=False)
        self.assertEqual([args[i + 1] for i, value in enumerate(args) if value == "-i"],
                         [str(item.path) for item in clips])
        steps = graph(args)
        self.assertIn("trim=duration=6.500", steps[1])
        self.assertEqual(steps[3], "[v0][v1][v2]concat=n=3:v=1:a=0[vcat]")
        self.assertEqual(args[-3:], ["-t", "18.500", "render.mp4"])
        self.assertEqual(args[args.index("-map") + 1], "[vcat]")
        for option in (["-c:v", "libx264"], ["-c:a", "aac"], ["-pix_fmt", "yuv420p"], ["-movflags", "+faststart"]):
            index = args.index(option[0])
            self.assertEqual(args[index:index + 2], option)

    def test_without_narration_each_clip_keeps_its_audio_or_silence(self):
        args = build_command("ffmpeg", [clip("c1", 1, audio=True), clip("c2", 2, 5.0, audio=False)], [], subtitles=False)
        steps = graph(args)
        self.assertTrue(steps[3].startswith("[0:a]aresample=48000"))
        self.assertEqual(steps[4], "anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration=5.000[a1]")
        self.assertEqual(steps[5], "[a0][a1]concat=n=2:v=0:a=1[aout]")
        self.assertEqual(audio_mode([clip("c1", 1)], []), "clips")

    def test_narration_takes_precedence_over_clip_audio(self):
        clips = [clip("c1", 1, 8.0), clip("c2", 2, 6.0), clip("c3", 3, 4.0)]
        # Narration for scenes 3 and 1 only, listed out of order.
        tracks = [Track(Path("/media/ws/a3"), 3), Track(Path("/media/ws/a1"), 1)]
        args = build_command("ffmpeg", clips, tracks, subtitles=False)
        joined = ";".join(graph(args))
        self.assertNotIn("[0:a]", joined)
        self.assertNotIn("[1:a]", joined)
        steps = graph(args)
        # Input 4 is scene 1's narration, input 3 scene 3's; scene 2 is silent. Each is fitted to its clip.
        self.assertTrue(steps[4].startswith("[4:a]aresample=48000") and steps[4].endswith("atrim=duration=8.000,asetpts=PTS-STARTPTS[a0]"))
        self.assertTrue(steps[5].startswith("anullsrc") and "duration=6.000" in steps[5])
        self.assertTrue(steps[6].startswith("[3:a]") and "atrim=duration=4.000" in steps[6])
        self.assertEqual(audio_mode(clips, tracks), "scenes")

    def test_one_narration_for_the_whole_video(self):
        clips = [clip("c1", 1, 8.0), clip("c2", 2, 6.0)]
        tracks = [Track(Path("/media/ws/n1"), None), Track(Path("/media/ws/n2"), None)]
        steps = graph(build_command("ffmpeg", clips, tracks, subtitles=False))
        self.assertEqual(steps[-1], "[t0][t1]concat=n=2:v=0:a=1,apad,atrim=duration=14.000[aout]")
        self.assertEqual(audio_mode(clips, tracks), "narration")
        # A single clip without a scene gets scene narration as one track too.
        self.assertEqual(audio_mode([clip("c", None)], [Track(Path("a"), 1)]), "narration")

    def test_subtitles_are_burned_from_a_relative_file_with_a_quoted_style(self):
        style = render.force_style({"preset": "bold", "font_size": "large"}, "Noto Sans")
        args = build_command("ffmpeg", [clip("c1", 1)], [], subtitles=True, style=style)
        burn = next(step for step in graph(args) if "subtitles=" in step)
        self.assertEqual(burn, "[vcat]subtitles=filename=subtitles.srt:force_style='FontName=Noto Sans,FontSize=24,"
                               + render.STYLE_PRESETS["bold"] + ",Alignment=2,MarginV=24'[vout]")
        self.assertEqual(args[args.index("-map") + 1], "[vout]")
        self.assertIn("FontSize=18", render.force_style({"preset": "unknown"}, "X"))

    def test_frame_size_is_even_and_bounded(self):
        self.assertEqual(render.frame_size([clip("c", 1, width=721, height=1281)]), (720, 1280))
        self.assertEqual(render.frame_size([clip("c", 1, width=3840, height=2160)]), (1920, 1080))
        self.assertEqual(render.frame_size([clip("c", 1, width=0, height=0)]), (720, 1280))


class TimingTest(unittest.TestCase):
    def test_scene_cues_move_into_their_clips(self):
        clips = [clip("c1", 1, 8.0), clip("c2", 2, 6.0), clip("c3", 3, 4.0)]
        segments = [{"scene_index": 1, "start": 0, "end": 4, "source": "scene"},
                    {"scene_index": 2, "start": 4, "end": 7, "source": "audio"},
                    {"scene_index": 3, "start": 7, "end": 12, "source": "scene"}]
        cues = [Cue(0, 2000, "a", 1), Cue(2000, 4000, "b {x}", 1), Cue(4000, 7000, "c", 2),
                Cue(7000, 12000, "d", 3), Cue(500, 900, "loose", None)]
        placed = retime(cues, segments, clips)
        # Scene estimates are stretched to the 8 s clip; narration timing keeps its pace; loose cues stay.
        self.assertEqual([(cue.start, cue.end, cue.text) for cue in placed],
                         [(0, 4000, "a"), (4000, 8000, "b (x)"), (8000, 11000, "c"), (14000, 18000, "d"),
                          (500, 900, "loose")])

    def test_cues_are_cut_at_the_clip_end_and_dropped_without_a_clip(self):
        clips = [clip("c1", 1, 2.0)]
        segments = [{"scene_index": 1, "start": 0, "end": 5, "source": "audio"},
                    {"scene_index": 2, "start": 5, "end": 6, "source": "scene"}]
        placed = retime([Cue(0, 1500, "x", 1), Cue(1500, 5000, "y", 1), Cue(5000, 6000, "z", 2)], segments, clips)
        self.assertEqual([(cue.start, cue.end) for cue in placed], [(0, 1500), (1500, 2000)])


class SafetyTest(unittest.TestCase):
    def test_ffmpeg_runs_without_a_shell_in_its_folder(self):
        runner = Recorder()
        render.run_ffmpeg(["ffmpeg", "-i", "x"], Path("/tmp/render-job"), 60, runner)
        args, kwargs = runner.calls[0]
        self.assertIsInstance(args, list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual((kwargs["cwd"], kwargs["timeout"], kwargs["check"]), (str(Path("/tmp/render-job")), 60, False))

    def test_failures_are_classified_without_paths(self):
        runner = Recorder(1, stderr="warning\n/srv/media/ws/abc: Invalid data\nC:\\media\\x.mp4: error while decoding\n")
        with self.assertRaises(RenderError) as caught:
            render.run_ffmpeg(["ffmpeg"], Path("."), 60, runner)
        self.assertEqual(caught.exception.code, "render_failed")
        self.assertNotIn("/srv", str(caught.exception))
        self.assertNotIn("C:\\", str(caught.exception))
        self.assertIn("Invalid data", str(caught.exception))

        def slow(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 5)

        with self.assertRaises(RenderError) as caught:
            render.run_ffmpeg(["ffmpeg"], Path("."), 5, slow)
        self.assertEqual((caught.exception.code, caught.exception.category), ("render_timeout", "timeout"))

        def missing(args, **kwargs):
            raise FileNotFoundError(args[0])

        with self.assertRaises(RenderError) as caught:
            render.run_ffmpeg(["ffmpeg"], Path("."), 5, missing)
        self.assertEqual(caught.exception.code, "ffmpeg_missing")

    def test_probe(self):
        runner = Recorder(stdout=json.dumps({"streams": [{"codec_type": "video", "width": 720, "height": 1280},
                                                         {"codec_type": "audio"}], "format": {"duration": "8.04"}}))
        self.assertEqual(render.probe("ffprobe", Path("clip"), runner),
                         {"duration": 8.04, "has_audio": True, "width": 720, "height": 1280})
        self.assertEqual(runner.calls[0][0][:3], ["ffprobe", "-v", "error"])
        for bad in (Recorder(1), Recorder(stdout="not json"), Recorder(stdout='{"format": {}}')):
            with self.subTest(result=bad.result), self.assertRaises(RenderError):
                render.probe("ffprobe", Path("clip"), bad)

    def test_tools_fonts_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "ffmpeg"
            tool.write_text("")
            with patch.dict(os.environ, {"RENDER_FFMPEG_PATH": str(tool), "RENDER_FFPROBE_PATH": str(tool)}):
                self.assertIsNone(render.tools_issue())
            with patch.dict(os.environ, {"RENDER_FFMPEG_PATH": str(tool), "RENDER_FFPROBE_PATH": str(tool) + "-gone"}):
                self.assertEqual(render.tools_issue()[0], "ffmpeg_missing")
        with patch.dict(os.environ, {"RENDER_FFMPEG_PATH": "", "RENDER_FFPROBE_PATH": ""}), \
                patch("app.render.shutil.which", return_value=None):
            self.assertIn("ffmpeg và ffprobe", render.tools_issue()[1])
        with patch("app.render.shutil.which", return_value=None):
            self.assertIsNone(render.font_issue())  # no fontconfig (e.g. Windows): libass uses system fonts
        with patch("app.render.shutil.which", return_value="/usr/bin/fc-list"):
            self.assertIsNone(render.font_issue(Recorder(stdout="Noto Sans,Noto Sans Regular\nDejaVu Sans\n")))
            self.assertEqual(render.font_issue(Recorder(stdout="DejaVu Sans\n"))[0], "font_unavailable")
            with patch.dict(os.environ, {"RENDER_SUBTITLE_FONT": "Evil'; rm -rf"}):
                self.assertEqual(render.font_issue(Recorder())[0], "font_unavailable")
        self.assertEqual(render.render_credit_cost(), 0)
        with patch.dict(os.environ, {"RENDER_CREDITS_PER_JOB": "3"}):
            self.assertEqual(render.render_credit_cost(), 3)
        with patch.dict(os.environ, {"RENDER_TIMEOUT_SECONDS": "5"}), self.assertRaises(RuntimeError):
            render.render_timeout_seconds()


class RenderHandlerTest(unittest.TestCase):
    """What the Render step checks and freezes before it queues a job."""

    def setUp(self):
        from datetime import datetime, timezone
        from uuid import uuid4

        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker

        from app.models import Asset, Base, CreditAccount, Project, SystemSetting, User, Workflow, WorkflowRun, Workspace
        from app.workflow import NodeExecutionResult, NodeHandler, build_default_registry
        from app.workflow.ports import AUDIO_ASSETS, IMAGE_ASSETS, SUBTITLE_ASSET, VIDEO_ASSETS, OutputPort

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.engine = create_engine(f"sqlite:///{root / 'render.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        tool = root / "tool"
        tool.write_text("")
        patcher = patch.dict(os.environ, {"RENDER_FFMPEG_PATH": str(tool), "RENDER_FFPROBE_PATH": str(tool)})
        patcher.start()
        self.addCleanup(patcher.stop)
        font = patch("app.render.font_issue", return_value=None)
        self.font = font.start()
        self.addCleanup(font.stop)
        self.ids = {name: str(uuid4()) for name in ("c1", "c2", "a1", "img", "sub")}
        (root / "media" / "space-1").mkdir(parents=True)
        with self.Session.begin() as db:
            db.add(SystemSetting(key="storage_dir", value=json.dumps(str(root / "media"))))
            db.add(User(id="user-1", email="render@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Render", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="R", topic="R"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=1))
            for name, content_type in (("c1", "video/mp4"), ("c2", "video/mp4"), ("a1", "audio/wav"),
                                       ("img", "image/png"), ("sub", "application/x-subrip")):
                db.add(Asset(id=self.ids[name], workspace_id="space-1", filename=name, content_type=content_type, bytes=1))
                (root / "media" / "space-1" / self.ids[name]).write_bytes(b"x")
        ids = self.ids

        def source(node_type, port, data_type, output):
            class Source(NodeHandler):
                outputs = (OutputPort(port, data_type),)

                def execute(self, context, node, inputs):
                    return NodeExecutionResult.completed("done", output)

            Source.node_type = node_type
            return Source()

        self.registry = build_default_registry()
        for handler in (
                source("clips", "video_assets", VIDEO_ASSETS, {"video_assets": [
                    {"id": ids["c2"], "scene_index": 2, "duration": 6}, {"id": ids["c1"], "scene_index": 1, "duration": 8}]}),
                source("images", "image_assets", IMAGE_ASSETS, {"image_assets": [{"id": ids["img"], "scene_index": 1}]}),
                source("voice_out", "audio_assets", AUDIO_ASSETS, {"audio_assets": [
                    {"id": ids["a1"], "scene_index": 1, "duration": 2.5}]}),
                source("subs", "subtitle_asset", SUBTITLE_ASSET, {"subtitle_asset": {
                    "id": ids["sub"], "timing": "audio", "style": {"preset": "bold", "font_size": "small"},
                    "cues": [{"start": 0, "end": 2.5, "text": "Xin chào", "scene_index": 1}],
                    "segments": [{"scene_index": 1, "start": 0, "end": 2.5, "source": "audio"}]}})):
            self.registry.register(handler)
        self.new_run = lambda graph: WorkflowRun(
            id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1", project_id="project-1",
            graph_snapshot=json.dumps(graph), status="running", created_at=datetime.now(timezone.utc))

    def start(self, sources):
        from app.models import Project, WorkflowJob, Workspace
        from app.workflow import ExecutionContext, WorkflowExecutor

        ports = {"clips": ("video_assets", "media"), "images": ("image_assets", "media"),
                 "voice_out": ("audio_assets", "audio"), "subs": ("subtitle_asset", "subtitle")}
        graph = {"nodes": [{"id": "render", "type": "render", "x": 0, "y": 0}]
                          + [{"id": name, "type": name, "x": 0, "y": 0} for name in sources],
                 "edges": [{"source": name, "target": "render", "sourceHandle": ports[name][0],
                            "targetHandle": ports[name][1]} for name in sources]}
        with self.Session.begin() as db:
            run = self.new_run(graph)
            db.add(run)
            db.flush()
            progress = WorkflowExecutor(self.registry).start_run(ExecutionContext(
                db, workspace=db.get(Workspace, "space-1"), project=db.get(Project, "project-1"), run=run, graph=graph))
            step = next(step for step in progress.steps if step.node_id == "render")
            job = db.query(WorkflowJob).filter(WorkflowJob.step_id == step.id).one_or_none()
            return step, (job.logical_key, job.payload) if job else None

    def test_payload_freezes_the_inputs(self):
        step, (key, payload) = self.start(["clips", "voice_out", "subs"])
        self.assertEqual((step.status, key), ("queued", f"render:{step.id}:final"))
        # Clips keep the order they arrived in; the video step already lists them by scene.
        self.assertEqual([clip["asset_id"] for clip in payload["clips"]], [self.ids["c2"], self.ids["c1"]])
        self.assertEqual(payload["audio"], [{"asset_id": self.ids["a1"], "scene_index": 1, "duration": 2.5}])
        self.assertEqual((payload["subtitle"]["asset_id"], payload["subtitle"]["style"]["preset"], payload["credits"]),
                         (self.ids["sub"], "bold", 0))
        self.assertEqual(json.loads(step.output), {"clip_count": 2, "audio_count": 1, "subtitles": True,
                                                   "audio_policy": "voice"})

    def test_images_fonts_and_credits_block_before_queueing(self):
        # Images are rendered as stills since Phase 16.
        step, (key, payload) = self.start(["images"])
        self.assertEqual((step.status, json.loads(step.output)["still_count"]), ("queued", 1))
        self.assertTrue(all(clip["still"] for clip in payload["clips"]))
        self.font.return_value = ("font_unavailable", "Server chưa có phông chữ Noto Sans để đốt phụ đề.")
        step, job = self.start(["clips", "subs"])
        self.assertEqual((step.status, job, json.loads(step.output)["error"]["code"]), ("blocked", None, "font_unavailable"))
        step, job = self.start(["clips"])  # no subtitles: the font does not matter
        self.assertEqual(step.status, "queued")
        with patch.dict(os.environ, {"RENDER_CREDITS_PER_JOB": "5"}), self.assertRaises(Exception) as caught:
            self.start(["clips"])
        self.assertEqual(getattr(caught.exception, "code", None), "insufficient_credits")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are not installed")
class RealFfmpegTest(unittest.TestCase):
    """A tiny local render with synthetic media; runs only where FFmpeg is installed."""

    def test_two_clips_narration_and_subtitles(self):
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)

            def make(name, *source):
                subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *source, str(folder / name)],
                               check=True, timeout=120)
                return folder / name

            first = make("c1.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:d=1.5",
                         "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", "-shortest", "-pix_fmt", "yuv420p")
            second = make("c2.mp4", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=1", "-pix_fmt", "yuv420p")
            voice = make("v1.wav", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.7")
            clips = []
            for path, scene in ((first, 1), (second, 2)):
                info = render.probe(ffprobe, path)
                clips.append(Clip(path, scene, info["duration"], info["has_audio"], info["width"], info["height"]))
            work = folder / "job"
            work.mkdir()
            cues = retime([Cue(0, 1000, "Xin chào — こんにちは", 1), Cue(1000, 2000, "Tạm biệt", 2)],
                          [{"scene_index": 1, "start": 0, "end": 1, "source": "scene"},
                           {"scene_index": 2, "start": 1, "end": 2, "source": "scene"}], clips)
            (work / render.SUBTITLE_NAME).write_bytes(render.burn_in_file(cues))
            args = build_command(ffmpeg, clips, [Track(voice, 1)], subtitles=True,
                                 style=render.force_style({"preset": "classic"}, render.subtitle_font()))
            render.run_ffmpeg(args, work, 300)
            output = work / render.OUTPUT_NAME
            self.assertTrue(valid_mp4(output, max_bytes=render.MAX_RENDER_BYTES))
            info = render.probe(ffprobe, output)
            self.assertAlmostEqual(info["duration"], clips[0].duration + clips[1].duration, delta=0.2)
            self.assertEqual((info["width"], info["height"], info["has_audio"]), (320, 240, True))


if __name__ == "__main__":
    unittest.main()
