"""FFmpeg rendering: scene clips + narration + subtitles → one H.264/AAC MP4.

System ``ffmpeg`` and ``ffprobe`` are used (never bundled): from
``RENDER_FFMPEG_PATH`` / ``RENDER_FFPROBE_PATH``, else from ``PATH``. Commands are
argument lists run without a shell, in a private temporary folder; the
subtitle file is written there and referred to by a relative name, so no path
ever appears inside a filtergraph.

Timeline: clips are joined in the order they arrive (a Video step lists them by
scene), each scaled and padded to the first clip's frame size (at most 1920
pixels on the long side), at 30 fps, keeping its own length.

Audio policy:

* narration (connected Voice audio) takes precedence and the clips' own audio is
  muted;
* narration with one file per scene is placed at the start of its scene's clip,
  padded with silence or cut to the clip's length;
* other narration (one file for the whole script) is joined in order and padded
  or cut to the whole video;
* without narration, each clip keeps its own audio (silence if it has none).

Still images (an Image step, or uploaded pictures) are scenes too: each is
shown for its scene's narration when there is one, else shares the single
narration with the other stills, else lasts ``RENDER_STILL_SECONDS`` (default 5).

Background music (a Music step) is looped to the video's length, lowered to its
volume and mixed under everything else.

Subtitles are re-timed onto this timeline: cues that belong to a scene are
placed inside that scene's clip. Cues timed by narration keep their pace (and
are cut at the clip's end, like the narration); cues timed by scene estimates or
clips are stretched to the clip. Cues without a scene keep their times.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping

from app.subtitles import Cue, burn_in_text, to_srt

SUBTITLE_NAME = "subtitles.srt"
OUTPUT_NAME = "render.mp4"
FPS = 30
MAX_SIDE = 1920
MAX_RENDER_BYTES = 500 * 1024 * 1024
DEFAULT_FONT = "Noto Sans"
FONT_SIZES = {"small": 14, "medium": 18, "large": 24}  # in libass units for a 288-line script
STYLE_PRESETS = {
    "classic": "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0",
    # libass draws the BorderStyle=3 box in OutlineColour.
    "boxed": "PrimaryColour=&H00FFFFFF,OutlineColour=&H99000000,BackColour=&H99000000,BorderStyle=3,Outline=1,Shadow=0",
    "bold": "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Bold=1",
}
_FONT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}\Z")
_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s'\"]+")

Runner = Callable[..., subprocess.CompletedProcess]


class RenderError(Exception):
    """A render failure with a stable code and category; its message never contains a path."""

    def __init__(self, code: str, message: str, category: str = "generation_failed"):
        super().__init__(message)
        self.code = code
        self.category = category


@dataclass(frozen=True)
class Clip:
    path: Path
    scene_index: int | None
    duration: float
    has_audio: bool
    width: int
    height: int
    # A still image shown for ``duration`` seconds (Phase 16).
    still: bool = False


@dataclass(frozen=True)
class Track:
    path: Path
    scene_index: int | None


# Configuration ---------------------------------------------------------------

def tools() -> tuple[str | None, str | None]:
    """The ffmpeg and ffprobe executables, or ``None`` for each one that is missing."""
    found = []
    for name, variable in (("ffmpeg", "RENDER_FFMPEG_PATH"), ("ffprobe", "RENDER_FFPROBE_PATH")):
        configured = os.environ.get(variable, "").strip()
        if configured:
            found.append(configured if Path(configured).is_file() else None)
        else:
            found.append(shutil.which(name))
    return found[0], found[1]


def tools_issue() -> tuple[str, str] | None:
    ffmpeg, ffprobe = tools()
    missing = [name for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if path is None]
    if missing:
        return "ffmpeg_missing", f"Server chưa cài {' và '.join(missing)} (cần FFmpeg trên PATH hoặc RENDER_FFMPEG_PATH)."
    return None


def subtitle_font() -> str:
    font = os.environ.get("RENDER_SUBTITLE_FONT", "").strip() or DEFAULT_FONT
    if not _FONT_NAME.fullmatch(font):
        raise RenderError("font_unavailable", "RENDER_SUBTITLE_FONT must be a plain font family name",
                          "configuration_error")
    return font


def font_issue(run: Runner = subprocess.run) -> tuple[str, str] | None:
    """Whether the subtitle font is installed, asked of fontconfig where it exists (Linux servers)."""
    try:
        font = subtitle_font()
    except RenderError:
        return "font_unavailable", "RENDER_SUBTITLE_FONT chỉ được là tên họ phông chữ."
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return None  # e.g. Windows development: libass finds fonts through the system
    try:
        listed = run([fc_list, ":", "family"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    families = {name.strip().lower() for line in (listed.stdout or "").splitlines() for name in line.split(",")}
    if font.lower() not in families:
        return "font_unavailable", f"Server chưa có phông chữ {font} để đốt phụ đề (cài fonts-noto-core và fonts-noto-cjk)."
    return None


def render_timeout_seconds() -> int:
    try:
        seconds = int(os.environ.get("RENDER_TIMEOUT_SECONDS", "1800"))
    except ValueError as exc:
        raise RuntimeError("RENDER_TIMEOUT_SECONDS must be an integer") from exc
    if not 60 <= seconds <= 21600:
        raise RuntimeError("RENDER_TIMEOUT_SECONDS must be between 60 and 21600")
    return seconds


def render_credit_cost() -> int:
    """Credits for one render (``RENDER_CREDITS_PER_JOB``, default 0: rendering is local and free)."""
    try:
        amount = int(os.environ.get("RENDER_CREDITS_PER_JOB", "0"))
    except ValueError as exc:
        raise RuntimeError("RENDER_CREDITS_PER_JOB must be an integer") from exc
    if not 0 <= amount <= 100000:
        raise RuntimeError("RENDER_CREDITS_PER_JOB must be between 0 and 100000")
    return amount


# Probing ---------------------------------------------------------------------

def still_seconds() -> float:
    """How long a still image is shown when nothing else decides (``RENDER_STILL_SECONDS``, default 5)."""
    try:
        value = float(os.environ.get("RENDER_STILL_SECONDS", "5"))
    except ValueError as exc:
        raise RenderError("invalid_config", "RENDER_STILL_SECONDS must be a number", "configuration_error") from exc
    if not 1 <= value <= 60:
        raise RenderError("invalid_config", "RENDER_STILL_SECONDS must be from 1 to 60", "configuration_error")
    return value


def probe(ffprobe: str, path: Path, run: Runner = subprocess.run, *, still: bool = False) -> dict:
    """Duration, audio presence and frame size of one media file; a still image has no duration (0)."""
    try:
        done = run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                    "-of", "json", str(path)], capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("render_failed", "ffprobe did not finish") from exc
    if done.returncode != 0:
        raise RenderError("render_failed", "ffprobe could not read an input file")
    try:
        data = json.loads(done.stdout or "{}")
        streams = [stream for stream in data.get("streams") or [] if isinstance(stream, dict)]
        raw = (data.get("format") or {}).get("duration")
        duration = 0.0 if still and raw in (None, "N/A") else float(raw)
    except (TypeError, ValueError) as exc:
        raise RenderError("render_failed", "ffprobe returned no duration") from exc
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    return {"duration": duration, "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
            "width": int(video.get("width") or 0), "height": int(video.get("height") or 0)}


def frame_size(clips: list[Clip]) -> tuple[int, int]:
    """The first clip's frame, even-sized and at most ``MAX_SIDE`` on its long side."""
    width, height = clips[0].width or 720, clips[0].height or 1280
    scale = min(1.0, MAX_SIDE / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


# Subtitles on the render timeline ---------------------------------------------

def retime(cues: list[Cue], segments: list[Mapping[str, Any]], clips: list[Clip]) -> list[Cue]:
    windows, clock = {}, 0.0
    for clip in clips:
        if clip.scene_index is not None and clip.scene_index not in windows:
            windows[clip.scene_index] = (clock, clock + clip.duration)
        clock += clip.duration
    total = round(clock * 1000)
    spans = {}
    for segment in segments:
        index = segment.get("scene_index") if isinstance(segment, Mapping) else None
        if isinstance(index, int) and not isinstance(index, bool):
            spans[index] = (round(float(segment.get("start") or 0) * 1000), round(float(segment.get("end") or 0) * 1000),
                            segment.get("source"))
    placed = []
    for cue in cues:
        if cue.scene_index is None or cue.scene_index not in spans:
            start, end = cue.start, min(cue.end, total)
        elif cue.scene_index not in windows:
            continue  # the scene has no clip in this render
        else:
            seg_start, seg_end, source = spans[cue.scene_index]
            window_start, window_end = (round(value * 1000) for value in windows[cue.scene_index])
            length = max(1, seg_end - seg_start)
            scale = 1.0 if source == "audio" else (window_end - window_start) / length
            start = window_start + round((cue.start - seg_start) * scale)
            end = min(window_end, window_start + round((cue.end - seg_start) * scale))
        if end > start:
            placed.append(Cue(start, end, burn_in_text(cue.text), cue.scene_index))
    return placed


def burn_in_file(cues: list[Cue]) -> bytes:
    return to_srt(cues).encode("utf-8")


def force_style(style: Mapping[str, Any] | None, font: str) -> str:
    style = style if isinstance(style, Mapping) else {}
    preset = STYLE_PRESETS.get(style.get("preset"), STYLE_PRESETS["classic"])
    size = FONT_SIZES.get(style.get("font_size"), FONT_SIZES["medium"])
    return f"FontName={font},FontSize={size},{preset},Alignment=2,MarginV=24"


# The FFmpeg command --------------------------------------------------------

def audio_mode(clips: list[Clip], tracks: list[Track]) -> str:
    """"scenes" (one narration per scene), "narration" (one narration for the whole video) or "clips"."""
    if not tracks:
        return "clips"
    clip_scenes = {clip.scene_index for clip in clips}
    if None not in clip_scenes and {track.scene_index for track in tracks} & clip_scenes:
        return "scenes"
    return "narration"


def build_command(ffmpeg: str, clips: list[Clip], tracks: list[Track], *, subtitles: bool,
                  style: str | None = None, music: Track | None = None, music_volume: float = 0.15) -> list[str]:
    """The ffmpeg argument list; it runs in the render's folder, where subtitles and output use fixed names."""
    width, height = frame_size(clips)
    total = sum(clip.duration for clip in clips)
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    for clip in clips:
        if clip.still:
            # One picture repeated as a video stream for exactly the clip's length.
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{clip.duration:.3f}"]
        args += ["-i", str(clip.path)]
    for track in tracks:
        args += ["-i", str(track.path)]
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music.path)]
    graph = []
    for number, clip in enumerate(clips):
        graph.append(f"[{number}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS},format=yuv420p,"
                     f"trim=duration={clip.duration:.3f},setpts=PTS-STARTPTS[v{number}]")
    graph.append("".join(f"[v{number}]" for number in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[vcat]")
    video = "vcat"
    if subtitles:
        graph.append(f"[vcat]subtitles=filename={SUBTITLE_NAME}:force_style='{style}'[vout]")
        video = "vout"

    def fitted(source: str, label: str, seconds: float) -> str:
        return (f"[{source}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
                f"atrim=duration={seconds:.3f},asetpts=PTS-STARTPTS[{label}]")

    def silence(label: str, seconds: float) -> str:
        return f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={seconds:.3f}[{label}]"

    mode = audio_mode(clips, tracks)
    first_track = len(clips)
    # Without music the mix is the final audio; with music it is the bed the music goes under.
    mixed = "aout" if music is None else "abed"
    if mode == "narration":
        for offset in range(len(tracks)):
            graph.append(f"[{first_track + offset}:a]aresample=48000,"
                         f"aformat=sample_fmts=fltp:channel_layouts=stereo[t{offset}]")
        joined = "".join(f"[t{offset}]" for offset in range(len(tracks)))
        graph.append(f"{joined}concat=n={len(tracks)}:v=0:a=1,apad,atrim=duration={total:.3f}[{mixed}]")
    else:
        by_scene = {}
        for offset, track in enumerate(tracks):
            by_scene.setdefault(track.scene_index, first_track + offset)
        for number, clip in enumerate(clips):
            if mode == "scenes":
                source = by_scene.get(clip.scene_index)
                graph.append(fitted(f"{source}:a", f"a{number}", clip.duration) if source is not None
                             else silence(f"a{number}", clip.duration))
            else:
                graph.append(fitted(f"{number}:a", f"a{number}", clip.duration) if clip.has_audio
                             else silence(f"a{number}", clip.duration))
        graph.append("".join(f"[a{number}]" for number in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1[{mixed}]")
    if music is not None:
        volume = min(1.0, max(0.01, music_volume))
        graph.append(f"[{len(clips) + len(tracks)}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={volume:.2f},atrim=duration={total:.3f},asetpts=PTS-STARTPTS[music]")
        # amix halves both inputs; doubling afterwards keeps the narration at its own level.
        graph.append("[abed][music]amix=inputs=2:duration=first:dropout_transition=0,volume=2[aout]")
    return args + ["-filter_complex", ";".join(graph), "-map", f"[{video}]", "-map", "[aout]",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                   "-t", f"{total:.3f}", OUTPUT_NAME]


def clip_command(ffmpeg: str, source: Path, start: float, end: float, output: str, *, copy: bool) -> list[str]:
    """Cut ``[start, end)`` seconds of ``source`` into ``output`` (a name inside the working folder).

    Stream copy keeps the original encoding and is fast, but starts on the
    nearest earlier keyframe; the re-encode is frame-accurate H.264/AAC. Only the
    first video and (if present) first audio stream are kept, so no subtitle,
    data or attachment stream of the source is carried over.
    """
    duration = f"{max(0.1, end - start):.3f}"
    command = [ffmpeg, "-nostdin", "-hide_banner", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(source),
               "-t", duration, "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
    if copy:
        command += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    return command + ["-movflags", "+faststart", output]


def safe_message(stderr: str | None) -> str:
    """The end of FFmpeg's error output, without file paths, for the step and the inspector."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    text = " ".join(lines[-3:]) if lines else "ffmpeg failed without a message"
    return _PATH.sub("[file]", text)[:300]


def run_ffmpeg(args: list[str], folder: Path, timeout: int, run: Runner = subprocess.run) -> None:
    try:
        done = run(args, cwd=str(folder), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("render_timeout", f"FFmpeg did not finish within {timeout} seconds", "timeout") from exc
    except OSError as exc:
        raise RenderError("ffmpeg_missing", "FFmpeg could not be started", "configuration_error") from exc
    if done.returncode != 0:
        raise RenderError("render_failed", safe_message(done.stderr))
