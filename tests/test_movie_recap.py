"""Phase 11 end to end: transcript → story → recap → matched scenes → source clips → render → review. Offline."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

RECAP = r'''
STORY = json.dumps({"title": "Ngôi làng", "summary": "Một cô gái cứu ngôi làng.", "characters": [{"name": "Lan", "role": "chính"}],
                    "plot_points": ["Tìm chìa khóa", "Rồng thức dậy", "Làng được cứu"], "acts": [], "important_moments": [],
                    "themes": ["dũng cảm"]}, ensure_ascii=False)
SCRIPT = json.dumps({"title": "Recap: Ngôi làng", "scenes": [
    {"text": "Lan tìm thấy một vật kỳ lạ.", "source_quote": "chiếc chìa khóa vàng dưới gốc cây", "moment": "Lan đào đất"},
    {"text": "Rồi con rồng xuất hiện.", "source_quote": "con rồng thức dậy", "moment": "Rồng mở mắt"},
    {"text": "Cuối cùng làng bình yên.", "source_quote": "ngôi làng được cứu", "moment": "Dân làng reo hò"}]}, ensure_ascii=False)
SEGMENTS = [(0.0, 4.0, "Chào mừng đến với ngôi làng nhỏ"), (5.0, 9.0, "Cô gái tìm thấy chiếc chìa khóa vàng dưới gốc cây"),
            (10.0, 14.0, "Con rồng thức dậy sau một trăm năm"), (15.0, 19.0, "Cuối cùng ngôi làng được cứu")]

movie = upload("phim.mp4", VALID_MP4, "video/mp4").json()["id"]
add_tool("script", "openai", "gpt-4.1-mini")
add_tool("transcription", "openai", "whisper-1")
fund(50)
nodes = [node("src", "source_media", {"asset_id": movie}), node("tr", "transcribe", x=300),
         node("story", "story_analysis", x=600), node("recap", "recap_script", {"scene_count": 3, "style": "review",
                                                                                "spoiler_level": "light"}, x=900),
         node("match", "match_scenes", {"min_seconds": 3, "max_seconds": 8}, x=1200), node("clips", "extract_clips", x=1500),
         node("render", "render", x=1800), node("review", "review", x=2100)]
edges = [edge("src", "source", "tr", "source"), edge("tr", "transcript", "story", "source"),
         edge("story", "analysis", "recap", "analysis"), edge("tr", "transcript", "recap", "source"),
         edge("recap", "scenes", "match", "scenes"), edge("tr", "transcript", "match", "transcript"),
         edge("src", "video", "match", "video"), edge("match", "source_clips", "clips", "source_clips"),
         edge("clips", "video_assets", "render", "media"), edge("render", "rendered_video", "review", "media")]
workflow, saved = save_graph(nodes, edges)
assert saved.status_code == 200, saved.text
checks = {s["node_id"]: s["status"] for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert checks["tr"] == "ready" and checks["match"] == "configured" and checks["clips"] == "configured", checks
run = start(workflow)

runner = ffmpeg_runner(duration=20.0, fail_copy=("clip-01.mp4",))
assert source_worker.run_one(runner=runner, provider_factory=lambda name: Transcriber(SEGMENTS))
drain(text_worker, provider_factory=writer({"analyse the story": STORY, "recap scripts": SCRIPT}))
state, by_node = steps(run["id"])
assert by_node["story"]["output"]["analysis"]["title"] == "Ngôi làng", by_node["story"]
assert "[00:05] Cô gái tìm thấy" in WRITER_PROMPTS[0]["prompt"]  # story analysis sees timestamps
assert "Hint at the twists" in WRITER_PROMPTS[1]["prompt"] and "a review" in WRITER_PROMPTS[1]["prompt"]
recap = by_node["recap"]["output"]
assert [s["index"] for s in recap["scenes"]] == [1, 2, 3] and recap["title"] == "Recap: Ngôi làng", recap
clips = by_node["match"]["output"]["source_clips"]
assert [(c["scene_index"], c["reason"], c["source_asset_id"]) for c in clips] == \
       [(1, "transcript_match", movie), (2, "transcript_match", movie), (3, "transcript_match", movie)], clips
assert [c["start"] for c in clips] == [4.7, 9.7, 14.7], clips
assert by_node["clips"]["status"] == "queued", by_node["clips"]

# Clips are cut locally: stream copy first, re-encode when the copy fails (clip 1 here).
assert render_worker.run_one(runner=runner)
state, by_node = steps(run["id"])
cut = by_node["clips"]["output"]["video_assets"]
assert [(c["scene_index"], c["cut"]) for c in cut] == [(1, "reencode"), (2, "copy"), (3, "copy")], cut
assert all(c["source_asset_id"] == movie and c["content_type"] == "video/mp4" for c in cut)
commands = [c for c in runner.calls if Path(c[0]).name == "ffmpeg" and c[-1].startswith("clip-")]
assert [("copy" in c, c[-1]) for c in commands] == [(True, "clip-01.mp4"), (False, "clip-01.mp4"),
                                                     (True, "clip-02.mp4"), (True, "clip-03.mp4")], commands
with Session() as db:
    rows = db.scalars(select(Asset).where(Asset.id.in_([c["id"] for c in cut]))).all()
    assert {row.source_asset_id for row in rows} == {movie}  # lineage is stored on the asset
    assert all(row.run_id == run["id"] for row in rows)

# Render takes the source clips in scene order, like generated clips.
assert by_node["render"]["status"] == "queued", by_node["render"]
assert render_worker.run_one(runner=runner)
state, by_node = steps(run["id"])
assert (state["status"], by_node["review"]["status"]) == ("awaiting_review", "awaiting_review"), state
with Session() as db:
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.logical_key.like("render:%:final")))
    assert [c["asset_id"] for c in job.payload["clips"]] == [c["id"] for c in cut]
approved = client.post(f"/api/workflow-runs/{run['id']}/approve")
assert approved.status_code == 200 and approved.json()["status"] == "completed", approved.text
summary = client.get(f"/api/workflow-runs/{run['id']}/summary").json()
assert summary["final_video"]["final"] is True and summary["publishing"]["ready"] is True
# The source video itself is never offered for publishing.
assert summary["final_video"]["asset_id"] != movie

# A transcript without timestamps (a plain text source) cannot place scenes: Match blocks with a reason.
workflow, _ = save_graph([node("txt", "source_text", {"text": "Một câu chuyện không có mốc thời gian nào cả."}),
                          node("src", "source_media", {"asset_id": movie}),
                          node("sc", "scenes", x=300), node("match", "match_scenes", x=600)],
                         [edge("txt", "text", "sc", "script"), edge("sc", "scenes", "match", "scenes"),
                          edge("txt", "source", "match", "transcript"), edge("src", "video", "match", "video")])
state, by_node = steps(start(workflow)["id"])
assert (by_node["match"]["status"], by_node["match"]["output"]["error"]["code"]) == ("blocked", "missing_timestamps"), by_node
print("recap ok")
'''


class MovieRecapTest(unittest.TestCase):
    def test_recap_pipeline_cuts_source_clips_with_lineage_and_renders_them(self):
        completed = run_program(RECAP)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])


if __name__ == "__main__":
    unittest.main()
