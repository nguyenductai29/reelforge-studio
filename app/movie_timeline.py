"""The unified movie timeline: dialogue and what is on screen, window by window (local, free, no model call).

Prepare Movie samples frames, Transcript gives timed dialogue and Visual Analysis describes the frames. This
module joins them into fixed windows (10 s for a short film, wider for a long one, so a two-hour movie stays a
few hundred windows), keeps each window's text short, and fits the whole timeline into a character budget by
widening the windows when needed. The text model therefore reads the story from bounded, timed notes instead
of the raw movie or a transcript that would be cut off before the ending.

The timeline is also offered as a ``source`` value (``source_type`` ``timeline``), whose segments read
``Dialogue: … | On screen: …``: Story Analysis consumes it like any transcript.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

MAX_WINDOWS = 400
BUDGET_CHARS = 45_000
DIALOGUE_CHARS = 420
VISUAL_CHARS = 260


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
        else None


def _text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else \
        f"{seconds // 60:02d}:{seconds % 60:02d}"


def auto_window(duration: float) -> int:
    """10 s up to ~66 minutes, then wider in steps of 5 s so that at most ``MAX_WINDOWS`` windows remain."""
    return max(10, int(math.ceil(duration / MAX_WINDOWS / 5.0) * 5))


def _segments(transcript: Mapping[str, Any] | None) -> list[tuple[float, float, str]]:
    found = []
    for item in (transcript or {}).get("segments") or []:
        if not isinstance(item, Mapping):
            continue
        start, end, text = _number(item.get("start")), _number(item.get("end")), _text(item.get("text"), 600)
        if start is None or not text:
            continue
        found.append((start, end if end is not None and end >= start else start, text))
    return sorted(found)


def _notes(visual: Mapping[str, Any] | None) -> list[dict]:
    notes = []
    for item in (visual or {}).get("frames") or []:
        if not isinstance(item, Mapping) or _number(item.get("time")) is None:
            continue
        importance = _number(item.get("importance"))
        notes.append({"time": float(item["time"]), "description": _text(item.get("description"), 300),
                      "action": _text(item.get("action"), 160), "location": _text(item.get("location"), 100),
                      "characters": [_text(name, 60) for name in (item.get("characters") or [])
                                     if isinstance(name, str) and name.strip()][:4],
                      "importance": min(1.0, max(0.0, importance)) if importance is not None else None})
    return sorted(notes, key=lambda note: note["time"])


def _visual_line(note: dict) -> str:
    parts = [note["description"] or note["action"]]
    if note["location"]:
        parts.append(f"({note['location']})")
    return " ".join(part for part in parts if part)


def build(transcript: Mapping[str, Any] | None, visual: Mapping[str, Any] | None, *, duration: float | None = None,
          window: int | None = None, cuts: Iterable[float] = (), budget: int = BUDGET_CHARS) -> dict:
    """``{"duration", "window_seconds", "windows": [{start, end, dialogue, visual, importance, cut}], ...}``."""
    segments, notes = _segments(transcript), _notes(visual)
    cut_times = sorted(float(cut) for cut in cuts if _number(cut) is not None)
    known = [end for _, end, _ in segments] + [note["time"] for note in notes] + cut_times
    total = _number(duration) or (max(known) if known else 0.0)
    if total <= 0:
        return {"duration": 0, "window_seconds": 0, "windows": [], "dialogue_segments": len(segments),
                "visual_notes": len(notes), "truncated": False}
    size = max(5, int(window)) if window else auto_window(total)
    dialogue_limit, visual_limit = DIALOGUE_CHARS, VISUAL_CHARS
    while True:
        count = max(1, math.ceil(total / size))
        buckets = [{"start": round(index * size, 3), "end": round(min(total, (index + 1) * size), 3),
                    "dialogue": [], "visual": [], "importance": None, "cut": False} for index in range(count)]
        for start, end, text in segments:
            index = min(count - 1, int(((start + end) / 2) // size))
            buckets[index]["dialogue"].append(text)
        for note in notes:
            bucket = buckets[min(count - 1, int(note["time"] // size))]
            if line := _visual_line(note):
                bucket["visual"].append(line)
            if note["importance"] is not None:
                bucket["importance"] = max(bucket["importance"] or 0.0, note["importance"])
        for cut in cut_times:
            buckets[min(count - 1, int(cut // size))]["cut"] = True
        windows = []
        for bucket in buckets:
            dialogue = _text(" ".join(bucket["dialogue"]), dialogue_limit)
            visual = _text("; ".join(dict.fromkeys(bucket["visual"])), visual_limit)
            importance = bucket["importance"] if bucket["importance"] is not None else (0.3 if dialogue else 0.0)
            windows.append({"start": bucket["start"], "end": bucket["end"], "dialogue": dialogue, "visual": visual,
                            "importance": round(importance, 2), "cut": bucket["cut"]})
        size_chars = sum(len(line) + 1 for line in lines(windows))
        if size_chars <= budget or (size >= total and dialogue_limit <= 120):
            return {"duration": round(total, 3), "window_seconds": size, "windows": windows,
                    "dialogue_segments": len(segments), "visual_notes": len(notes),
                    "truncated": size_chars > budget}
        # Too long for one prompt: wider windows first (down to 40), then shorter text per window, then wider
        # windows again until one window holds the movie (always ends: the size grows each time).
        if size < total / 40 or (dialogue_limit <= 120 and visual_limit <= 80):
            size = min(max(size + 1, int(math.ceil(total))), int(size * 1.5) + 1)
        else:
            dialogue_limit, visual_limit = max(120, int(dialogue_limit * 0.75)), max(80, int(visual_limit * 0.75))


def lines(windows: Iterable[Mapping[str, Any]]) -> list[str]:
    """One prompt line per window that has something in it: ``[mm:ss–mm:ss] Dialogue: … | On screen: …``."""
    found = []
    for window in windows:
        parts = []
        if window.get("dialogue"):
            parts.append(f"Dialogue: {window['dialogue']}")
        if window.get("visual"):
            parts.append(f"On screen: {window['visual']}")
        if not parts:
            continue
        marker = " [cut]" if window.get("cut") else ""
        found.append(f"[{_clock(window['start'])}–{_clock(window['end'])}]{marker} " + " | ".join(parts))
    return found


def prompt_text(timeline: Mapping[str, Any] | None, budget: int = BUDGET_CHARS) -> str:
    text, size = [], 0
    for line in lines((timeline or {}).get("windows") or []):
        size += len(line) + 1
        if size > budget:
            break
        text.append(line)
    return "\n".join(text)


def as_source(timeline: Mapping[str, Any], *, title: str = "", language: str | None = None,
              movie_source_id: str | None = None) -> dict:
    """The timeline as a ``source`` value: one segment per window with dialogue or visuals."""
    from app import sources

    segments = []
    for window in timeline.get("windows") or []:
        parts = []
        if window.get("dialogue"):
            parts.append(f"Dialogue: {window['dialogue']}")
        if window.get("visual"):
            parts.append(f"On screen: {window['visual']}")
        if parts:
            segments.append({"start": window["start"], "end": window["end"], "text": " | ".join(parts)})
    return sources.make_source("timeline", title=title, text="\n".join(segment["text"] for segment in segments),
                               language=language, segments=segments,
                               metadata={"duration": timeline.get("duration"), "movie_source_id": movie_source_id,
                                         "window_seconds": timeline.get("window_seconds")})
