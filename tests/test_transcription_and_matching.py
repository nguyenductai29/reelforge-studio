"""Phase 10 transcription provider and Phase 11 scene matching, recap parsing and clip commands. Offline."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from app import render
from app.providers.transcription import (OpenAITranscriptionProvider, TranscriptionProviderError,
                                         create_transcription_provider, transcription_config_issue,
                                         transcription_credit_cost)
from app.scene_matching import Segment, match_scenes, normalize, segments_from
from app.workflow.nodes.recap import _clips, parse_recap, parse_story, source_prompt_text

VERBOSE = {"text": "Hello there. Goodbye.", "language": "english", "duration": 12.5,
           "segments": [{"start": 0.0, "end": 2.4, "text": " Hello there."},
                        {"start": 9.0, "end": 11.2, "text": " Goodbye."}]}


class TranscriptionProviderTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.audio = Path(folder.name) / "part-000.mp3"
        self.audio.write_bytes(b"ID3" + b"\x00" * 64)

    def provider(self, handler):
        seen = []

        def record(request):
            seen.append(request)
            return handler(request)
        return OpenAITranscriptionProvider("sk-test", http_client=httpx.Client(transport=httpx.MockTransport(record))), seen

    def test_request_and_timestamped_segments(self):
        provider, seen = self.provider(lambda request: httpx.Response(200, json=VERBOSE,
                                                                      headers={"x-request-id": "req_1"}))
        result = provider.transcribe(self.audio, model="whisper-1", language="en")
        request = seen[0]
        self.assertEqual(str(request.url), "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(request.headers["Authorization"], "Bearer sk-test")
        body = request.content.decode("latin-1")
        for part in ('name="model"', "whisper-1", 'name="response_format"', "verbose_json",
                     "timestamp_granularities[]", 'name="language"', 'filename="part-000.mp3"'):
            self.assertIn(part, body)
        self.assertEqual((result.text, result.language, result.duration, result.remote_request_id),
                         ("Hello there. Goodbye.", "english", 12.5, "req_1"))
        self.assertEqual([(s.start, s.end, s.text) for s in result.segments],
                         [(0.0, 2.4, "Hello there."), (9.0, 11.2, "Goodbye.")])

    def test_errors_are_classified_and_never_carry_the_key(self):
        cases = [(401, "authentication_error", False), (429, "rate_limited", True), (500, "provider_unavailable", True),
                 (400, "invalid_request", False)]
        for status, code, retryable in cases:
            provider, _ = self.provider(lambda request, status=status: httpx.Response(status, json={"error": {"message": "no"}}))
            with self.subTest(status=status), self.assertRaises(TranscriptionProviderError) as caught:
                provider.transcribe(self.audio, model="whisper-1")
            self.assertEqual((caught.exception.code, caught.exception.retryable), (code, retryable))
            self.assertNotIn("sk-test", str(caught.exception))

        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)
        provider, _ = self.provider(timeout)
        with self.assertRaises(TranscriptionProviderError) as caught:
            provider.transcribe(self.audio, model="whisper-1")
        self.assertEqual((caught.exception.code, caught.exception.category), ("submission_unknown", "timeout"))
        provider, _ = self.provider(lambda request: httpx.Response(200, json={"text": "", "segments": []}))
        with self.assertRaises(TranscriptionProviderError) as caught:
            provider.transcribe(self.audio, model="whisper-1")
        self.assertEqual(caught.exception.code, "empty_output")
        provider, _ = self.provider(lambda request: httpx.Response(200, content=b"not json"))
        with self.assertRaises(TranscriptionProviderError) as caught:
            provider.transcribe(self.audio, model="whisper-1")
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_local_checks_happen_before_any_request(self):
        provider, seen = self.provider(lambda request: httpx.Response(200, json=VERBOSE))
        for kwargs, path in (({"model": "gpt-4o"}, self.audio), ({"model": "whisper-1", "language": "vi-VN"}, self.audio),
                             ({"model": "whisper-1"}, self.audio.with_suffix(".exe"))):
            with self.subTest(kwargs=kwargs), self.assertRaises(TranscriptionProviderError):
                provider.transcribe(path, **kwargs)
        with patch.object(Path, "stat", return_value=os.stat_result((0,) * 6 + (26 * 1024 * 1024, 0, 0, 0))):
            with self.assertRaises(TranscriptionProviderError):
                provider.transcribe(self.audio, model="whisper-1")
        self.assertEqual(seen, [])

    def test_configuration_and_price(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(transcription_config_issue("openai")[0], "missing_key")
            with self.assertRaises(TranscriptionProviderError):
                create_transcription_provider("openai")
        self.assertEqual(transcription_config_issue("nobody")[0], "unsupported_provider")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x", "TRANSCRIPTION_CREDITS_PER_JOB": "3"}):
            self.assertIsNone(transcription_config_issue("openai"))
            self.assertEqual(transcription_credit_cost(), 3)
        with patch.dict(os.environ, {"TRANSCRIPTION_CREDITS_PER_JOB": "0"}), self.assertRaises(RuntimeError):
            transcription_credit_cost()


SEGMENTS = [Segment(float(i * 5), float(i * 5 + 4), text) for i, text in enumerate([
    "Chào mừng đến với ngôi làng nhỏ",
    "Không ai biết bí mật của khu rừng",
    "Cô gái tìm thấy chiếc chìa khóa vàng dưới gốc cây",
    "Con rồng thức dậy sau một trăm năm",
    "Họ cùng nhau chạy trốn qua dòng sông",
    "Cuối cùng ngôi làng được cứu",
])]


class SceneMatchingTest(unittest.TestCase):
    def test_quotes_find_their_moment_in_order(self):
        scenes = [{"index": 1, "text": "Mọi chuyện bắt đầu khi cô ấy tìm ra một vật lạ.",
                   "source_quote": "chiếc chìa khóa vàng dưới gốc cây"},
                  {"index": 2, "text": "Và rồi thứ đáng sợ nhất xuất hiện.", "moment": "Con rồng thức dậy"},
                  {"index": 3, "text": "Kết thúc có hậu.", "source_quote": "ngoi lang duoc cuu"}]
        clips = match_scenes(scenes, SEGMENTS, source_asset_id="video-1", min_seconds=3, max_seconds=12, padding=0.5)
        self.assertEqual([clip["scene_index"] for clip in clips], [1, 2, 3])
        self.assertEqual([clip["reason"] for clip in clips], ["transcript_match"] * 3)
        self.assertAlmostEqual(clips[0]["start"], 9.5)
        self.assertEqual(clips[1]["start"], 14.5)
        # Diacritics do not matter: "ngoi lang duoc cuu" matches "ngôi làng được cứu".
        self.assertEqual(clips[2]["start"], 24.5)
        for clip in clips:
            self.assertEqual(clip["source_asset_id"], "video-1")
            self.assertTrue(0 < clip["confidence"] <= 1)
            self.assertTrue(3 <= clip["end"] - clip["start"] <= 12)
            self.assertLessEqual(clip["end"], SEGMENTS[-1].end)

    def test_unmatched_scenes_fall_back_to_their_position_and_long_narration_stretches_the_clip(self):
        scenes = [{"index": 1, "text": " ".join(["lời"] * 25), "source_quote": "không có trong phim"},
                  {"index": 2, "text": "ngắn"}]
        clips = match_scenes(scenes, SEGMENTS, source_asset_id="v", min_seconds=3, max_seconds=12, min_confidence=0.5)
        self.assertEqual([clip["reason"] for clip in clips], ["position_fallback"] * 2)
        self.assertEqual([clip["confidence"] for clip in clips], [0.0, 0.0])
        self.assertGreaterEqual(clips[0]["end"] - clips[0]["start"], 10.5)  # 25 words ≈ 10 s + 0.5 s
        self.assertLess(clips[0]["start"], clips[1]["start"])
        with self.assertRaises(ValueError):
            match_scenes([{"text": "x"}], [], source_asset_id="v")

    def test_segments_are_validated_and_matching_is_fast(self):
        self.assertEqual(segments_from([{"start": 2, "end": 1, "text": "x"}, {"start": 0, "end": 1, "text": " "},
                                        {"start": "a", "end": 1, "text": "x"}, {"start": 1, "end": 2, "text": "ok"}]),
                         [Segment(1.0, 2.0, "ok")])
        self.assertEqual(normalize("Đêm HÀ NỘI"), "dem ha noi")
        long_movie = [Segment(i * 4.0, i * 4.0 + 3.5, f"câu thoại số {i} về nhân vật {i % 37} và nơi chốn {i % 11}")
                      for i in range(2000)]
        started = time.monotonic()
        clips = match_scenes([{"index": n, "text": "x", "source_quote": f"câu thoại số {n * 97}"} for n in range(1, 21)],
                             long_movie, source_asset_id="v")
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(clips), 20)
        self.assertEqual(clips[4]["reason"], "transcript_match")


class RecapParsingTest(unittest.TestCase):
    def test_story_analysis_is_bounded_and_tolerates_prose(self):
        reply = "```json\n" + json.dumps({
            "title": "Ngôi làng", "summary": "Tóm tắt", "characters": [{"name": "Lan", "role": "chính"}, {"x": 1}],
            "plot_points": ["A", {"description": "B"}, ""], "acts": [{"name": "Hồi 1", "summary": "Mở đầu"}],
            "important_moments": [{"description": "Tìm chìa khóa", "quote": "chìa khóa vàng", "start": 10, "end": -1}],
            "themes": ["dũng cảm", 3]}, ensure_ascii=False) + "\n```"
        story = parse_story(reply)
        self.assertEqual(story["characters"], [{"name": "Lan", "role": "chính", "description": ""}])
        self.assertEqual(story["plot_points"], ["A", "B"])
        self.assertEqual(story["important_moments"][0]["start"], 10.0)
        self.assertIsNone(story["important_moments"][0]["end"])
        self.assertEqual(story["themes"], ["dũng cảm"])
        self.assertEqual(parse_story("Chỉ là văn bản")["summary"], "Chỉ là văn bản")

    def test_recap_scenes_carry_quotes_and_estimates(self):
        recap = parse_recap(json.dumps({"title": "Recap", "scenes": [
            {"text": "Mở đầu câu chuyện ở ngôi làng.", "source_quote": "Chào mừng", "moment": "Toàn cảnh làng"},
            {"text": ""}, {"text": "Kết thúc.", "moment": ""}]}), 20)
        self.assertEqual([scene["index"] for scene in recap["scenes"]], [1, 2])
        self.assertEqual(recap["scenes"][0]["visual_prompt"], "Toàn cảnh làng")
        self.assertEqual(recap["scenes"][1]["visual_prompt"], "Kết thúc.")
        self.assertEqual(recap["script"], "Mở đầu câu chuyện ở ngôi làng.\n\nKết thúc.")
        fallback = parse_recap("Đoạn một.\n\nĐoạn hai.\n\nĐoạn ba.", 2)
        self.assertEqual([scene["text"] for scene in fallback["scenes"]], ["Đoạn một.", "Đoạn hai."])

    def test_prompts_show_timestamps_and_clip_lists_are_checked(self):
        text = source_prompt_text({"text": "x", "segments": [{"start": 65, "end": 66, "text": "Xin chào"},
                                                             {"start": 3700, "end": 3701, "text": "Muộn"}]})
        self.assertEqual(text, "[01:05] Xin chào\n[1:01:40] Muộn")
        self.assertEqual(source_prompt_text("  plain  "), "plain")
        self.assertEqual(_clips([{"source_asset_id": "v", "start": 1, "end": 4, "scene_index": 2}]),
                         [{"source_asset_id": "v", "start": 1.0, "end": 4.0, "scene_index": 2}])
        for bad in ([{"source_asset_id": "v", "start": 4, "end": 1}], [{"source_asset_id": "v", "start": 0, "end": 500}],
                    [{"start": 0, "end": 1}], ["x"]):
            with self.subTest(bad=bad):
                self.assertIsNone(_clips(bad))

    def test_clip_commands_copy_or_reencode_only_the_main_streams(self):
        copy = render.clip_command("ffmpeg", Path("/m/src"), 10, 14.5, "clip-01.mp4", copy=True)
        self.assertEqual(copy[copy.index("-ss") + 1], "10.000")
        self.assertEqual(copy[copy.index("-t") + 1], "4.500")
        self.assertIn("copy", copy)
        self.assertEqual(copy[-1], "clip-01.mp4")
        encode = render.clip_command("ffmpeg", Path("/m/src"), 0, 3, "clip-01.mp4", copy=False)
        self.assertIn("libx264", encode)
        self.assertIn("aac", encode)
        for command in (copy, encode):
            self.assertIn("0:v:0", command)
            self.assertIn("-sn", command)


if __name__ == "__main__":
    unittest.main()
