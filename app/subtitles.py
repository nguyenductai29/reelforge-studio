"""Subtitle cues from scenes or text: deterministic, local and free, written as SRT or WebVTT.

Timing follows the best source available, in this order:

1. **audio**: narration durations. One narration per scene times its scene;
   one narration for the whole script is spread over the scenes in proportion
   to their estimated lengths.
2. **video**: the clip durations of the connected Video step, per scene.
3. **scenes**: each scene's own ``duration`` (the Scene Splitter's estimate).
4. **estimate**: plain text without scenes, at 2.5 words per second.

Scenes follow each other with no gap, in scene order; a scene without text
keeps its time but has no cue. Inside a scene, the text is wrapped into lines of
at most ``max_chars`` characters (words are kept whole unless a single word is
longer, as in Japanese text without spaces), grouped into cues of at most
``max_lines`` lines, and the scene's time is shared between its cues in
proportion to their length. No speech recognition or forced alignment is used.
"""
from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

FORMATS = {"srt": ("application/x-subrip", "srt"), "vtt": ("text/vtt", "vtt")}
CONTENT_TYPES = frozenset(content_type for content_type, _ in FORMATS.values())
STYLES = ("classic", "boxed", "bold")
FONT_SIZES = ("small", "medium", "large")
WORDS_PER_SECOND = 2.5
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class Cue:
    start: int  # milliseconds
    end: int
    text: str  # lines joined by "\n"
    scene_index: int | None = None


@dataclass(frozen=True)
class Segment:
    """The time one scene (or the whole text) occupies."""

    scene_index: int | None
    start: int
    end: int
    text: str
    source: str  # audio, video, scene or estimate


def clean(text: Any) -> str:
    """One line of plain text: control characters and line breaks become spaces; "-->" cannot end a cue early."""
    if not isinstance(text, str):
        return ""
    return " ".join(_CONTROL.sub(" ", text).split()).replace("-->", "→")


def _words(text: str) -> int:
    # Scripts without spaces (e.g. Japanese) are counted by characters, as the Scene Splitter does.
    return max(len(text.split()), len(text) // 6, 1)


def estimate_seconds(text: str) -> float:
    return max(1.0, _words(text) / WORDS_PER_SECOND)


def wrap(text: str, max_chars: int) -> list[str]:
    lines, line = [], ""
    for word in text.split(" "):
        while len(word) > max_chars:
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        if not word:
            continue
        candidate = f"{line} {word}" if line else word
        if len(candidate) <= max_chars:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _scene_index(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def durations_by_scene(entries: Any) -> dict[int, float]:
    """``scene_index → duration`` from media entries (audio or video assets) that carry both."""
    found: dict[int, float] = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, Mapping) and (index := _scene_index(entry.get("scene_index"))) is not None:
            if (seconds := _seconds(entry.get("duration"))) is not None:
                found.setdefault(index, seconds)
    return found


def whole_duration(entries: Any) -> float | None:
    """The total duration of entries without a scene (one narration or clip for everything), if all are known."""
    items = [entry for entry in entries if isinstance(entry, Mapping)] if isinstance(entries, list) else []
    items = [entry for entry in items if entry.get("scene_index") is None]
    values = [_seconds(entry.get("duration")) for entry in items]
    return sum(values) if values and all(value is not None for value in values) else None


def _scenes(scenes: Any) -> list[tuple[int, str, float]]:
    """(scene index, text, estimated seconds) per scene, in order; a missing or repeated index uses the position."""
    items, used = [], set()
    for position, scene in enumerate(scenes if isinstance(scenes, list) else [], 1):
        if not isinstance(scene, Mapping):
            continue
        index = _scene_index(scene.get("index"))
        if index is None or index in used:
            index = position
            while index in used:
                index += 1
        used.add(index)
        text = clean(scene.get("text"))
        items.append((index, text, _seconds(scene.get("duration")) or estimate_seconds(text or "x")))
    return items


def plan_scenes(scenes: Any, audio: Any = None, video: Any = None) -> tuple[list[Segment], str]:
    """Each scene's time span and the timing source, following the module docstring's order."""
    items = _scenes(scenes)
    by_audio, by_video = durations_by_scene(audio), durations_by_scene(video)
    narration = whole_duration(audio)
    spans: list[tuple[float, str]] = []
    if by_audio:
        timing = "audio"
        for index, _, estimate in items:
            if index in by_audio:
                spans.append((by_audio[index], "audio"))
            elif index in by_video:
                spans.append((by_video[index], "video"))
            else:
                spans.append((estimate, "scene"))
    elif narration:
        timing = "audio"
        scale = narration / sum(estimate for _, _, estimate in items) if items else 0
        spans = [(estimate * scale, "audio") for _, _, estimate in items]
    elif by_video:
        timing = "video"
        spans = [(by_video[index], "video") if index in by_video else (estimate, "scene")
                 for index, _, estimate in items]
    else:
        timing = "scenes"
        spans = [(estimate, "scene") for _, _, estimate in items]
    segments, clock = [], 0
    for (index, text, _), (seconds, source) in zip(items, spans):
        end = clock + max(1, round(seconds * 1000))
        segments.append(Segment(index, clock, end, text, source))
        clock = end
    return segments, timing


def plan_text(text: Any, audio: Any = None, video: Any = None) -> tuple[list[Segment], str]:
    """One span for plain text: the narration's length, else the video's, else a reading-speed estimate."""
    text = clean(text)
    if not text:
        return [], "estimate"
    narration = whole_duration(audio) or (sum(durations_by_scene(audio).values()) or None)
    footage = whole_duration(video) or (sum(durations_by_scene(video).values()) or None)
    if narration:
        seconds, timing = narration, "audio"
    elif footage:
        seconds, timing = footage, "video"
    else:
        seconds, timing = estimate_seconds(text), "estimate"
    return [Segment(None, 0, max(1, round(seconds * 1000)), text, timing)], timing


def build_cues(segments: list[Segment], *, max_chars: int = 42, max_lines: int = 2) -> list[Cue]:
    cues = []
    for segment in segments:
        lines = wrap(segment.text, max_chars)
        groups = [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]
        if not groups:
            continue
        weights = [sum(len(line) for line in group) for group in groups]
        total, span = sum(weights), segment.end - segment.start
        start, done = segment.start, 0
        for number, (group, weight) in enumerate(zip(groups, weights), 1):
            done += weight
            end = segment.end if number == len(groups) else segment.start + round(span * done / total)
            if end > start:
                cues.append(Cue(start, end, "\n".join(group), segment.scene_index))
                start = end
    return cues


def _stamp(ms: int, separator: str) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def to_srt(cues: list[Cue]) -> str:
    return "\n".join(f"{number}\n{_stamp(cue.start, ',')} --> {_stamp(cue.end, ',')}\n{cue.text}\n"
                     for number, cue in enumerate(cues, 1))


def _vtt_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(cues: list[Cue]) -> str:
    blocks = [f"{number}\n{_stamp(cue.start, '.')} --> {_stamp(cue.end, '.')}\n{_vtt_text(cue.text)}\n"
              for number, cue in enumerate(cues, 1)]
    return "WEBVTT\n\n" + "\n".join(blocks)


def render(cues: list[Cue], fmt: str) -> bytes:
    return (to_vtt(cues) if fmt == "vtt" else to_srt(cues)).encode("utf-8")


def burn_in_text(text: str) -> str:
    """Text for a subtitle burned in by FFmpeg/libass, where braces start override tags and "<…>" is markup."""
    return (text.replace("\\", "＼").replace("{", "(").replace("}", ")")
            .replace("<", "‹").replace(">", "›"))


def cue_dict(cue: Cue) -> dict:
    return {"start": cue.start / 1000, "end": cue.end / 1000, "text": cue.text, "scene_index": cue.scene_index}


def segment_dict(segment: Segment) -> dict:
    return {"scene_index": segment.scene_index, "start": segment.start / 1000, "end": segment.end / 1000,
            "source": segment.source}


def cues_from(value: Any) -> list[Cue]:
    """Cues read back from a step output; malformed entries are skipped."""
    cues = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str):
            continue
        start, end = _seconds(item.get("start")) or 0.0, _seconds(item.get("end"))
        if end is None or end <= start:
            continue
        cues.append(Cue(round(start * 1000), round(end * 1000), item["text"], _scene_index(item.get("scene_index"))))
    return cues
