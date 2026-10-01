"""Movie Recap steps (Phase 11): analyse a story, write a recap, find and cut the matching source clips.

* **Story Analysis** (``story_analysis``): one JSON text generation over a source or
  transcript (with its timestamps) → title, characters, plot points, acts,
  important moments and themes.
* **Recap Script** (``recap_script``): one JSON text generation → a narrated script
  split into scenes. Each scene carries a short ``source_quote`` (original dialogue)
  or ``moment`` so Match Source Scenes can find it in the transcript.
* **Match Source Scenes** (``match_scenes``): local and free; one source clip per
  scene by transcript similarity (``app/scene_matching.py``).
* **Extract Source Clips** (``extract_clips``): local and free; cuts each clip from
  the source video with FFmpeg in the render worker (stream copy first, H.264/AAC
  re-encode when copying is not possible) and stores each one as an asset that
  records the video it came from (``assets.source_asset_id``). Render joins them
  like generated clips, one per scene.

Use only content you are authorized to use. Nothing here downloads protected
streams, removes watermarks or bypasses DRM; the source must be a file the
workspace uploaded itself.
"""
import json
import math
import re
from typing import Any

from sqlalchemy import select

from app import render
from app import storage
from app.media_paths import asset_path
from app.models import Asset
from app.scene_matching import match_scenes, segments_from
from app.workflow.config import INTEGER, NUMBER, SELECT, ConfigField, advanced
from app.workflow.nodes.base import NodeHandler
from app.workflow.nodes.text import (INSTRUCTIONS, LANGUAGE, MAX_SOURCE_CHARS, MODEL, PLATFORM, TEMPERATURE, TONE,
                                     WORDS_PER_SECOND, TextNodeHandler, language_name, max_tokens_field)
from app.workflow.ports import SCENES, SOURCE, SOURCE_CLIPS, STORY, TEXT, VIDEO_ASSETS, InputPort, OutputPort
from app.workflow.results import JobRequest, NodeError, NodeExecutionResult, NodeReadiness

RECAP_STYLES = {"summary": "a concise plot summary", "storytelling": "gripping storytelling that builds suspense",
                "review": "a review that recaps the plot and gives a short verdict",
                "explainer": "an explainer that makes the plot and its twists easy to follow"}
SPOILER_LEVELS = {"none": "Do not reveal twists or the ending; stop before the climax.",
                  "light": "Hint at the twists but do not reveal the ending.",
                  "full": "Cover the whole story, including the twists and the ending."}
MAX_CLIPS = 20
MAX_CLIP_SECONDS = 120.0
VIDEO_TYPES = ("video/mp4", "video/webm")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")

NO_SOURCE_DETAIL = "Chưa nối nguồn hoặc transcript cho bước này."
MATCHED_DETAIL = "Đã ghép cảnh với nguồn."
NO_SEGMENTS_DETAIL = "Transcript chưa có mốc thời gian; nối bước Phiên âm hoặc tệp SRT/VTT."
NO_VIDEO_DETAIL = "Chưa có video nguồn để cắt clip; nối Uploaded Media Source (video)."
MATCH_READY_DETAIL = "Ghép cảnh chạy trên máy chủ, miễn phí."
CLIPS_QUEUED_DETAIL = "Đã xếp hàng cắt clip từ video nguồn."
CLIPS_MISSING_DETAIL = "Chưa có clip nguồn để cắt; nối bước Match Source Scenes."
CLIPS_INVALID_DETAIL = "Danh sách clip nguồn không hợp lệ."
SOURCE_MISSING_DETAIL = "Không tìm thấy video nguồn trong kho media."
STORAGE_FULL_DETAIL = "Kho media của workspace đã đầy; chưa cắt được clip."
CLIPS_READY_DETAIL = "Sẵn sàng cắt clip bằng FFmpeg (miễn phí). Chỉ dùng nội dung bạn có quyền sử dụng."


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(_FENCE.sub("", text or ""))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _text(value, limit=500) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _seconds(value) -> float | None:
    return round(float(value), 3) if (isinstance(value, (int, float)) and not isinstance(value, bool)
                                      and math.isfinite(value) and value >= 0) else None


def _items(value, limit) -> list:
    return value[:limit] if isinstance(value, list) else []


def parse_story(text: str) -> dict[str, Any]:
    """The analysis with every field present and bounded; a reply that is not JSON keeps its text as the summary."""
    value = parse_json_object(text) or {}
    characters = [{"name": _text(item.get("name"), 100), "role": _text(item.get("role"), 100),
                   "description": _text(item.get("description"), 300)}
                  for item in _items(value.get("characters"), 20) if isinstance(item, dict) and _text(item.get("name"))]
    plot_points = [_text(item.get("description") if isinstance(item, dict) else item, 400)
                   for item in _items(value.get("plot_points"), 30)]
    acts = [{"name": _text(item.get("name"), 100), "summary": _text(item.get("summary"), 800)}
            for item in _items(value.get("acts"), 8) if isinstance(item, dict)]
    moments = [{"description": _text(item.get("description"), 400), "quote": _text(item.get("quote"), 300),
                "start": _seconds(item.get("start")), "end": _seconds(item.get("end"))}
               for item in _items(value.get("important_moments"), 30) if isinstance(item, dict)]
    return {"title": _text(value.get("title"), 200),
            "summary": _text(value.get("summary"), 2000) or ("" if value else _text(text, 2000)),
            "characters": characters, "plot_points": [point for point in plot_points if point], "acts": acts,
            "important_moments": [moment for moment in moments if moment["description"] or moment["quote"]],
            "themes": [theme for theme in (_text(item, 100) for item in _items(value.get("themes"), 12)) if theme]}


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else \
        f"{seconds // 60:02d}:{seconds % 60:02d}"


def source_prompt_text(value) -> str:
    """A source as prompt text: timed transcript lines when it has segments, else its text; capped."""
    if isinstance(value, str):
        return value.strip()[:MAX_SOURCE_CHARS]
    if not isinstance(value, dict):
        return ""
    segments = segments_from(value.get("segments"))
    if segments:
        lines, size = [], 0
        for segment in segments:
            line = f"[{_clock(segment.start)}] {segment.text}"
            size += len(line) + 1
            if size > MAX_SOURCE_CHARS:
                break
            lines.append(line)
        return "\n".join(lines)
    return (value.get("text") or "").strip()[:MAX_SOURCE_CHARS] if isinstance(value.get("text"), str) else ""


class StoryAnalysisNodeHandler(TextNodeHandler):
    node_type = "story_analysis"
    inputs = (InputPort("source", (SOURCE, TEXT)),)
    outputs = (OutputPort("analysis", STORY, extract=lambda output: output.get("analysis") or None),
               OutputPort("text", TEXT, keys=("summary",)))
    requires = (("source",),)
    missing_input_detail = NO_SOURCE_DETAIL
    response_format = "json"
    config_fields = (LANGUAGE, MODEL, advanced(INSTRUCTIONS), TEMPERATURE, max_tokens_field(4096))
    system_prompt = ("You analyse the story of a film or video from its transcript or text. Reply with one JSON "
                     "object and nothing else. Never invent events that are not in the source.")

    def build_prompt(self, context, config, inputs):
        source = source_prompt_text(inputs.get("source"))
        if not source:
            return None
        lines = [f"Analyse the story below. Write every description in "
                 f"{language_name(self.language(context, config))}; keep quotes in their original language.",
                 'Return exactly: {"title": string, "summary": string, '
                 '"characters": [{"name": string, "role": string, "description": string}], '
                 '"plot_points": [string], "acts": [{"name": string, "summary": string}], '
                 '"important_moments": [{"description": string, "quote": string, "start": seconds, "end": seconds}], '
                 '"themes": [string]}.',
                 "plot_points are in story order. important_moments quote the exact line of dialogue when there is "
                 "one and give its time in seconds when the source shows timestamps like [mm:ss].",
                 *self.extras(config), self.quoted("Source", source)]
        return "\n".join(lines)

    def output_from(self, payload, result):
        analysis = parse_story(result.text)
        return {"analysis": analysis, "summary": analysis["summary"], "title": analysis["title"],
                "text": result.text, "provider": result.provider, "model": result.model,
                "usage": result.usage.as_dict(), "language": payload.get("language")}


def parse_recap(text: str, scene_count: int) -> dict[str, Any]:
    """Title, joined narration and scenes ``[{index, text, source_quote, moment, visual_prompt}]``."""
    value = parse_json_object(text) or {}
    scenes = []
    for item in _items(value.get("scenes"), scene_count):
        if not isinstance(item, dict) or not _text(item.get("text"), 2000):
            continue
        narration = _text(item.get("text"), 2000)
        moment = _text(item.get("moment"), 400)
        scenes.append({"index": len(scenes) + 1, "text": narration, "source_quote": _text(item.get("source_quote"), 300),
                       "moment": moment, "visual_prompt": moment or narration[:400],
                       "duration": round(len(narration.split()) / WORDS_PER_SECOND, 1)})
    if not scenes:
        paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", text or "") if part.strip()]
        scenes = [{"index": index, "text": part[:2000], "source_quote": "", "moment": "", "visual_prompt": part[:400],
                   "duration": round(len(part.split()) / WORDS_PER_SECOND, 1)}
                  for index, part in enumerate(paragraphs[:scene_count], 1)]
    return {"title": _text(value.get("title"), 200), "scenes": scenes,
            "script": "\n\n".join(scene["text"] for scene in scenes)}


class RecapScriptNodeHandler(TextNodeHandler):
    node_type = "recap_script"
    inputs = (InputPort("analysis", (STORY,)), InputPort("source", (SOURCE, TEXT)))
    outputs = (OutputPort("script", TEXT, keys=("script",)), OutputPort("scenes", SCENES, keys=("scenes",)),
               OutputPort("title", TEXT, keys=("title",)))
    requires = (("analysis", "source"),)
    missing_input_detail = NO_SOURCE_DETAIL
    response_format = "json"
    config_fields = (
        LANGUAGE,
        ConfigField("style", SELECT, default="storytelling", options=tuple(RECAP_STYLES), label="recap_style",
                    code="invalid_style"),
        ConfigField("duration", INTEGER, default=60, minimum=15, maximum=900, presets=(30, 60, 90, 180, 300),
                    label="target_duration", code="invalid_duration"),
        ConfigField("spoiler_level", SELECT, default="full", options=tuple(SPOILER_LEVELS), code="invalid_spoiler"),
        ConfigField("scene_count", INTEGER, default=6, minimum=2, maximum=MAX_CLIPS, code="invalid_count"),
        TONE, PLATFORM, INSTRUCTIONS, MODEL, TEMPERATURE, max_tokens_field(4096),
    )
    system_prompt = ("You write narrated recap scripts for short social videos about films and shows. Reply with "
                     "one JSON object and nothing else. Stay faithful to the source; never invent events.")

    def build_prompt(self, context, config, inputs):
        analysis = inputs.get("analysis") if isinstance(inputs.get("analysis"), dict) else None
        source = source_prompt_text(inputs.get("source"))
        if not analysis and not source:
            return None
        seconds = config["duration"]
        lines = [f"Write a recap script in {language_name(self.language(context, config))} as "
                 f"{RECAP_STYLES[config['style']]}.",
                 f"Target spoken length: about {seconds} seconds (roughly {round(seconds * WORDS_PER_SECOND)} words), "
                 f"split into exactly {config['scene_count']} scenes in story order.",
                 SPOILER_LEVELS[config["spoiler_level"]],
                 'Return exactly: {"title": string, "scenes": [{"text": string, "source_quote": string, '
                 '"moment": string}]}.',
                 "text: the narration of the scene. source_quote: one short line of dialogue copied exactly from the "
                 "source (original language) that happens in this scene, or an empty string. moment: one sentence "
                 "describing what is on screen.",
                 *self.extras(config)]
        if analysis:
            lines.append(self.quoted("Story analysis", json.dumps(analysis, ensure_ascii=False)[:20000]))
        if source:
            lines.append(self.quoted("Source", source))
        return "\n".join(lines)

    def output_from(self, payload, result):
        recap = parse_recap(result.text, MAX_CLIPS)
        return {**recap, "text": result.text, "provider": result.provider, "model": result.model,
                "usage": result.usage.as_dict(), "language": payload.get("language")}


def _video_entry(value):
    for entry in value if isinstance(value, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            return entry
    return None


class MatchScenesNodeHandler(NodeHandler):
    node_type = "match_scenes"
    inputs = (InputPort("scenes", (SCENES,), multiple=True), InputPort("transcript", (SOURCE,)),
              InputPort("video", (VIDEO_ASSETS,)))
    outputs = (OutputPort("source_clips", SOURCE_CLIPS, keys=("source_clips",)),)
    requires = (("scenes",), ("transcript",))
    missing_input_detail = NO_SOURCE_DETAIL
    config_fields = (
        ConfigField("min_seconds", NUMBER, default=3, minimum=1, maximum=30, label="clip_min_seconds",
                    code="invalid_duration"),
        ConfigField("max_seconds", NUMBER, default=12, minimum=2, maximum=60, label="clip_max_seconds",
                    code="invalid_duration"),
        advanced(ConfigField("min_confidence", NUMBER, default=0.2, minimum=0, maximum=1, code="invalid_confidence")),
        advanced(ConfigField("padding", NUMBER, default=0.3, minimum=0, maximum=5, label="clip_padding",
                             code="invalid_duration")),
    )

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        transcript = inputs.get("transcript") if isinstance(inputs.get("transcript"), dict) else {}
        segments = segments_from(transcript.get("segments"))
        if not segments:
            return NodeExecutionResult.blocked(NO_SEGMENTS_DETAIL, NodeError("missing_timestamps", "No segments"))
        video = _video_entry(inputs.get("video"))
        asset_id = video["id"] if video else (transcript.get("asset_id") if transcript.get("source_type") in (
            "video", "transcript") else None)
        asset = context.db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == context.workspace.id)
                                  ) if isinstance(asset_id, str) else None
        if asset is None or asset.content_type not in VIDEO_TYPES:
            return NodeExecutionResult.blocked(NO_VIDEO_DETAIL, NodeError("missing_input", "No source video"))
        metadata = transcript.get("metadata") if isinstance(transcript.get("metadata"), dict) else {}
        duration = metadata.get("duration") if isinstance(metadata.get("duration"), (int, float)) else None
        clips = match_scenes(inputs.get("scenes") or [], segments, source_asset_id=asset.id,
                             min_seconds=config["min_seconds"], max_seconds=max(config["max_seconds"],
                                                                                config["min_seconds"]),
                             min_confidence=config["min_confidence"], padding=config["padding"], duration=duration)
        if not clips:
            return NodeExecutionResult.blocked(NO_SOURCE_DETAIL, NodeError("missing_input", "No scenes to match"))
        matched = sum(1 for clip in clips if clip["reason"] == "transcript_match")
        return NodeExecutionResult.completed(f"{MATCHED_DETAIL} ({matched}/{len(clips)} theo lời thoại)",
                                             {"source_clips": clips[:MAX_CLIPS], "matched": matched,
                                              "source_asset_id": asset.id})

    def readiness(self, context, node):
        return NodeReadiness("configured", MATCH_READY_DETAIL)


def _clips(value) -> list[dict] | None:
    """The clips to cut, or ``None`` when any of them is malformed."""
    clips = []
    for position, item in enumerate(value if isinstance(value, list) else [], 1):
        start, end = _seconds(item.get("start")) if isinstance(item, dict) else None, \
            _seconds(item.get("end")) if isinstance(item, dict) else None
        if (start is None or end is None or not isinstance(item.get("source_asset_id"), str)
                or not 0.2 <= end - start <= MAX_CLIP_SECONDS):
            return None
        scene = item.get("scene_index")
        clips.append({"source_asset_id": item["source_asset_id"], "start": start, "end": end,
                      "scene_index": scene if isinstance(scene, int) and not isinstance(scene, bool) else position})
    return clips


def _extracted(output):
    assets = output.get("video_assets")
    return assets if isinstance(assets, list) and assets else None


class ExtractClipsNodeHandler(NodeHandler):
    node_type = "extract_clips"
    inputs = (InputPort("source_clips", (SOURCE_CLIPS,), multiple=True),)
    outputs = (OutputPort("video_assets", VIDEO_ASSETS, extract=_extracted),)
    requires = (("source_clips",),)
    missing_input_detail = CLIPS_MISSING_DETAIL
    config_fields = (
        ConfigField("cut_mode", SELECT, default="copy_first", options=("copy_first", "reencode"),
                    code="invalid_cut_mode"),
    )

    def execute(self, context, node, inputs):
        clips = _clips(inputs.get("source_clips"))
        if not clips:
            return NodeExecutionResult.blocked(CLIPS_INVALID_DETAIL if clips is None else CLIPS_MISSING_DETAIL,
                                               NodeError("invalid_request", "Invalid source clips"))
        if len(clips) > MAX_CLIPS:
            return NodeExecutionResult.blocked(CLIPS_INVALID_DETAIL, NodeError("invalid_request", "Too many clips"))
        db, workspace_id = context.db, context.workspace.id
        ids = {clip["source_asset_id"] for clip in clips}
        rows = {row.id: row for row in db.scalars(select(Asset).where(Asset.workspace_id == workspace_id,
                                                                       Asset.id.in_(ids)))}
        if any(rows.get(asset_id) is None or rows[asset_id].content_type not in VIDEO_TYPES
               or not asset_path(db, workspace_id, asset_id).is_file() for asset_id in ids):
            return NodeExecutionResult.blocked(SOURCE_MISSING_DETAIL, NodeError("input_missing", "Source missing"))
        if issue := render.tools_issue():
            return NodeExecutionResult.blocked(issue[1], NodeError(issue[0], issue[1]))
        if storage.is_full(db, workspace_id):
            return NodeExecutionResult.blocked(STORAGE_FULL_DETAIL, NodeError("storage_limit_exceeded", "Storage full"))
        step = context.step_for(node)
        config = self.config_values(inputs.config)
        payload = {"kind": "clips.extract", "node_type": self.node_type, "node_id": node["id"], "provider": "ffmpeg",
                   "model": "local", "mode": config["cut_mode"],
                   "clips": [{**clip, "content_type": rows[clip["source_asset_id"]].content_type} for clip in clips]}
        return NodeExecutionResult.queued(CLIPS_QUEUED_DETAIL,
                                          JobRequest("render", payload, logical_key=f"render:{step.id}:clips"),
                                          {"expected": len(clips)})

    def readiness(self, context, node):
        if issue := render.tools_issue():
            return NodeReadiness(issue[0], issue[1], code=issue[0])
        return NodeReadiness("configured", CLIPS_READY_DETAIL)


RECAP_HANDLERS = (StoryAnalysisNodeHandler, RecapScriptNodeHandler, MatchScenesNodeHandler, ExtractClipsNodeHandler)
