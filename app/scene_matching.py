"""Match recap scenes to moments of the source video by transcript similarity (Movie Recap, Phase 11).

Everything is local and deterministic; no model is called. Each scene is
compared with every window of consecutive transcript segments (at most
``max_seconds`` long) using the words they share, weighted by how rare each word
is in the transcript (IDF), blended with character-trigram overlap so small
spelling differences still count. The scene's ``source_quote`` (a line of the
original dialogue the Recap Script step asked for) is used first, then its
``moment`` description, then its narration.

A scene whose best window scores below ``min_confidence`` falls back to the
moment at the same relative position in the source (reason
``position_fallback``, confidence 0), so every scene still gets a clip that a
person can review. Clip bounds are padded, and stretched to the narration's
estimated length so a scene's voice-over is never cut short by its clip.
"""
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping

WORDS_PER_SECOND = 2.5
MAX_WINDOW_SEGMENTS = 12
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def normalize(text: str) -> str:
    """Lowercase text without diacritics, so "Hà Nội" and "ha noi" compare equal."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


# Very common words carry no signal; IDF already discounts the rest.
_STOPWORD_TEXT = """a an and are as at be but by for from has have he her his i in is it its me my of on or
our she so that the their them they this to was we were what when which who will with you your
và là của có cho không một những các được này đó thì mà với trong khi đã sẽ anh em tôi"""
_STOPWORDS = frozenset(normalize(word) for word in _STOPWORD_TEXT.split())


def words(text: str) -> list[str]:
    return [word for word in _WORD.findall(normalize(text or "")) if len(word) > 1 and word not in _STOPWORDS]


def trigrams(text: str) -> set[str]:
    compact = " ".join(words(text))
    return {compact[index:index + 3] for index in range(max(0, len(compact) - 2))}


def segments_from(value: Any) -> list[Segment]:
    """Valid, time-ordered segments of a transcript (``{"start", "end", "text"}`` items)."""
    result = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        start, end, text = item.get("start"), item.get("end"), item.get("text")
        if (isinstance(start, (int, float)) and isinstance(end, (int, float)) and not isinstance(start, bool)
                and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end
                and isinstance(text, str) and text.strip()):
            result.append(Segment(float(start), float(end), text.strip()))
    return sorted(result, key=lambda segment: (segment.start, segment.end))


def estimate_seconds(text: str) -> float:
    return len((text or "").split()) / WORDS_PER_SECOND


def _query(scene: Mapping[str, Any]) -> str:
    for key in ("source_quote", "moment", "text"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


class _Index:
    def __init__(self, segments: list[Segment]):
        self.segments = segments
        self.words = [set(words(segment.text)) for segment in segments]
        self.grams = [trigrams(segment.text) for segment in segments]
        counts: dict[str, int] = {}
        for bag in self.words:
            for word in bag:
                counts[word] = counts.get(word, 0) + 1
        total = max(1, len(segments))
        self.idf = {word: math.log(1 + total / count) for word, count in counts.items()}
        self.default_idf = math.log(1 + total)

    def score(self, query_words: set[str], query_grams: set[str], window: set[str],
              grams: set[str]) -> tuple[float, list[str]]:
        if not query_words:
            return 0.0, []
        shared = query_words & window
        weight = sum(self.idf.get(word, self.default_idf) for word in query_words)
        recall = sum(self.idf.get(word, self.default_idf) for word in shared) / weight if weight else 0.0
        dice = 2 * len(query_grams & grams) / (len(query_grams) + len(grams)) if query_grams and grams else 0.0
        return 0.75 * recall + 0.25 * dice, sorted(shared, key=lambda word: (-self.idf.get(word, 0), word))[:8]


def _bounds(start: float, end: float, *, target: float, padding: float, max_seconds: float,
            duration: float | None) -> tuple[float, float]:
    start = max(0.0, start - padding)
    end = max(end + padding, start + target)
    end = min(end, start + max_seconds)
    if duration:
        end = min(end, duration)
        start = max(0.0, min(start, end - min(target, max_seconds)))
    return round(start, 3), round(max(end, start + 0.5), 3)


def match_scenes(scenes: Iterable[Mapping[str, Any]], segments: list[Segment], *, source_asset_id: str,
                 min_seconds: float = 3.0, max_seconds: float = 12.0, min_confidence: float = 0.2,
                 padding: float = 0.3, duration: float | None = None) -> list[dict[str, Any]]:
    """One source clip per scene, in scene order (see the module docstring)."""
    scenes = [scene for scene in scenes if isinstance(scene, Mapping)]
    index = _Index(segments)
    duration = duration or (segments[-1].end if segments else None)
    clips = []
    for position, scene in enumerate(scenes, 1):
        scene_index = scene.get("index") if isinstance(scene.get("index"), int) and not isinstance(
            scene.get("index"), bool) else position
        target = min(max(estimate_seconds(scene.get("text") or "") + 0.5, min_seconds), max_seconds)
        query = _query(scene)
        query_words, query_grams = set(words(query)), trigrams(query)
        best = (0.0, [], None, None)
        # Only windows that start on a segment sharing a word can be best: a window
        # starting earlier adds nothing but length.
        for first in (position for position, bag in enumerate(index.words) if bag & query_words):
            window, grams = set(), set()
            for last in range(first, min(len(segments), first + MAX_WINDOW_SEGMENTS)):
                if segments[last].end - segments[first].start > max_seconds:
                    break
                window |= index.words[last]
                grams |= index.grams[last]
                score, shared = index.score(query_words, query_grams, window, grams)
                # A longer window must earn its extra length.
                score -= 0.01 * (last - first)
                if score > best[0]:
                    best = (score, shared, first, last)
        score, shared, first, last = best
        if first is not None and score >= min_confidence:
            start, end = _bounds(segments[first].start, segments[last].end, target=target, padding=padding,
                                 max_seconds=max_seconds, duration=duration)
            text = " ".join(segment.text for segment in segments[first:last + 1])
            clips.append({"scene_index": scene_index, "source_asset_id": source_asset_id, "start": start,
                          "end": end, "confidence": round(min(1.0, score), 3), "reason": "transcript_match",
                          "matched_words": shared, "text": text[:300]})
            continue
        if not duration:
            raise ValueError("The transcript has no timestamps to place scenes on")
        moment = duration * (position - 0.5) / max(1, len(scenes))
        nearest = min(segments, key=lambda segment: abs(segment.start - moment)) if segments else None
        start = nearest.start if nearest else moment
        bounds = _bounds(start, start, target=target, padding=0.0, max_seconds=max_seconds, duration=duration)
        clips.append({"scene_index": scene_index, "source_asset_id": source_asset_id, "start": bounds[0],
                      "end": bounds[1], "confidence": 0.0, "reason": "position_fallback", "matched_words": [],
                      "text": (nearest.text if nearest else "")[:300]})
    return clips
