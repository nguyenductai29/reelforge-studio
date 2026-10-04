"""Clip Selector: short excerpts of the movie for each narrated section (local, free, no model call).

Each review section carries ``source_ranges`` (the moments of the movie it talks about) and is narrated for a
known time (its Voice file, else its words at 2.5 per second). The selector fills that time with excerpts of
``min_seconds`` to ``max_seconds`` (2–8 s by default), taken from the section's own ranges first, in their order,
then from the timeline windows that match the section's words, then from the matching position in the movie:

* an excerpt never overlaps one already chosen (no scene is repeated);
* the clip count is bounded (``max_clips``), the excerpts of one section stay in movie order;
* the total is kept under ``max_share`` of the movie when the narration allows it, and reported when it does not;
* a clip is longer than ``max_seconds`` only when the clip budget would otherwise leave narration uncovered.

The product favours short, commentary-led excerpts; it does not decide whether a use is lawful: the user remains
responsible for the source and for what they publish.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

WORDS_PER_SECOND = 2.5
TAIL_SECONDS = 0.4
_WORD = re.compile(r"\w{3,}", re.UNICODE)


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
        else None


def _index(section: Mapping[str, Any], position: int) -> int:
    value = section.get("index")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else position


def narration_seconds(section: Mapping[str, Any], spoken: Mapping[int, float] | None, index: int) -> float:
    """How long the section is narrated: its Voice file when known, else its words at 2.5 per second."""
    known = (spoken or {}).get(index)
    if known and known > 0:
        return float(known)
    words = len(str(section.get("text") or "").split())
    return words / WORDS_PER_SECOND


def _ranges(section: Mapping[str, Any], duration: float) -> list[tuple[float, float]]:
    found = []
    for item in section.get("source_ranges") or []:
        if not isinstance(item, Mapping):
            continue
        start, end = _number(item.get("start")), _number(item.get("end"))
        if start is None:
            continue
        start = min(max(0.0, start), max(0.0, duration - 0.5))
        end = min(duration, end) if end is not None and end > start else min(duration, start + 4.0)
        found.append((start, end))
    return found


def _words(text: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text or "")}


def _matching_windows(section: Mapping[str, Any], windows: Iterable[Mapping[str, Any]]) -> list[tuple[float, float]]:
    """Timeline windows that share the most words with the section (ties: the more important window)."""
    wanted = _words(str(section.get("text") or ""))
    scored = []
    for window in windows or []:
        start, end = _number(window.get("start")), _number(window.get("end"))
        if start is None or end is None or end <= start:
            continue
        shared = len(wanted & _words(f"{window.get('dialogue') or ''} {window.get('visual') or ''}"))
        if shared:
            scored.append((shared, _number(window.get("importance")) or 0.0, start, end))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [(start, end) for _, _, start, end in scored[:3]]


def _overlap(start: float, end: float, used: list[tuple[float, float]]) -> tuple[float, float] | None:
    for other_start, other_end in used:
        if start < other_end and other_start < end:
            return other_start, other_end
    return None


def select(sections: list[Mapping[str, Any]], *, duration: float, movie_source_id: str,
           spoken: Mapping[int, float] | None = None, windows: Iterable[Mapping[str, Any]] = (),
           min_seconds: float = 2.0, max_seconds: float = 8.0, max_clips: int = 30, max_share: float = 0.25,
           padding: float = 0.3, content_type: str | None = None) -> dict:
    """The clips to cut, in section order, and how well each section is covered."""
    duration = float(duration or 0)
    sections = [section for section in sections if isinstance(section, Mapping) and section.get("text")]
    if duration <= 1 or not sections:
        return {"clips": [], "coverage": [], "total_seconds": 0.0, "share": 0.0, "warnings": ["nothing_to_select"]}
    min_seconds = max(0.5, min(float(min_seconds), duration))
    max_seconds = max(min_seconds, float(max_seconds))
    windows = list(windows or [])
    plans = []
    for position, section in enumerate(sections, 1):
        index = _index(section, position)
        needed = max(min_seconds, narration_seconds(section, spoken, index) + TAIL_SECONDS)
        plans.append({"index": index, "section": section, "needed": needed,
                      "slots": max(1, math.ceil(needed / max_seconds))})
    total_needed = sum(plan["needed"] for plan in plans)
    if sum(plan["slots"] for plan in plans) > max_clips:
        # Fewer, longer clips: each section keeps its share of the budget, at least one clip.
        for plan in plans:
            plan["slots"] = max(1, math.floor(max_clips * plan["needed"] / total_needed))
        while sum(plan["slots"] for plan in plans) > max_clips:
            widest = max((plan for plan in plans if plan["slots"] > 1), key=lambda plan: plan["slots"], default=None)
            if widest is None:
                break
            widest["slots"] -= 1
    used: list[tuple[float, float]] = []
    clips, coverage, warnings = [], [], []
    for number, plan in enumerate(plans):
        section, index = plan["section"], plan["index"]
        length = min(duration, max(min_seconds, plan["needed"] / plan["slots"]))
        anchors = [(start, end, "source_range") for start, end in _ranges(section, duration)]
        anchors += [(start, end, "timeline_match") for start, end in _matching_windows(section, windows)]
        relative = duration * (number + 0.5) / len(plans)
        anchors.append((max(0.0, relative - length / 2), min(duration, relative + length / 2), "position_fallback"))
        chosen: list[tuple[float, float, str]] = []
        for anchor_start, anchor_end, reason in anchors:
            cursor = max(0.0, anchor_start - padding)
            while len(chosen) < plan["slots"] and cursor < max(anchor_end, anchor_start + length):
                start, end = cursor, min(duration, cursor + length)
                if end - start < min_seconds:
                    start = max(0.0, end - length)
                clash = _overlap(start, end, used)
                if clash is not None:
                    cursor = max(cursor, clash[1]) + 0.1  # always forward: the loop ends
                    continue
                chosen.append((round(start, 3), round(end, 3), reason))
                used.append((start, end))
                cursor = max(cursor + 0.1, end)
            if len(chosen) >= plan["slots"]:
                break
        # Still short (every anchor already used): continue forward through the movie, then from its start.
        cursor = chosen[-1][1] if chosen else relative
        for _ in range(4 * plan["slots"] + 8):
            if len(chosen) >= plan["slots"]:
                break
            if cursor + min_seconds > duration:
                cursor = 0.0
            start, end = cursor, min(duration, cursor + length)
            clash = _overlap(start, end, used)
            if clash is not None:
                cursor = clash[1] + 0.1
                continue
            chosen.append((round(start, 3), round(end, 3), "continuation"))
            used.append((start, end))
            cursor = end
        chosen.sort()
        covered = sum(end - start for start, end, _ in chosen)
        if covered + 0.05 < plan["needed"]:
            warnings.append(f"section_{index}_short")
        if length > max_seconds:
            warnings.append(f"section_{index}_long_clips")
        coverage.append({"scene_index": index, "needed": round(plan["needed"], 2), "covered": round(covered, 2),
                         "clips": len(chosen)})
        for start, end, reason in chosen:
            clips.append({"scene_index": index, "movie_source_id": movie_source_id, "start": start, "end": end,
                          "reason": reason, "text": str(section.get("text") or "")[:300],
                          **({"content_type": content_type} if content_type else {})})
    total = round(sum(clip["end"] - clip["start"] for clip in clips), 3)
    share = round(total / duration, 4)
    if share > max_share:
        warnings.append("share_exceeded")
    return {"clips": clips[:max(max_clips, len(plans))], "coverage": coverage, "total_seconds": total, "share": share,
            "warnings": warnings}
