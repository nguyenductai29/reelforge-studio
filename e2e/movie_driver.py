"""Advance the movie workers for the browser tests (e2e/tests/12-movie-sources.spec.ts), with test doubles only.

Run from ``e2e/.stack/api`` (the spec does it) with that stack's environment:

    python ../../movie_driver.py sources             # imports, uploads and deletions, until none is due
    python ../../movie_driver.py pipeline <run id>   # every worker lane of a review, until it waits for review

* Google Drive is the fake Drive server (``tests/fake_drive.py``, ``REELFORGE_GOOGLE_API_BASE``), shared with the API.
* A direct URL is served by an in-process mock behind a resolver that answers a public address: the SSRF rules of the
  real worker stay on (a private address would still be refused).
* FFmpeg, ffprobe and every AI provider (vision, transcription, text, voice) are faked; nothing leaves the machine.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import httpx

MOVIE = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 1200
VALID_MP4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             b"\x00\x00\x00\x10mdat12345678"
             b"\x00\x00\x00\x10moov\x00\x00\x00\x08trak")
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


def fake_run(args, **kwargs):
    """ffprobe reports a 125-second 1280×720 movie (4-second clips); ffmpeg writes the file it was asked for."""
    tool = Path(args[0]).name.lower()
    if tool.startswith("ffprobe"):
        target = Path(args[-1]).name
        length = 4.0 if target.startswith("clip-") else 125.0
        data = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": str(length)},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                            {"codec_type": "audio", "codec_name": "aac"}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
    if "showinfo" in " ".join(args):
        return subprocess.CompletedProcess(args, 0, "", "[Parsed_showinfo_1] n:0 pts_time:40.5\n")
    folder = Path(kwargs.get("cwd") or ".")
    output = args[-1]
    if output == "part-%03d.mp3":
        (folder / "part-000.mp3").write_bytes(MP3)
    elif output.endswith(".jpg"):
        (folder / output).write_bytes(JPEG)
    elif output.endswith(".mp3"):
        (folder / output).write_bytes(MP3)
    elif output.endswith((".srt", ".vtt", ".ass")):
        (folder / output).write_text("", encoding="utf-8")
    else:
        (folder / output).write_bytes(VALID_MP4)
    return subprocess.CompletedProcess(args, 0, "", "")


def resolver(host, port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def media(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/redirect.mp4":
        return httpx.Response(302, headers={"Location": "https://10.0.0.8/private.mp4"})
    if request.url.path.endswith(".mp4"):
        return httpx.Response(200, headers={"Content-Type": "video/mp4", "Content-Length": str(len(MOVIE))},
                              content=MOVIE)
    return httpx.Response(404)


def start():
    # The stack's dummy FFmpeg (e2e/prepare.py): steps check that the tools exist, the fakes above do the work.
    tools = Path("instance/tools").resolve()
    os.environ["RENDER_FFMPEG_PATH"] = str(tools / "ffmpeg")
    os.environ["RENDER_FFPROBE_PATH"] = str(tools / "ffprobe")
    from app import render
    from app.runtime_env import start_process

    render.font_issue = lambda run=None: None  # libass is faked too
    start_process("e2e_movie_driver")


def sources() -> int:
    from app import movie_worker

    client = httpx.Client(transport=httpx.MockTransport(media))
    done = 0
    for _ in range(30):
        if not movie_worker.run_source_work(http_client=client, resolver=resolver, runner=fake_run,
                                            worker_id="e2e-driver"):
            break
        done += 1
    print(json.dumps({"source_work": done}))
    return 0


def pipeline(run_id: str) -> int:
    from app import movie_worker, render_worker, source_worker, text_worker, voice_worker
    from app.audio_files import pcm_to_wav
    from app.db import Session
    from app.models import WorkflowRun
    from app.providers.text import TextResult, TextUsage
    from app.providers.transcription import TranscriptResult, TranscriptSegment
    from app.providers.voice import VoiceResult

    story = {"title": "E2E", "summary": "A short test movie.", "setup": "A quiet start", "climax": "A loud end",
             "plot_points": ["Start", "End"], "acts": [], "important_moments": [], "themes": ["test"]}
    review = {"title": "Review: E2E", "sections": [
        {"text": "The movie opens quietly.", "kind": "fact", "source_ranges": [{"start": 5, "end": 12}], "importance": 0.5},
        {"text": "It ends with a bang.", "kind": "opinion", "source_ranges": [{"start": 100, "end": 108}], "importance": 0.9}]}
    meta = {"title": "Review: E2E", "description": "A short review.", "tags": ["review"]}

    class Writer:
        def __init__(self, name):
            self.name = name

        def generate(self, **kwargs):
            system = kwargs.get("system_prompt") or ""
            if "still frames" in system:  # Visual Analysis: one note per frame of the batch
                frames = [int(part.split()[1]) for part in kwargs["prompt"].splitlines() if part.startswith("Frame ")]
                text = json.dumps({"frames": [{"index": index, "description": f"Frame {index}", "importance": 0.5}
                                              for index in frames]})
            elif "analyse the story" in system:
                text = json.dumps(story)
            elif "review scripts" in system:
                text = json.dumps(review)
            else:
                text = json.dumps(meta)
            return TextResult(text=text, usage=TextUsage.of(10, 20), provider=self.name, model=kwargs["model"])

        def close(self):
            pass

    class Transcriber:
        def transcribe(self, path, *, model, language=None):
            segments = (TranscriptSegment(1.0, 4.0, "Hello there"), TranscriptSegment(101.0, 104.0, "Goodbye"))
            return TranscriptResult("openai", model, "Hello there Goodbye", "english", 125.0, segments, "req_e2e")

        def close(self):
            pass

    class Voices:
        def generate(self, request):
            return VoiceResult("gemini", request.model, pcm_to_wav(b"\x00\x00" * 24000 * 2, sample_rate=24000),
                               "audio/wav", 2.0, "resp")

        def close(self):
            pass

    status = "running"
    for _ in range(60):
        progressed = [
            movie_worker.run_job(provider_factory=Writer, runner=fake_run, worker_id="e2e-driver"),
            source_worker.run_one(provider_factory=lambda name: Transcriber(), runner=fake_run),
            text_worker.run_one(provider_factory=Writer),
            voice_worker.run_one(client=Voices()),
            render_worker.run_one(runner=fake_run),
        ]
        with Session() as db:
            status = db.get(WorkflowRun, run_id).status
        if status != "running" or not any(progressed):
            break
    print(json.dumps({"run": run_id, "status": status}))
    return 0 if status == "awaiting_review" else 1


def main(argv: list[str]) -> int:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    start()
    if argv[:1] == ["sources"]:
        return sources()
    if len(argv) == 2 and argv[0] == "pipeline":
        return pipeline(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
