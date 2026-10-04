"""Movie files on the worker: recognize, probe, hash, fetch safely, and take the audio and frames a review needs.

* **Recognition** reads the first bytes: an MP4 or QuickTime ``ftyp`` box, or a Matroska/WebM EBML header. ZIP,
  RAR, 7z, gzip, tar, PDF, HTML and executables are refused before ffprobe ever reads them.
* **ffprobe** must then find a video stream, a duration and a known container (MP4, MOV, MKV, WebM).
* **Direct URLs** follow ``app/sources.py``: https on port 443 only, every resolved address public, the request
  sent to the checked address (the name is used only for ``Host`` and TLS), redirects checked again (at most 3),
  a size limit enforced on ``Content-Length`` and while streaming, and a time limit. Nothing is sent but the URL:
  never a credential of ours.
* **Audio** for transcription is mono 16 kHz MP3; **frames** are sampled every N seconds plus shortly after scene
  cuts found on keyframes, within a fixed budget, as small JPEGs.

Every command is an argument list run without a shell; no path appears in an error message.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Callable
from urllib.parse import urljoin

import httpx

from app import render, sources

Runner = Callable[..., subprocess.CompletedProcess]
MEDIA_TYPES = ("video/mp4", "video/quicktime", "video/x-matroska", "video/webm", "video/x-m4v", "application/mp4",
               "application/octet-stream", "binary/octet-stream", "application/x-matroska")
CONTAINER_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "matroska": "video/x-matroska", "webm": "video/webm"}
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DOWNLOAD_DEADLINE_SECONDS = 6 * 3600
USER_AGENT = "ReelForgeStudio/1.0 (+movie source import)"
FRAME_WIDTH = 512
_SHOWINFO_TIME = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")
_REFUSED_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"Rar!\x1a\x07", b"7z\xbc\xaf\x27\x1c", b"\x1f\x8b", b"%PDF",
                  b"\x7fELF", b"MZ", b"BZh", b"\xfd7zXZ")


class MovieMediaError(Exception):
    """A movie file that cannot be used, with a stable ``code``; retryable for network trouble."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class Probe:
    container: str
    content_type: str
    duration: float
    width: int
    height: int
    video_codec: str
    audio_codec: str | None


# --- recognition and probing ---------------------------------------------------------------------------------

def sniff(head: bytes) -> str | None:
    """``mp4``, ``mov`` or ``matroska`` from a file's first bytes, else None (archives and documents included)."""
    if any(head.startswith(magic) for magic in _REFUSED_MAGIC) or b"ustar" in head[257:265]:
        return None
    lowered = head[:512].lstrip().lower()
    if lowered.startswith((b"<!doctype", b"<html", b"<?xml", b"{", b"[")):
        return None
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if head[4:8] == b"ftyp":
        return "mov" if head[8:12] == b"qt  " else "mp4"
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"):
        return "mov"
    return None


def sniff_file(path: Path) -> str | None:
    with open(path, "rb") as handle:
        return sniff(handle.read(512))


def probe(ffprobe: str, path: Path, sniffed: str | None, run: Runner = subprocess.run) -> Probe:
    """Container, codecs, size and duration; ``invalid_media`` unless ffprobe finds a usable movie."""
    try:
        done = run([ffprobe, "-v", "error", "-show_entries",
                    "format=format_name,duration:stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
                   capture_output=True, text=True, timeout=120, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise MovieMediaError("invalid_media", "ffprobe could not read the movie") from exc
    if done.returncode != 0:
        raise MovieMediaError("invalid_media", "ffprobe could not read the movie")
    try:
        data = json.loads(done.stdout or "{}")
        fmt = data.get("format") or {}
        names = set(str(fmt.get("format_name") or "").split(","))
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError) as exc:
        raise MovieMediaError("invalid_media", "The movie has no readable duration") from exc
    streams = [stream for stream in data.get("streams") or [] if isinstance(stream, dict)]
    video = next((stream for stream in streams if stream.get("codec_type") == "video"
                  and str(stream.get("codec_name") or "") not in ("mjpeg", "png", "bmp", "gif")), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None:
        raise MovieMediaError("no_video", "The file has no video stream")
    if not duration > 0:
        raise MovieMediaError("invalid_media", "The movie has no duration")
    if names & {"mov", "mp4"}:
        container = "mov" if sniffed == "mov" else "mp4"
    elif names & {"matroska", "webm"}:
        codecs = {str(video.get("codec_name"))} | ({str(audio.get("codec_name"))} if audio else set())
        container = "webm" if codecs <= {"vp8", "vp9", "av1", "opus", "vorbis"} else "matroska"
    else:
        raise MovieMediaError("unsupported_type", "Only MP4, MOV, MKV and WebM movies can be used")
    if sniffed is None or (sniffed == "matroska") != (container in ("matroska", "webm")):
        raise MovieMediaError("unsupported_type", "The file content does not match a supported movie format")
    return Probe(container, CONTAINER_TYPES[container], round(duration, 3), int(video.get("width") or 0),
                 int(video.get("height") or 0), str(video.get("codec_name") or "")[:32],
                 str(audio.get("codec_name"))[:32] if audio else None)


# --- copying and hashing -------------------------------------------------------------------------------------

class Hashes:
    def __init__(self):
        self.sha256 = hashlib.sha256()
        self.md5 = hashlib.md5()  # noqa: S324 - compared with Google Drive's md5Checksum, not for security
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self.sha256.update(chunk)
        self.md5.update(chunk)
        self.size += len(chunk)

    def feed_file(self, path: Path) -> None:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                self.update(block)


def copy_file(source: Path, target: Path, *, limit: int, hashes: Hashes,
              progress: Callable[[int], None] | None = None) -> int:
    """Copy into ``target`` (a ``.part`` file), hashing as it goes; ``too_large`` past ``limit``."""
    with open(source, "rb") as reader, open(target, "wb") as writer:
        for block in iter(lambda: reader.read(4 * 1024 * 1024), b""):
            if hashes.size + len(block) > limit:
                raise MovieMediaError("too_large", "The movie is larger than the server allows")
            writer.write(block)
            hashes.update(block)
            if progress:
                progress(hashes.size)
    return hashes.size


# --- direct URLs ---------------------------------------------------------------------------------------------

def fetch_url(url: str, target: Path, *, limit: int, client: httpx.Client, hashes: Hashes,
              resolver: Callable = socket.getaddrinfo, progress: Callable[[int], None] | None = None,
              deadline_seconds: int = DOWNLOAD_DEADLINE_SECONDS, clock: Callable[[], float] = time.monotonic) -> str:
    """Download one public media URL into ``target`` with the SSRF rules of ``app/sources.py``; returns the
    content type the server declared. A blocked address, a redirect to one, a page instead of a media file or a
    file past ``limit`` fail without retry; a timeout or an unreachable host may be tried again."""
    started = clock()
    for _ in range(sources.MAX_REDIRECTS + 1):
        try:
            host, path = sources.check_url(url)
            address = sources.resolve_public(host, resolver)
        except sources.SourceError as exc:
            raise MovieMediaError(exc.code, "This address cannot be imported",
                                  retryable=exc.code == "unreachable") from exc
        pinned = f"https://[{address}]{path}" if ":" in address else f"https://{address}{path}"
        try:
            with client.stream("GET", pinned, headers={"Host": host, "User-Agent": USER_AGENT,
                                                       "Accept": "video/*, application/octet-stream;q=0.8"},
                               extensions={"sni_hostname": host}, follow_redirects=False,
                               timeout=DOWNLOAD_TIMEOUT) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise MovieMediaError("fetch_failed", "The server redirected without a location")
                    url = urljoin(url, location)
                    continue
                if response.status_code in (408, 429) or response.status_code >= 500:
                    raise MovieMediaError("fetch_failed", f"The server answered HTTP {response.status_code}",
                                          retryable=True)
                if response.status_code != 200:
                    raise MovieMediaError("fetch_failed", f"The server answered HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type and content_type not in MEDIA_TYPES and not content_type.startswith("video/"):
                    raise MovieMediaError("unsupported_type", "The address is not a movie file")
                declared = response.headers.get("Content-Length", "")
                if declared.isdigit() and int(declared) > limit:
                    raise MovieMediaError("too_large", "The movie is larger than the server allows")
                with open(target, "wb") as writer:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if hashes.size + len(chunk) > limit:
                            raise MovieMediaError("too_large", "The movie is larger than the server allows")
                        if clock() - started > deadline_seconds:
                            raise MovieMediaError("timeout", "The download took too long", retryable=True)
                        writer.write(chunk)
                        hashes.update(chunk)
                        if progress:
                            progress(hashes.size)
                if declared.isdigit() and hashes.size != int(declared):
                    raise MovieMediaError("fetch_failed", "The download ended early", retryable=True)
                return content_type
        except httpx.TimeoutException as exc:
            raise MovieMediaError("timeout", "The server did not answer in time", retryable=True) from exc
        except httpx.RequestError as exc:
            raise MovieMediaError("unreachable", "The server could not be reached", retryable=True) from exc
    raise MovieMediaError("fetch_failed", "The address redirected too many times")


# --- audio and frames ----------------------------------------------------------------------------------------

def extract_audio(ffmpeg: str, source: Path, folder: Path, *, timeout: int, run: Runner = subprocess.run) -> Path:
    """Mono 16 kHz MP3 of the movie's sound, for transcription (``audio.mp3`` in ``folder``)."""
    folder.mkdir(parents=True, exist_ok=True)
    render.run_ffmpeg([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn",
                       "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", "audio.mp3"],
                      folder, timeout, run)
    output = folder / "audio.mp3"
    if not output.is_file() or output.stat().st_size == 0:
        raise render.RenderError("audio_extraction_failed", "FFmpeg produced no audio")
    return output


def scene_cuts(ffmpeg: str, source: Path, *, timeout: int, threshold: float = 0.4,
               run: Runner = subprocess.run) -> list[float]:
    """Times of scene changes, found on keyframes only (cheap: encoders put a keyframe at most cuts)."""
    try:
        done = run([ffmpeg, "-nostdin", "-hide_banner", "-skip_frame", "nokey", "-i", str(source), "-an", "-sn",
                    "-vf", f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                   capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return []  # scene cuts only refine the sampling: without them the regular grid is used
    if done.returncode != 0:
        return []
    return sorted({round(float(match.group(1)), 3) for match in _SHOWINFO_TIME.finditer(done.stderr or "")})


def frame_times(duration: float, *, interval: float, max_frames: int, cuts: list[float] | None = None) -> list[dict]:
    """The sampled moments: a regular grid (wider for long movies) plus moments just after scene cuts.

    At most ``max_frames``; a quarter of the budget may go to scene cuts, and no two moments are closer than
    two seconds. Each is ``{"index", "time", "cut"}``, in time order."""
    if duration <= 0 or max_frames < 1:
        return []
    cut_budget = max_frames // 4 if cuts else 0
    grid_budget = max(1, max_frames - cut_budget)
    step = max(float(interval), duration / grid_budget)
    grid = [round(min(duration - 0.05, step / 2 + index * step), 3) for index in range(grid_budget)
            if step / 2 + index * step < duration]
    chosen = [(time_, False) for time_ in grid]
    if cuts and cut_budget:
        spaced = [cut for cut in cuts if 0 < cut < duration - 0.5]
        if len(spaced) > cut_budget:
            spaced = [spaced[round(index * (len(spaced) - 1) / max(1, cut_budget - 1))] for index in range(cut_budget)]
        for cut in spaced:
            moment = round(min(duration - 0.05, cut + 0.5), 3)
            if all(abs(moment - other) >= 2.0 for other, _ in chosen):
                chosen.append((moment, True))
    chosen.sort()
    return [{"index": index, "time": moment, "cut": cut} for index, (moment, cut) in enumerate(chosen[:max_frames], 1)]


def frame_name(index: int) -> str:
    return f"f-{int(index):04d}.jpg"


def extract_frame(ffmpeg: str, source: Path, folder: Path, moment: float, index: int, *, timeout: int = 120,
                  run: Runner = subprocess.run) -> Path:
    """One small JPEG at ``moment`` (fast input seeking, accurate to the frame)."""
    name = frame_name(index)
    render.run_ffmpeg([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, moment):.3f}",
                       "-i", str(source), "-frames:v", "1", "-an", "-sn",
                       "-vf", f"scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "5", name], folder, timeout, run)
    output = folder / name
    if not output.is_file() or output.stat().st_size == 0:
        raise render.RenderError("frame_extraction_failed", "FFmpeg produced no frame")
    return output


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
