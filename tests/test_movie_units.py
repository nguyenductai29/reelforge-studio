"""Movie sources, offline and without a database: local path safety, file recognition, the URL fetch's SSRF rules,
frame sampling, the timeline, clip selection, the parsers of the movie steps, the render's scene windows and the
Google Drive client against an in-memory Drive (tests/fake_drive.py)."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import clip_selection, google_drive, movie_media, movie_sources, movie_timeline, render  # noqa: E402
from app.workflow.nodes.movie import parse_sections, parse_vision, vision_prompt  # noqa: E402
from app.workflow.nodes.recap import parse_story  # noqa: E402
from fake_drive import FOLDER, FakeDrive  # noqa: E402

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
MKV_HEAD = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def public_resolver(host, port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_resolver(host, port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]


class LocalImportTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name) / "import"
        (self.root / "films").mkdir(parents=True)
        (self.root / "films" / "movie.mp4").write_bytes(MP4_HEAD)
        (self.root / "notes.txt").write_text("not a movie")
        (self.root / ".hidden.mp4").write_bytes(MP4_HEAD)
        (Path(self.folder.name) / "outside.mp4").write_bytes(MP4_HEAD)

    def tearDown(self):
        self.folder.cleanup()

    def test_a_file_inside_the_root(self):
        path = movie_sources.resolve_local("films/movie.mp4", root=self.root)
        self.assertEqual(path, (self.root / "films" / "movie.mp4").resolve())
        self.assertEqual(movie_sources.resolve_local("films\\movie.mp4", root=self.root), path)

    def test_traversal_and_absolute_paths_are_refused(self):
        for bad in ("../outside.mp4", "films/../../outside.mp4", "/etc/passwd", "C:/Windows/win.ini", "", "a\x00b",
                    str(Path(self.folder.name) / "outside.mp4")):
            with self.subTest(path=bad), self.assertRaises(movie_sources.MovieSourceError) as caught:
                movie_sources.resolve_local(bad, root=self.root)
            self.assertIn(caught.exception.code, ("invalid_path", "not_found"))

    def test_only_regular_movie_files(self):
        with self.assertRaises(movie_sources.MovieSourceError) as caught:
            movie_sources.resolve_local("notes.txt", root=self.root)
        self.assertEqual(caught.exception.code, "unsupported_type")
        with self.assertRaises(movie_sources.MovieSourceError) as caught:
            movie_sources.resolve_local("films", root=self.root)
        self.assertEqual(caught.exception.code, "not_a_file")
        with self.assertRaises(movie_sources.MovieSourceError) as caught:
            movie_sources.resolve_local("films/missing.mp4", root=self.root)
        self.assertEqual(caught.exception.code, "not_found")

    def test_links_are_never_followed(self):
        link = self.root / "link.mp4"
        try:
            os.symlink(Path(self.folder.name) / "outside.mp4", link)
        except (OSError, NotImplementedError):
            self.skipTest("this system does not allow creating symbolic links")
        with self.assertRaises(movie_sources.MovieSourceError) as caught:
            movie_sources.resolve_local("link.mp4", root=self.root)
        self.assertEqual(caught.exception.code, "symlink_refused")
        inside = self.root / "inside.mp4"
        os.symlink(self.root / "films" / "movie.mp4", inside)  # even a link that stays inside the root
        with self.assertRaises(movie_sources.MovieSourceError):
            movie_sources.resolve_local("inside.mp4", root=self.root)

    def test_listing_shows_folders_and_movies_only(self):
        top = movie_sources.list_local("", root=self.root)
        films = movie_sources.list_local("films", root=self.root)
        self.assertEqual([(entry["type"], entry["name"]) for entry in top["entries"]], [("folder", "films")])
        self.assertIsNone(top["parent"])
        self.assertEqual(films["folder"], "films")
        self.assertEqual(films["parent"], "")
        self.assertEqual([(entry["name"], entry["bytes"]) for entry in films["entries"]], [("movie.mp4", len(MP4_HEAD))])
        self.assertNotIn(str(self.root), json.dumps(films))  # relative paths only

    def test_names_and_urls_are_cleaned(self):
        self.assertEqual(movie_sources.sanitize_name("C:\\films\\phim\x07.mp4"), "phim.mp4")
        self.assertEqual(movie_sources.sanitize_name("   "), "movie")
        self.assertEqual(movie_sources.display_url("https://user:pw@CDN.example.com/a/b.mp4?token=SECRET#x"),
                         "https://cdn.example.com/a/b.mp4")


class RecognitionTest(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(movie_media.sniff(MP4_HEAD), "mp4")
        self.assertEqual(movie_media.sniff(b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 32), "mov")
        self.assertEqual(movie_media.sniff(MKV_HEAD), "matroska")
        for refused in (b"PK\x03\x04rest", b"Rar!\x1a\x07\x00", b"%PDF-1.7", b"MZ\x90\x00", b"\x7fELF",
                        b"<!DOCTYPE html><html>", b"  <html>", b'{"a": 1}', b"\x1f\x8b\x08", b"random bytes"):
            with self.subTest(head=refused[:8]):
                self.assertIsNone(movie_media.sniff(refused + b"\x00" * 600))

    @staticmethod
    def _probe(data: dict, returncode: int = 0):
        return lambda args, **kwargs: subprocess.CompletedProcess(args, returncode, json.dumps(data), "")

    def test_probe(self):
        good = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "42.5"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                            {"codec_type": "audio", "codec_name": "aac"}]}
        info = movie_media.probe("ffprobe", Path("movie.mp4"), "mp4", self._probe(good))
        self.assertEqual((info.container, info.content_type, info.duration, info.width, info.audio_codec),
                         ("mp4", "video/mp4", 42.5, 1280, "aac"))
        webm = {"format": {"format_name": "matroska,webm", "duration": "10"},
                "streams": [{"codec_type": "video", "codec_name": "vp9"}, {"codec_type": "audio", "codec_name": "opus"}]}
        self.assertEqual(movie_media.probe("ffprobe", Path("m.webm"), "matroska", self._probe(webm)).container, "webm")
        cases = [({"format": {"format_name": "mp3", "duration": "10"}, "streams": [{"codec_type": "audio"}]}, "mp4",
                  "no_video"),
                 (good, "matroska", "unsupported_type"),  # the bytes said Matroska, ffprobe found MP4
                 ({"format": {"format_name": "avi", "duration": "10"}, "streams": [{"codec_type": "video"}]}, "mp4",
                  "unsupported_type"),
                 ({"format": {"format_name": "mov,mp4", "duration": "N/A"}, "streams": []}, "mp4", "invalid_media")]
        for data, sniffed, code in cases:
            with self.subTest(code=code), self.assertRaises(movie_media.MovieMediaError) as caught:
                movie_media.probe("ffprobe", Path("x"), sniffed, self._probe(data))
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            movie_media.probe("ffprobe", Path("x"), "mp4", self._probe({}, returncode=1))
        self.assertEqual(caught.exception.code, "invalid_media")


class FetchTest(unittest.TestCase):
    """Direct URLs: https to public addresses only, redirects checked again, size and type limits."""

    def fetch(self, url, handler, *, resolver=public_resolver, limit=10_000):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "source.part"
            hashes = movie_media.Hashes()
            client = httpx.Client(transport=httpx.MockTransport(handler))
            content_type = movie_media.fetch_url(url, target, limit=limit, client=client, hashes=hashes,
                                                 resolver=resolver)
            return content_type, target.read_bytes(), hashes

    def test_a_public_movie(self):
        seen = []

        def handler(request):
            seen.append((str(request.url), request.headers["host"]))
            return httpx.Response(200, headers={"Content-Type": "video/mp4", "Content-Length": str(len(MP4_HEAD))},
                                  content=MP4_HEAD)

        content_type, data, hashes = self.fetch("https://media.example.com/films/movie.mp4?sig=abc", handler)
        self.assertEqual((content_type, data), ("video/mp4", MP4_HEAD))
        self.assertEqual(hashes.sha256.hexdigest(), hashlib.sha256(MP4_HEAD).hexdigest())
        # Sent to the checked address; the name only travels in Host (and TLS).
        self.assertEqual(seen, [("https://93.184.216.34/films/movie.mp4?sig=abc", "media.example.com")])

    def test_refused_addresses(self):
        never = lambda request: self.fail("no request may be sent")  # noqa: E731
        for url in ("http://media.example.com/m.mp4", "https://127.0.0.1/m.mp4", "https://localhost/m.mp4",
                    "https://media.example.com:8443/m.mp4", "https://user:pw@media.example.com/m.mp4",
                    "https://[::1]/m.mp4", "https://169.254.169.254/latest/meta-data"):
            with self.subTest(url=url), self.assertRaises(movie_media.MovieMediaError) as caught:
                self.fetch(url, never)
            self.assertEqual(caught.exception.code, "blocked_url")
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://internal.example.com/m.mp4", never, resolver=private_resolver)
        self.assertEqual(caught.exception.code, "blocked_url")

    def test_redirect_to_a_private_address_is_refused(self):
        def handler(request):
            return httpx.Response(302, headers={"Location": "https://10.0.0.8/secret.mp4"})

        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://media.example.com/m.mp4", handler)
        self.assertEqual(caught.exception.code, "blocked_url")

    def test_size_type_and_redirect_limits(self):
        big = lambda request: httpx.Response(200, headers={"Content-Type": "video/mp4", "Content-Length": "999999"},  # noqa: E731
                                             content=b"")
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://media.example.com/m.mp4", big)
        self.assertEqual(caught.exception.code, "too_large")
        streamed = lambda request: httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=b"x" * 20_000)  # noqa: E731
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://media.example.com/m.mp4", streamed)
        self.assertEqual(caught.exception.code, "too_large")
        page = lambda request: httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html></html>")  # noqa: E731
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://media.example.com/m.mp4", page)
        self.assertEqual(caught.exception.code, "unsupported_type")
        loop = lambda request: httpx.Response(302, headers={"Location": "/again.mp4"})  # noqa: E731
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://media.example.com/m.mp4", loop)
        self.assertEqual(caught.exception.code, "fetch_failed")
        busy = lambda request: httpx.Response(503)  # noqa: E731
        with self.assertRaises(movie_media.MovieMediaError) as caught:
            self.fetch("https://media.example.com/m.mp4", busy)
        self.assertTrue(caught.exception.retryable)


class FramesAndTimelineTest(unittest.TestCase):
    def test_frame_times_stay_in_budget(self):
        frames = movie_media.frame_times(7200, interval=10, max_frames=300, cuts=[float(t) for t in range(5, 7000, 7)])
        self.assertLessEqual(len(frames), 300)
        times = [frame["time"] for frame in frames]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 <= moment < 7200 for moment in times))
        self.assertTrue(any(frame["cut"] for frame in frames))
        self.assertGreater(times[-1], 6900)  # the end of the movie is sampled too
        short = movie_media.frame_times(40, interval=10, max_frames=300)
        self.assertEqual([frame["time"] for frame in short], [5.0, 15.0, 25.0, 35.0])
        self.assertEqual([frame["index"] for frame in short], [1, 2, 3, 4])
        self.assertEqual(movie_media.frame_times(0, interval=10, max_frames=10), [])

    def test_timeline_windows_and_budget(self):
        transcript = {"segments": [{"start": 1, "end": 4, "text": "Where is the key?"},
                                   {"start": 31, "end": 35, "text": "The dragon wakes up."}]}
        visual = {"frames": [{"time": 5, "description": "A girl digs under a tree", "importance": 0.4},
                             {"time": 32, "description": "A dragon opens its eyes", "location": "a cave",
                              "importance": 0.9}]}
        timeline = movie_timeline.build(transcript, visual, duration=40, window=10, cuts=[30.5])
        self.assertEqual(len(timeline["windows"]), 4)
        self.assertEqual(timeline["windows"][3]["importance"], 0.9)
        self.assertTrue(timeline["windows"][3]["cut"])
        lines = movie_timeline.lines(timeline["windows"])
        self.assertEqual(lines[0], "[00:00–00:10] Dialogue: Where is the key? | On screen: A girl digs under a tree")
        source = movie_timeline.as_source(timeline, title="Phim", movie_source_id="m1")
        self.assertEqual(source["source_type"], "timeline")
        self.assertEqual(len(source["segments"]), 2)
        # A long movie fits the prompt budget by widening the windows.
        many = {"segments": [{"start": t, "end": t + 2, "text": "word " * 40} for t in range(0, 7200, 3)]}
        long = movie_timeline.build(many, None, duration=7200, budget=20_000)
        self.assertLessEqual(sum(len(line) + 1 for line in movie_timeline.lines(long["windows"])), 20_000)
        self.assertLessEqual(len(movie_timeline.prompt_text(long, budget=5_000)), 5_000)


class ClipSelectionTest(unittest.TestCase):
    SECTIONS = [{"index": 1, "text": " ".join(["setup"] * 20), "source_ranges": [{"start": 10, "end": 20}]},
                {"index": 2, "text": " ".join(["dragon"] * 25), "source_ranges": [{"start": 300, "end": 330}]},
                {"index": 3, "text": " ".join(["ending"] * 15), "source_ranges": [{"start": 5000, "end": 5010}]}]

    def test_clips_cover_each_section_without_repeats(self):
        chosen = clip_selection.select(self.SECTIONS, duration=5400, movie_source_id="m1", spoken={1: 9.0, 2: 11.5})
        clips = chosen["clips"]
        self.assertTrue(clips)
        for clip in clips:
            self.assertGreaterEqual(round(clip["end"] - clip["start"], 3), 2.0)
            self.assertLessEqual(clip["end"] - clip["start"], 8.0 + 1e-6)
            self.assertEqual(clip["movie_source_id"], "m1")
        spans = sorted((clip["start"], clip["end"]) for clip in clips)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start + 1e-6)  # never the same moment twice
        self.assertEqual([clip["scene_index"] for clip in clips], sorted(clip["scene_index"] for clip in clips))
        first = [clip for clip in clips if clip["scene_index"] == 1]
        self.assertGreaterEqual(sum(clip["end"] - clip["start"] for clip in first), 9.0)
        self.assertTrue(all(9.0 <= clip["start"] <= 20.0 for clip in first))  # from its own time range first
        self.assertLess(chosen["total_seconds"], 5400 * 0.25)
        self.assertTrue(all(10 <= clip["start"] < 5400 for clip in clips[-2:]))

    def test_bounded_clip_count_and_never_the_whole_movie(self):
        sections = [{"index": index, "text": "word " * 200} for index in range(1, 9)]
        chosen = clip_selection.select(sections, duration=120, movie_source_id="m1", max_clips=10)
        self.assertLessEqual(len(chosen["clips"]), 10)
        self.assertIn("share_exceeded", chosen["warnings"])
        self.assertLessEqual(sum(clip["end"] - clip["start"] for clip in chosen["clips"]), 120)
        self.assertEqual(clip_selection.select([], duration=100, movie_source_id="m1")["clips"], [])
        self.assertEqual(clip_selection.select(sections, duration=0, movie_source_id="m1")["clips"], [])

    def test_timeline_match_when_no_range(self):
        windows = [{"start": 600, "end": 610, "dialogue": "the golden key under the old tree", "visual": "",
                    "importance": 0.5}]
        chosen = clip_selection.select([{"index": 1, "text": "She finds the golden key under the tree."}],
                                       duration=1200, movie_source_id="m1", windows=windows)
        self.assertEqual(chosen["clips"][0]["reason"], "timeline_match")
        self.assertGreaterEqual(chosen["clips"][0]["start"], 599)


class ParserTest(unittest.TestCase):
    def test_review_sections(self):
        reply = json.dumps({"title": "Review", "sections": [
            {"text": "It starts in a village.", "kind": "fact", "importance": 1.7,
             "source_ranges": [{"start": 12, "end": 20}, {"start": 9000, "end": 9010}, {"start": -3}]},
            {"text": "The pacing is uneven.", "kind": "opinion", "source_ranges": [{"start": 50}]},
            {"text": ""}, "garbage"]})
        parsed = parse_sections(f"```json\n{reply}\n```", 8, 600)
        self.assertEqual(parsed["title"], "Review")
        self.assertEqual(len(parsed["scenes"]), 2)
        first, second = parsed["scenes"]
        self.assertEqual(first["source_ranges"], [{"start": 12.0, "end": 20.0}])  # outside the movie: dropped
        self.assertEqual(first["importance"], 1.0)
        self.assertEqual(second["kind"], "opinion")
        self.assertEqual(second["source_ranges"], [{"start": 50.0, "end": 55.0}])
        self.assertEqual(parse_sections("not json", 8, 600)["scenes"], [])

    def test_vision_notes(self):
        frames = [{"index": 41, "time": 400.0, "cut": False}, {"index": 42, "time": 410.0, "cut": True}]
        reply = json.dumps({"frames": [
            {"index": 42, "description": "A man in a grey coat runs", "characters": ["a man in a grey coat", 3],
             "location": "a train station", "action": "running", "importance": 0.8},
            {"index": 1, "description": "A woman reads a letter", "importance": "high"},
            {"index": 42, "description": "duplicate"}, {"index": 99, "description": "unknown frame"}]})
        notes = parse_vision(reply, frames)
        self.assertEqual([note["index"] for note in notes], [41, 42])  # "1" meant the first image of the batch
        self.assertEqual(notes[1]["characters"], ["a man in a grey coat"])
        self.assertIsNone(notes[0]["importance"])
        prompt = vision_prompt(frames, "vi")
        self.assertIn("Vietnamese", prompt)
        self.assertIn("Frame 42 (image 2) at 06:50, just after a scene cut", prompt)

    def test_story_from_a_timeline(self):
        story = parse_story(json.dumps({"title": "T", "summary": "S", "setup": "A village", "climax": "The fight",
                                        "turning_points": [{"description": "The key", "start": 120}, {"x": 1}],
                                        "visual_moments": [{"description": "Sunrise", "start": 10, "end": 14}]}))
        self.assertEqual(story["setup"], "A village")
        self.assertEqual(story["turning_points"], [{"description": "The key", "start": 120.0}])
        self.assertEqual(story["visual_moments"][0]["end"], 14.0)
        self.assertNotIn("ending", story)  # absent fields stay absent
        self.assertNotIn("setup", parse_story(json.dumps({"title": "T"})))


class SceneWindowsTest(unittest.TestCase):
    def test_consecutive_clips_of_one_scene_share_a_window(self):
        clips = [render.Clip(Path(name), scene, duration, True, 720, 1280) for name, duration, scene in
                 (("a", 3.0, 1), ("b", 4.0, 1), ("c", 2.0, 2), ("d", 5.0, 3), ("e", 1.0, 3))]
        self.assertEqual(render.scene_windows(clips), {1: (0.0, 7.0), 2: (7.0, 9.0), 3: (9.0, 15.0)})


SETTINGS = google_drive.DriveConfig(enabled=True, auth_mode="oauth", root_folder_id="rootfolder01",
                                    client_id="client-id", client_secret="client-SECRET",
                                    refresh_token="refresh-SECRET", delete_mode="trash")


class DriveClientTest(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.http = httpx.Client(transport=httpx.MockTransport(self.drive.handle))

    def tearDown(self):
        self.http.close()

    def client(self, settings=SETTINGS):
        return google_drive.DriveClient(settings, http_client=self.http)

    def test_configuration_problems(self):
        self.assertEqual(google_drive.DriveConfig(False, "oauth", "x").problem(), "disabled")
        self.assertEqual(google_drive.DriveConfig(True, "oauth", "").problem(), "no_root_folder")
        self.assertEqual(google_drive.DriveConfig(True, "oauth", "bad/id").problem(), "no_root_folder")
        self.assertEqual(google_drive.DriveConfig(True, "oauth", "root").problem(), "missing_credentials")
        self.assertEqual(google_drive.DriveConfig(True, "service_account", "root").problem(), "missing_credentials")
        self.assertIsNone(SETTINGS.problem())
        self.assertNotIn("SECRET", repr(SETTINGS))
        with self.assertRaises(google_drive.DriveError):
            google_drive.DriveClient(google_drive.DriveConfig(False, "oauth", "x"))

    def test_folders_upload_resume_download_and_removal(self):
        drive = self.client()
        folder = drive.source_folder("ws-1", "src-1")
        self.assertEqual(folder, drive.source_folder("ws-1", "src-1"))  # found, not created twice
        self.assertEqual(self.drive.folder("movie-sources", "ws-1", "src-1"), folder)
        data = os.urandom(3 * 1024 * 1024 + 123)
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "source.mp4"
            local.write_bytes(data)
            session = drive.start_upload(folder, "source.mp4", "video/mp4", len(data))
            self.assertTrue(session.startswith("https://www.googleapis.com/upload/"))
            with mock.patch.object(google_drive, "CHUNK", 1024 * 1024):
                self.drive.fail("upload_chunk", 503)
                with self.assertRaises(google_drive.DriveError) as caught:
                    drive.upload(session, local, len(data))
                self.assertTrue(caught.exception.retryable)
                offset = drive.upload_offset(session, len(data))
                self.assertEqual(offset, 0)
                moved = []
                with mock.patch.object(google_drive, "CHUNK", 1024 * 1024):
                    meta = drive.upload(session, local, len(data), offset=offset, progress=moved.append)
            self.assertEqual(meta["md5Checksum"], hashlib.md5(data).hexdigest())
            self.assertEqual(int(meta["size"]), len(data))
            self.assertEqual(moved[-1], len(data))
            self.assertIsInstance(drive.upload_offset(session, len(data)), dict)  # finished: its metadata
            target = Path(tmp) / "copy.part"
            target.write_bytes(data[:1000])  # a download interrupted earlier resumes after these bytes
            seen = []
            self.assertEqual(drive.download(meta["id"], target, limit=len(data), on_chunk=seen.append), len(data))
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(sum(len(chunk) for chunk in seen), len(data) - 1000)
            with self.assertRaises(google_drive.DriveError) as caught:
                drive.download(meta["id"], Path(tmp) / "small.part", limit=100)
            self.assertEqual(caught.exception.code, "too_large")
        self.assertTrue(drive.remove(meta["id"]))
        self.assertTrue(self.drive.files[meta["id"]]["trashed"])
        self.assertTrue(drive.delete(meta["id"]))
        self.assertFalse(drive.delete(meta["id"]))  # already gone: harmless
        self.assertFalse(drive.trash(meta["id"]))
        self.assertEqual(self.drive.tokens, 1)  # one access token for the whole session

    def test_inbox_listing_and_move(self):
        inbox = self.drive.folder("inbox", "ws-1", create=True)
        movie = self.drive.add_file(inbox, "Phim hay.mkv", MKV_HEAD, "video/x-matroska")
        self.drive.add_file(inbox, "notes.txt", b"x", "text/plain")
        drive = self.client()
        self.assertEqual(drive.inbox_folder("ws-1"), inbox)
        self.assertIsNone(drive.inbox_folder("ws-2"))
        listing = drive.children(inbox, folders=False, videos=True)
        self.assertEqual([item["name"] for item in listing["files"]], ["Phim hay.mkv"])
        target = drive.source_folder("ws-1", "src-1")
        meta = drive.move(movie, target, inbox, "source.mkv")
        self.assertEqual((meta["name"], meta["parents"]), ("source.mkv", [target]))
        with self.assertRaises(google_drive.DriveError):
            drive.move("bad id", target, inbox, "x")

    def test_errors_are_classified_and_tokens_refreshed(self):
        drive = self.client()
        self.drive.fail("list", 429)
        with self.assertRaises(google_drive.DriveError) as caught:
            drive.children("rootfolder01")
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("rate_limited", True))
        self.drive.fail("list", 500)
        with self.assertRaises(google_drive.DriveError) as caught:
            drive.children("rootfolder01")
        self.assertEqual(caught.exception.code, "server_error")
        with self.assertRaises(google_drive.DriveError) as caught:
            drive.file("missing01")
        self.assertEqual(caught.exception.code, "not_found")
        self.drive.fail("token", 400)
        with self.assertRaises(google_drive.DriveError) as caught:
            self.client().token()
        self.assertEqual(caught.exception.code, "auth_failed")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual({entry.get("refresh_token") for entry in self.drive.token_requests}, {"***"})

    def test_service_account_signs_a_jwt(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        settings = google_drive.DriveConfig(True, "service_account", "rootfolder01", service_account_json=json.dumps(
            {"client_email": "robot@project.iam.gserviceaccount.com", "private_key": pem, "private_key_id": "k1"}))
        self.assertTrue(self.client(settings).token())
        self.assertEqual(self.drive.token_requests[-1]["grant_type"], "urn:ietf:params:oauth:grant-type:jwt-bearer")
        broken = google_drive.DriveConfig(True, "service_account", "rootfolder01", service_account_json="{}")
        with self.assertRaises(google_drive.DriveError) as caught:
            self.client(broken).token()
        self.assertEqual(caught.exception.code, "auth_failed")

    def test_connection_check_leaves_nothing_behind(self):
        result = google_drive.check_connection(SETTINGS, http_client=self.http)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual([check["key"] for check in result["checks"]],
                         ["configuration", "credentials", "root_folder", "upload", "delete"])
        self.assertEqual(result["checks"][1]["account"], "drive-owner@example.com")
        self.assertEqual(self.drive.live_files(), [])
        self.assertEqual([item for item in self.drive.files.values() if item["mimeType"] != FOLDER], [])
        self.assertNotIn("SECRET", json.dumps(result))
        self.drive.fail("token", 401)
        failed = google_drive.check_connection(SETTINGS, http_client=self.http)
        self.assertEqual((failed["status"], failed["checks"][-1]), ("error", {"key": "credentials", "status": "error",
                                                                              "code": "auth_failed"}))
        missing = google_drive.check_connection(google_drive.DriveConfig(True, "oauth", "rootfolder01"))
        self.assertEqual(missing["checks"], [{"key": "configuration", "status": "error",
                                              "code": "missing_credentials"}])

    def test_the_test_server_must_be_local(self):
        for value in ("https://evil.example.com", "http://10.0.0.1:8021", "ftp://127.0.0.1"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {google_drive.DEV_BASE_ENV: value}), \
                    self.assertRaises(google_drive.DriveError):
                google_drive.DriveClient(SETTINGS, http_client=self.http)
        with mock.patch.dict(os.environ, {google_drive.DEV_BASE_ENV: "http://127.0.0.1:8021"}):
            self.assertEqual(google_drive.DriveClient(SETTINGS, http_client=self.http).api, "http://127.0.0.1:8021")


if __name__ == "__main__":
    unittest.main()
