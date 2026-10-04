"""Phase 16 features: background music, still images in Render, workspace content defaults, scripts, profile. Offline."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

from app import render  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class RenderCommandTest(unittest.TestCase):
    def clip(self, name, scene, duration=4.0, still=False):
        return render.Clip(Path(name), scene, duration, not still, 720, 1280, still)

    def test_stills_loop_for_their_duration_and_music_goes_under_everything(self):
        clips = [self.clip("a.mp4", 1), self.clip("b.png", 2, 3.5, still=True)]
        tracks = [render.Track(Path("v1.wav"), 1), render.Track(Path("v2.wav"), 2)]
        command = render.build_command("ffmpeg", clips, tracks, subtitles=False, music=render.Track(Path("m.mp3"), None),
                                       music_volume=0.25)
        still = command.index("b.png")
        self.assertEqual(command[still - 7:still], ["-loop", "1", "-framerate", "30", "-t", "3.500", "-i"])
        music = command.index("m.mp3")
        self.assertEqual(command[music - 3:music], ["-stream_loop", "-1", "-i"])
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[4:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.25", graph)
        self.assertIn("[abed][music]amix=inputs=2:duration=first:dropout_transition=0,volume=2[aout]", graph)
        self.assertEqual(command[command.index("-t", still + 1):][1], "7.500")  # the output length

    def test_music_played_once_is_not_looped(self):
        clips = [render.Clip(Path("a.mp4"), 1, 4.0, False, 1080, 1920)]
        command = render.build_command("ffmpeg", clips, [], subtitles=False, music=render.Track(Path("m.mp3"), None),
                                       music_loop=False)
        self.assertNotIn("-stream_loop", command)
        self.assertEqual(command[command.index("m.mp3") - 1], "-i")
        # It is still cut where the video ends.
        self.assertIn("atrim=duration=4.000", command[command.index("-filter_complex") + 1])

    def test_without_music_or_stills_the_command_is_unchanged(self):
        clips = [self.clip("a.mp4", 1), self.clip("b.mp4", 2)]
        command = render.build_command("ffmpeg", clips, [], subtitles=False)
        self.assertNotIn("-loop", command)
        self.assertNotIn("-stream_loop", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("amix", graph)
        self.assertTrue(graph.endswith("concat=n=2:v=0:a=1[aout]"))


FEATURES = r'''
from app import render_worker
from app.models import UserProfile
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
add_tool("script", "openai", "gpt-4.1-mini")
fund(50)

# Background music: an uploaded track, mixed by Render under uploaded stills.
song = upload("nhac.mp3", MP3, "audio/mpeg").json()["id"]
photo = upload("san-pham.png", PNG, "image/png").json()["id"]
workflow, saved = save_graph(
    [node("img", "source_media", {"asset_id": photo}), node("bgm", "music", {"asset_id": song, "volume": 30}),
     node("render", "render", x=300), node("review", "review", x=600)],
    [edge("img", "image", "render", "media"), edge("bgm", "music", "render", "music"),
     edge("render", "rendered_video", "review", "media")])
assert saved.status_code == 200, saved.text
checks = {s["node_id"]: s["status"] for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert checks["bgm"] == "configured" and checks["render"] == "configured", checks
run = start(workflow)
state, by_node = steps(run["id"])
assert by_node["bgm"]["output"]["music"]["volume"] == 30
assert (by_node["render"]["status"], by_node["render"]["output"]["still_count"], by_node["render"]["output"]["music"]) == \
       ("queued", 1, True), by_node["render"]
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.logical_key.like("render:%:final")))
    assert job.payload["music"] == {"asset_id": song, "volume": 30, "mode": "loop"} and job.payload["clips"][0]["still"] is True
runner = ffmpeg_runner()
assert render_worker.run_one(runner=runner)
state, by_node = steps(run["id"])
assert by_node["render"]["status"] == "completed", by_node["render"]
command = next(c for c in runner.calls if Path(c[0]).name == "ffmpeg")
assert "-loop" in command and "-stream_loop" in command and "volume=0.30" in command[command.index("-filter_complex") + 1]
assert command[command.index("-loop") + 5] == "5.000"  # no narration: the default still length
# Music must be an audio upload; an empty Music step blocks with a reason.
_, saved = save_graph([node("bgm", "music", {"asset_id": photo})], [])
assert saved.status_code == 422 and saved.json()["detail"]["code"] == "invalid_asset"
workflow, _ = save_graph([node("bgm", "music")], [])
assert client.get(f"/api/workflows/{workflow}/readiness").json()["steps"][0]["status"] == "missing_input"

# Workspace content defaults fill the text settings a step leaves empty; a step's own value wins.
settings = client.get("/api/settings").json()["workspace"]
assert (settings["default_tone"], settings["default_platform"], settings["default_duration"]) == ("neutral", "generic", None)
saved = client.put("/api/settings/workspace", json={**settings, "default_tone": "funny", "default_platform": "tiktok",
                                                    "default_duration": 45, "default_publish_time": "19:30"})
assert saved.status_code == 200, saved.text
assert client.put("/api/settings/workspace", json={**settings, "default_publish_time": "25:00"}).status_code == 422
assert client.put("/api/settings/workspace", json={**settings, "default_tone": "angry"}).status_code == 422
workflow, _ = save_graph([node("idea", "idea"), node("w1", "ai_writer", x=300), node("w2", "ai_writer", {"tone": "dramatic"}, x=300)],
                         [edge("idea", "topic", "w1", "prompt"), edge("idea", "topic", "w2", "prompt")])
start(workflow)
WRITER_PROMPTS.clear()
drain(text_worker, provider_factory=writer())
prompts = sorted(p["prompt"] for p in WRITER_PROMPTS)
assert any("Tone: funny" in p and "TikTok" in p and "45 seconds" in p for p in prompts), prompts
assert any("Tone: dramatic" in p and "TikTok" in p for p in prompts), prompts

# Scripts written by runs, newest first (Library → Scripts, a project's latest script).
scripts = client.get("/api/scripts", params={"project_id": project}).json()
assert scripts["total"] == 2 and scripts["items"][0]["node_type"] == "ai_writer", scripts
assert scripts["items"][0]["text"] == "Bản tóm tắt ngắn." and scripts["items"][0]["words"] == 4
assert client.get("/api/scripts", params={"project_id": "other"}).json()["total"] == 0

# Uploaded files can be attached to a project; generated media stays with its run.
assert client.patch(f"/api/assets/{song}", json={"project_id": project}).status_code == 200
assert next(a for a in client.get("/api/dashboard").json()["assets"] if a["id"] == song)["project_id"] == project
assert client.patch(f"/api/assets/{song}", json={"project_id": "missing"}).status_code == 404
with Session() as db:
    rendered = db.scalar(select(Asset.id).where(Asset.run_id == run["id"]))
assert client.patch(f"/api/assets/{rendered}", json={"project_id": None}).status_code == 409

# A display name, stored beside the user.
assert client.put("/api/settings/profile", json={"display_name": "  Tài   Nguyễn "}).json() == {"display_name": "Tài Nguyễn"}
assert client.get("/api/settings").json()["profile"] == {"display_name": "Tài Nguyễn"}
assert client.put("/api/settings/profile", json={"display_name": "x" * 81}).status_code == 422
assert client.put("/api/settings/profile", json={"display_name": ""}).json()["display_name"] is None

# Publications are listed one page at a time.
connect_youtube()
for _ in range(3):
    run_id, _ = approved_run()
    publish(run_id, target("youtube"), status=201)
listed = client.get("/api/publications", params={"limit": 2}).json()
assert (listed["total"], len(listed["publications"])) == (3, 2), listed
assert len(client.get("/api/publications", params={"limit": 2, "offset": 2}).json()["publications"]) == 1
print("features ok")
'''


class ProductFeaturesTest(unittest.TestCase):
    def test_music_stills_defaults_scripts_assets_profile_and_paging(self):
        completed = run_program(FEATURES)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])


if __name__ == "__main__":
    unittest.main()
