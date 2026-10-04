"""Movie steps: an automatic Movie Recap, Movie Review or Ending Explained from a movie source (migration 0027).

::

    Movie Source → Prepare Movie ─audio─→ Transcript ─────────────┐
                        └──frames──→ Visual Analysis ─────────────┴→ Movie Timeline → Story Analysis
                                                                       └──────────────┬→ Review Script
    Review Script ─scenes─→ Voice ─audio─→ Clip Selector → Extract Source Clips → Render → Review → Publish

* **Movie Source** (``movie_source``, free, at once): records that the run uses the source (``movie_source_uses``),
  so it is not deleted while the run needs it.
* **Prepare Movie** (``movie_prepare``, free, movie worker): downloads the source from Google Drive once into the
  worker's scratch space, extracts mono 16 kHz audio (an intermediate asset, for Transcript) and samples frames
  every N seconds plus after scene cuts, within a budget.
* **Visual Analysis** (``visual_analysis``, paid, movie worker): a vision-capable Text model (Gemini, OpenAI or
  Anthropic) describes batches of frames; each batch is one job with its own credit reservation
  (``VISION_CREDITS_PER_BATCH``), charged once, refunded when the provider definitely refused it, held for
  reconciliation when the outcome is unknown. Nothing is identified beyond what the frames show.
* **Movie Timeline** (``movie_timeline``, free, at once): dialogue and visual notes window by window
  (``app/movie_timeline.py``); Story Analysis reads it as its source.
* **Review Script** (``review_script``, paid text): sections with the time ranges of the movie they talk about.
* **Clip Selector** (``clip_select``, free, at once): short excerpts per section (``app/clip_selection.py``).

Extract Source Clips, Voice, Subtitle, Render, Review and Publish are the existing steps. Use only movies you are
authorized to use; the result favours commentary over long excerpts but does not decide what is lawful.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app import clip_selection, movie_sources, movie_timeline, render, storage, system_config, usage
from app.models import CreditAccount
from app.providers.text import TEXT_PROVIDERS, TEXT_TASK, text_provider_config_issue
from app.workflow.config import INTEGER, MOVIE_SOURCE, NUMBER, SELECT, ConfigField, advanced
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, NodeHandler
from app.workflow.nodes.media import references
from app.workflow.nodes.recap import SPOILER_LEVELS, _items, _seconds, _text, parse_json_object
from app.workflow.nodes.text import (INSTRUCTIONS, LANGUAGE, MODEL, PLATFORM, TEMPERATURE, TONE, WORDS_PER_SECOND,
                                     TextNodeHandler, language_name, max_tokens_field)
from app.workflow.ports import (AUDIO_ASSETS, FRAMES, MOVIE, SCENES, SOURCE, SOURCE_CLIPS, STORY, TEXT, TIMELINE,
                                VISUAL, InputPort, OutputPort)
from app.workflow.results import JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError

MAX_FRAMES = 500
MAX_SECTIONS = 20
REVIEW_MODES = {
    "recap": "a recap that retells the story clearly, in order",
    "review": "a review: a short recap of the setup, then an opinion on what works and what does not, with a verdict",
    "ending_explained": "an explanation of the ending: what happens, what it means and how the story leads there",
}

MISSING_SOURCE_DETAIL = "Chọn nguồn phim cho bước này."
SOURCE_UNAVAILABLE_DETAIL = "Không tìm thấy nguồn phim trong studio."
SOURCE_DETAILS = {
    "source_not_ready": "Nguồn phim chưa sẵn sàng (đang nhập hoặc đang tải lên Drive).",
    "source_failed": "Nhập nguồn phim thất bại; thử lại việc nhập trước.",
    "source_expired": "Nguồn phim đã hết hạn hoặc đã bị xóa.",
}
SOURCE_USED_DETAIL = "Đã giữ nguồn phim cho lần chạy này."
SOURCE_READY_DETAIL = "Nguồn phim sẵn sàng."
PREPARE_QUEUED_DETAIL = "Đã xếp hàng chuẩn bị phim (tải về máy chủ, tách âm thanh, lấy khung hình)."
PREPARE_READY_DETAIL = "Chuẩn bị phim chạy trên máy chủ, miễn phí."
VISUAL_QUEUED_DETAIL = "Đã xếp hàng phân tích hình ảnh."
VISUAL_MISSING_TOOL_DETAIL = "Chọn model văn bản đọc được hình ảnh (Gemini, OpenAI hoặc Anthropic) trong Model AI."
VISUAL_TOOL_UNAVAILABLE_DETAIL = "Model đã chọn cho bước này không còn được bật; chọn model khác."
NO_FRAMES_DETAIL = "Chưa có khung hình; nối bước Chuẩn bị phim."
TIMELINE_DONE_DETAIL = "Đã dựng dòng thời gian của phim."
TIMELINE_EMPTY_DETAIL = "Chưa có lời thoại hay mô tả hình ảnh để dựng dòng thời gian."
CLIPS_SELECTED_DETAIL = "Đã chọn đoạn trích cho từng phần."
CLIPS_NONE_DETAIL = "Không chọn được đoạn trích nào; kiểm tra kịch bản và nguồn phim."
NO_MOVIE_DETAIL = "Chưa nối nguồn phim."
STORAGE_FULL_DETAIL = "Kho media của workspace đã đầy."


def _movie(output):
    value = output.get("movie")
    return value if isinstance(value, dict) and value.get("id") else None


def movie_value(source) -> dict[str, Any]:
    """A movie source as steps see it: facts only, never a path, a URL or a Drive ID."""
    return {"id": source.id, "name": source.original_name, "duration": source.duration_seconds,
            "width": source.width, "height": source.height, "container": source.container,
            "content_type": source.content_type, "has_audio": bool(source.audio_codec), "bytes": source.bytes}


def _source_for(context, value) -> Any:
    source_id = value.get("id") if isinstance(value, dict) else None
    return movie_sources.for_workspace(context.db, context.workspace.id, source_id) if source_id else None


def vision_credit_cost() -> int:
    """Credits held for, and charged by, one batch of frames (``VISION_CREDITS_PER_BATCH``, default 1)."""
    try:
        amount = int(system_config.env("VISION_CREDITS_PER_BATCH").strip() or "1")
    except ValueError as exc:
        raise RuntimeError("VISION_CREDITS_PER_BATCH must be a positive integer") from exc
    if not 1 <= amount <= 100000:
        raise RuntimeError("VISION_CREDITS_PER_BATCH must be between 1 and 100000")
    return amount


class MovieSourceNodeHandler(NodeHandler):
    node_type = "movie_source"
    outputs = (OutputPort("movie", MOVIE, extract=_movie),)
    config_fields = (ConfigField("movie_source_id", MOVIE_SOURCE, label="movie_source", code="invalid_movie_source"),)

    def execute(self, context, node, inputs):
        source_id = self.config_values(inputs.config)["movie_source_id"]
        if not source_id:
            return NodeExecutionResult.blocked(MISSING_SOURCE_DETAIL, NodeError("missing_input", "No movie source"))
        source = movie_sources.for_workspace(context.db, context.workspace.id, source_id, locked=True)
        if source is None:
            return NodeExecutionResult.blocked(SOURCE_UNAVAILABLE_DETAIL,
                                               NodeError("input_missing", "Movie source missing"))
        try:
            movie_sources.begin_use(context.db, source, context.run, context.now)
        except movie_sources.MovieSourceError as exc:
            return NodeExecutionResult.blocked(SOURCE_DETAILS.get(exc.code, SOURCE_UNAVAILABLE_DETAIL),
                                               NodeError(exc.code, str(exc)))
        return NodeExecutionResult.completed(SOURCE_USED_DETAIL, {"movie": movie_value(source),
                                                                  "movie_source_id": source.id})

    def readiness(self, context, node):
        source_id = self.config_values(node.get("config"))["movie_source_id"]
        if not source_id:
            return NodeReadiness("missing_input", MISSING_SOURCE_DETAIL, code="missing_input", field="movie_source_id")
        source = movie_sources.for_workspace(context.db, context.workspace.id, source_id)
        if source is None:
            return NodeReadiness("invalid_settings", SOURCE_UNAVAILABLE_DETAIL, code="invalid_movie_source",
                                 field="movie_source_id")
        if source.status not in movie_sources.USABLE:
            code = "source_not_ready" if source.status in movie_sources.WORKING else \
                "source_failed" if source.status == "failed" else "source_expired"
            return NodeReadiness(code, SOURCE_DETAILS[code], code=code, field="movie_source_id")
        return NodeReadiness("configured", SOURCE_READY_DETAIL)


class MoviePrepareNodeHandler(NodeHandler):
    node_type = "movie_prepare"
    inputs = (InputPort("movie", (MOVIE,)),)
    outputs = (OutputPort("movie", MOVIE, extract=_movie),
               OutputPort("audio", AUDIO_ASSETS, keys=("audio_assets",)),
               OutputPort("frames", FRAMES, keys=("frames",)))
    requires = (("movie",),)
    missing_input_detail = NO_MOVIE_DETAIL
    config_fields = (
        ConfigField("frame_interval", INTEGER, minimum=2, maximum=120, presets=(5, 10, 20, 30), label="frame_interval",
                    code="invalid_interval"),
        ConfigField("max_frames", INTEGER, minimum=10, maximum=MAX_FRAMES, presets=(100, 200, 300, 500),
                    label="max_frames", code="invalid_count"),
        advanced(ConfigField("scene_cuts", SELECT, default="on", options=("on", "off"), label="scene_cuts",
                             code="invalid_option")),
    )

    def execute(self, context, node, inputs):
        source = _source_for(context, inputs.get("movie"))
        if source is None or source.status not in movie_sources.USABLE:
            return NodeExecutionResult.blocked(SOURCE_DETAILS["source_expired"],
                                               NodeError("source_expired", "Movie source unavailable"))
        if issue := render.tools_issue():
            return NodeExecutionResult.blocked(issue[1], NodeError(issue[0], issue[1]))
        if storage.is_full(context.db, context.workspace.id):
            return NodeExecutionResult.blocked(STORAGE_FULL_DETAIL, NodeError("storage_limit_exceeded", "Storage full"))
        config, current = self.config_values(inputs.config), movie_sources.settings()
        step = context.step_for(node)
        payload = {"kind": "movie.prepare", "node_type": self.node_type, "node_id": node["id"],
                   "movie_source_id": source.id, "frame_interval": config["frame_interval"] or current.frame_interval_seconds,
                   "max_frames": config["max_frames"] or current.max_frames, "scene_cuts": config["scene_cuts"] == "on"}
        return NodeExecutionResult.queued(PREPARE_QUEUED_DETAIL,
                                          JobRequest("movie", payload, logical_key=f"movie:{step.id}:prepare"),
                                          {"movie_source_id": source.id})

    def readiness(self, context, node):
        if issue := render.tools_issue():
            return NodeReadiness(issue[0], issue[1], code=issue[0])
        return NodeReadiness("configured", PREPARE_READY_DETAIL)


def _frames(value) -> list[dict]:
    frames, seen = [], set()
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        index, moment = item.get("index"), _seconds(item.get("time"))
        if not isinstance(index, int) or isinstance(index, bool) or index < 1 or index in seen or moment is None:
            continue
        seen.add(index)
        frames.append({"index": index, "time": moment, "cut": bool(item.get("cut"))})
    return frames[:MAX_FRAMES]


class VisualAnalysisNodeHandler(NodeHandler):
    node_type = "visual_analysis"
    inputs = (InputPort("frames", (FRAMES,)), InputPort("movie", (MOVIE,)))
    outputs = (OutputPort("visual", VISUAL, extract=lambda output: output.get("visual") or None),)
    requires = (("frames",), ("movie",))
    missing_input_detail = NO_FRAMES_DETAIL
    config_fields = (
        MODEL,
        ConfigField("batch_size", INTEGER, default=10, minimum=4, maximum=20, label="frames_per_batch",
                    code="invalid_count"),
        LANGUAGE,
    )

    def _tool(self, context, config):
        return context.find_tool(TEXT_TASK, TEXT_PROVIDERS, config.get("tool_id"))

    def _problem(self, config, tool):
        if tool is None and config.get("tool_id"):
            return "tool_unavailable", VISUAL_TOOL_UNAVAILABLE_DETAIL
        if tool is None:
            return "missing_tool", VISUAL_MISSING_TOOL_DETAIL
        return text_provider_config_issue(tool.provider)

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        source = _source_for(context, inputs.get("movie"))
        frames = _frames(inputs.get("frames"))
        if source is None or source.status not in movie_sources.USABLE:
            return NodeExecutionResult.blocked(SOURCE_DETAILS["source_expired"],
                                               NodeError("source_expired", "Movie source unavailable"))
        if not frames:
            return NodeExecutionResult.blocked(NO_FRAMES_DETAIL, NodeError("missing_input", "No frames"))
        tool = self._tool(context, config)
        if problem := self._problem(config, tool):
            return NodeExecutionResult.blocked(problem[1], NodeError(problem[0], problem[1]))
        size = config["batch_size"]
        batches = [frames[start:start + size] for start in range(0, len(frames), size)]
        cost, step = vision_credit_cost(), context.step_for(node)
        account = context.db.scalar(select(CreditAccount).where(CreditAccount.workspace_id == context.workspace.id)
                                    .with_for_update())
        if account is None or account.balance < cost * len(batches):
            raise RunRequestError(402, "Not enough credits for this step", code="insufficient_credits",
                                  step_detail=INSUFFICIENT_CREDITS_DETAIL)
        language = config["language"] if config["language"] != "auto" else \
            context.workspace_settings.get("default_language") or "vi"
        jobs, reserved = [], []
        for number, batch in enumerate(batches, 1):
            operation = f"batch:{number}"
            refs = references("vision", step.id, operation)
            usage.post_credit(context.db, context.workspace.id, -cost, "vision_reserve", refs["reserve_reference"])
            reserved.append(refs["reserve_reference"])
            jobs.append(JobRequest("movie", {
                "kind": "vision.analyze", "node_type": self.node_type, "node_id": node["id"],
                "provider": tool.provider, "model": tool.model, "tool_id": tool.id, "movie_source_id": source.id,
                "frames": batch, "operation": operation, "index": number, "language": language, "credits": cost,
                **refs}, logical_key=f"movie:{step.id}:{operation}"))
        output = {"mode": "batches", "expected": len(batches), "frame_count": len(frames), "provider": tool.provider,
                  "model": tool.model, "operations": [{"operation": f"batch:{number}", "frames": len(batch)}
                                                      for number, batch in enumerate(batches, 1)]}
        return NodeExecutionResult.queued(VISUAL_QUEUED_DETAIL, jobs, output,
                                          metadata={"credits_reserved": cost * len(batches),
                                                    "credit_references": reserved})

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        tool = self._tool(context, config)
        cost = vision_credit_cost()
        if problem := self._problem(config, tool):
            return NodeReadiness(problem[0], problem[1], credits=cost)
        return NodeReadiness("ready", f"Sẵn sàng phân tích hình ảnh; giữ {cost} credits mỗi lô khung hình.",
                             credits=cost)


VISION_SYSTEM_PROMPT = (
    "You describe still frames sampled from a movie for a narrator who cannot see them. Reply with one JSON object "
    "and nothing else. Describe only what is visible. Never identify a real person, an actor or a character from "
    "their face or appearance; name someone only when the frame itself shows the name (a caption or on-screen "
    "text). Do not guess places, brands or events that the frame does not show.")
VISION_MAX_TOKENS = 4096


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else \
        f"{seconds // 60:02d}:{seconds % 60:02d}"


def vision_prompt(frames: list[dict], language: str) -> str:
    """The user prompt for one batch: the images are attached in the same order as the list."""
    listed = "\n".join(f"Frame {frame['index']} (image {position}) at {_clock(frame['time'])}"
                       + (", just after a scene cut" if frame.get("cut") else "")
                       for position, frame in enumerate(frames, 1))
    return "\n".join([
        f"Describe each of the {len(frames)} frames below, in {language_name(language)}. They are in time order; "
        "image 1 is the first frame listed.",
        'Return exactly: {"frames": [{"index": frame number, "description": one or two sentences, '
        '"characters": [short descriptions of the people visible, e.g. "a man in a grey coat"], '
        '"location": string, "action": string, "importance": number from 0 to 1}]}.',
        "importance: how much the frame seems to matter to the story (conflict, strong emotion, a turning point, "
        "a reveal) compared with an ordinary moment.",
        listed])


def parse_vision(text: str, frames: list[dict]) -> list[dict]:
    """The notes for the frames of this batch, bounded; a frame the reply skipped or garbled is left out."""
    value = parse_json_object(text) or {}
    by_index = {frame["index"]: frame for frame in frames}
    notes, seen = [], set()
    for item in _items(value.get("frames"), len(frames) + 5):
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if index not in by_index and 1 <= index <= len(frames):
            index = frames[index - 1]["index"]  # numbered by position instead of frame number
        frame = by_index.get(index)
        description, action = _text(item.get("description"), 300), _text(item.get("action"), 160)
        if frame is None or index in seen or not (description or action):
            continue
        seen.add(index)
        importance = item.get("importance")
        notes.append({"index": index, "time": frame["time"], "cut": frame.get("cut", False),
                      "description": description, "action": action, "location": _text(item.get("location"), 100),
                      "characters": [_text(name, 60) for name in _items(item.get("characters"), 4)
                                     if isinstance(name, str) and name.strip()],
                      "importance": round(min(1.0, max(0.0, float(importance))), 2)
                      if isinstance(importance, (int, float)) and not isinstance(importance, bool) else None})
    return sorted(notes, key=lambda note: note["time"])


def _timeline(output):
    value = output.get("timeline")
    return value if isinstance(value, dict) and value.get("windows") else None


class MovieTimelineNodeHandler(NodeHandler):
    node_type = "movie_timeline"
    inputs = (InputPort("transcript", (SOURCE,)), InputPort("visual", (VISUAL,)), InputPort("movie", (MOVIE,)))
    outputs = (OutputPort("timeline", TIMELINE, extract=_timeline),
               OutputPort("source", SOURCE, extract=lambda output: output.get("source") or None),
               OutputPort("text", TEXT, extract=lambda output: (output.get("source") or {}).get("text") or None))
    requires = (("transcript", "visual"),)
    missing_input_detail = TIMELINE_EMPTY_DETAIL
    config_fields = (
        ConfigField("window_seconds", INTEGER, minimum=5, maximum=120, presets=(10, 20, 30, 60), label="window_seconds",
                    code="invalid_duration"),
    )

    def execute(self, context, node, inputs):
        transcript = inputs.get("transcript") if isinstance(inputs.get("transcript"), dict) else None
        visual = inputs.get("visual") if isinstance(inputs.get("visual"), dict) else None
        movie = inputs.get("movie") if isinstance(inputs.get("movie"), dict) else {}
        duration = movie.get("duration") or (transcript or {}).get("metadata", {}).get("duration")
        timeline = movie_timeline.build(transcript, visual, duration=duration,
                                        window=self.config_values(inputs.config)["window_seconds"],
                                        cuts=movie.get("scene_cuts") or [])
        if not movie_timeline.lines(timeline["windows"]):
            return NodeExecutionResult.blocked(TIMELINE_EMPTY_DETAIL, NodeError("missing_input", "Empty timeline"))
        source = movie_timeline.as_source(timeline, title=str(movie.get("name") or ""),
                                          language=(transcript or {}).get("language"),
                                          movie_source_id=movie.get("id"))
        return NodeExecutionResult.completed(TIMELINE_DONE_DETAIL, {
            "timeline": timeline, "source": source, "window_count": len(timeline["windows"]),
            "window_seconds": timeline["window_seconds"], "truncated": timeline["truncated"]})


def parse_sections(text: str, count: int, duration: float | None) -> dict[str, Any]:
    """Title, narration and sections ``[{index, text, source_ranges, importance, kind, duration}]``, all bounded."""
    value = parse_json_object(text) or {}
    limit = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
    sections = []
    for item in _items(value.get("sections"), count):
        if not isinstance(item, dict) or not _text(item.get("text"), 2000):
            continue
        narration = _text(item.get("text"), 2000)
        ranges = []
        for window in _items(item.get("source_ranges"), 4):
            start, end = (_seconds(window.get("start")), _seconds(window.get("end"))) if isinstance(window, dict) \
                else (None, None)
            if start is None or (limit is not None and start >= limit):
                continue
            end = end if end is not None and end > start else start + 5.0
            ranges.append({"start": round(start, 3), "end": round(min(end, limit) if limit else end, 3)})
        importance = item.get("importance")
        importance = round(min(1.0, max(0.0, float(importance))), 2) \
            if isinstance(importance, (int, float)) and not isinstance(importance, bool) else 0.5
        kind = item.get("kind") if item.get("kind") in ("fact", "opinion") else "fact"
        sections.append({"index": len(sections) + 1, "text": narration, "source_ranges": ranges,
                         "importance": importance, "kind": kind, "visual_prompt": narration[:400],
                         "duration": round(len(narration.split()) / WORDS_PER_SECOND, 1)})
    return {"title": _text(value.get("title"), 200), "scenes": sections,
            "script": "\n\n".join(section["text"] for section in sections)}


class ReviewScriptNodeHandler(TextNodeHandler):
    node_type = "review_script"
    inputs = (InputPort("analysis", (STORY,)), InputPort("timeline", (TIMELINE,)), InputPort("movie", (MOVIE,)))
    outputs = (OutputPort("scenes", SCENES, keys=("scenes",)), OutputPort("script", TEXT, keys=("script",)),
               OutputPort("title", TEXT, keys=("title",)))
    requires = (("analysis", "timeline"),)
    response_format = "json"
    config_fields = (
        LANGUAGE,
        ConfigField("mode", SELECT, default="review", options=tuple(REVIEW_MODES), label="review_mode",
                    code="invalid_mode"),
        ConfigField("spoiler_level", SELECT, default="light", options=tuple(SPOILER_LEVELS), code="invalid_spoiler"),
        TONE,
        ConfigField("duration", INTEGER, default=90, minimum=15, maximum=900, presets=(60, 180, 480),
                    label="target_duration", code="invalid_duration"),
        ConfigField("section_count", INTEGER, default=8, minimum=3, maximum=MAX_SECTIONS, label="section_count",
                    code="invalid_count"),
        PLATFORM, INSTRUCTIONS, MODEL, TEMPERATURE, max_tokens_field(6144),
    )
    system_prompt = ("You write narrated movie recap and review scripts for short videos. Reply with one JSON object "
                     "and nothing else. Say what happens in the movie only from the notes you are given, keep opinions "
                     "clearly apart from facts, and never invent events, names or quotes.")

    @staticmethod
    def _duration(inputs) -> float | None:
        movie = inputs.get("movie") if isinstance(inputs.get("movie"), dict) else {}
        timeline = inputs.get("timeline") if isinstance(inputs.get("timeline"), dict) else {}
        value = movie.get("duration") or timeline.get("duration")
        return float(value) if isinstance(value, (int, float)) and value > 0 else None

    def payload_extras(self, context, config, inputs):
        return {"movie_duration": self._duration(inputs), "section_count": config["section_count"]}

    def build_prompt(self, context, config, inputs):
        analysis = inputs.get("analysis") if isinstance(inputs.get("analysis"), dict) else None
        timeline = inputs.get("timeline") if isinstance(inputs.get("timeline"), dict) else None
        if not analysis and not timeline:
            return None
        duration = self._duration(inputs)
        # The narration is never longer than a quarter of the movie: excerpts stay a small part of it.
        seconds = min(config["duration"], max(15, int(duration * 0.25))) if duration else config["duration"]
        lines = [f"Write {REVIEW_MODES[config['mode']]}, in {language_name(self.language(context, config))}.",
                 f"Target spoken length: about {seconds} seconds (roughly {round(seconds * WORDS_PER_SECOND)} words), "
                 f"in exactly {config['section_count']} sections in story order.",
                 SPOILER_LEVELS[config["spoiler_level"]],
                 'Return exactly: {"title": string, "sections": [{"text": string, "kind": "fact" or "opinion", '
                 '"source_ranges": [{"start": seconds, "end": seconds}], "importance": number from 0 to 1}]}.',
                 "text: the narration of the section. kind: fact for what happens, opinion for judgement. "
                 "source_ranges: one to three moments of the movie that show what the section talks about, "
                 "taken from the timeline's times, each 3 to 20 seconds"
                 + (f", within 0 and {int(duration)}" if duration else "") + ". Use different moments for "
                 "different sections. importance: how central the moment is to the story.",
                 "Describe people by what they do or look like unless the dialogue names them.",
                 *self.extras(config)]
        if analysis:
            import json

            lines.append(self.quoted("Story analysis", json.dumps(analysis, ensure_ascii=False)[:20000]))
        if timeline:
            lines.append(self.quoted("Timeline (dialogue and what is on screen)",
                                     movie_timeline.prompt_text(timeline, budget=30000)))
        return "\n".join(lines)

    def output_from(self, payload, result):
        sections = parse_sections(result.text, payload.get("section_count") or MAX_SECTIONS,
                                  payload.get("movie_duration"))
        return {**sections, "text": result.text, "provider": result.provider, "model": result.model,
                "usage": result.usage.as_dict(), "language": payload.get("language")}


class ClipSelectNodeHandler(NodeHandler):
    node_type = "clip_select"
    inputs = (InputPort("scenes", (SCENES,), multiple=True), InputPort("movie", (MOVIE,)),
              InputPort("timeline", (TIMELINE,)), InputPort("audio", (AUDIO_ASSETS,), multiple=True))
    outputs = (OutputPort("source_clips", SOURCE_CLIPS, keys=("source_clips",)),)
    requires = (("scenes",), ("movie",))
    missing_input_detail = NO_MOVIE_DETAIL
    config_fields = (
        ConfigField("min_seconds", NUMBER, default=2, minimum=1, maximum=10, label="clip_min_seconds",
                    code="invalid_duration"),
        ConfigField("max_seconds", NUMBER, default=8, minimum=2, maximum=30, label="clip_max_seconds",
                    code="invalid_duration"),
        ConfigField("max_clips", INTEGER, default=30, minimum=2, maximum=40, label="max_clips", code="invalid_count"),
        advanced(ConfigField("max_share", NUMBER, default=0.25, minimum=0.02, maximum=0.5, label="max_source_share",
                             code="invalid_share")),
        advanced(ConfigField("padding", NUMBER, default=0.3, minimum=0, maximum=3, label="clip_padding",
                             code="invalid_duration")),
    )

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        movie = inputs.get("movie") if isinstance(inputs.get("movie"), dict) else {}
        source = _source_for(context, movie)
        if source is None or source.status in movie_sources.GONE:
            return NodeExecutionResult.blocked(SOURCE_DETAILS["source_expired"],
                                               NodeError("source_expired", "Movie source unavailable"))
        spoken = {}
        for entry in inputs.get("audio") or []:
            scene, seconds = entry.get("scene_index"), entry.get("duration")
            if isinstance(scene, int) and isinstance(seconds, (int, float)) and seconds > 0:
                spoken[scene] = spoken.get(scene, 0.0) + float(seconds)
        timeline = inputs.get("timeline") if isinstance(inputs.get("timeline"), dict) else {}
        chosen = clip_selection.select(
            inputs.get("scenes") or [], duration=source.duration_seconds or movie.get("duration") or 0,
            movie_source_id=source.id, spoken=spoken, windows=timeline.get("windows") or [],
            min_seconds=config["min_seconds"], max_seconds=max(config["max_seconds"], config["min_seconds"]),
            max_clips=config["max_clips"], max_share=config["max_share"], padding=config["padding"],
            content_type=source.content_type)
        if not chosen["clips"]:
            return NodeExecutionResult.blocked(CLIPS_NONE_DETAIL, NodeError("missing_input", "No clips selected"))
        return NodeExecutionResult.completed(
            f"{CLIPS_SELECTED_DETAIL} ({len(chosen['clips'])} đoạn, {chosen['total_seconds']:.0f} giây)",
            {"source_clips": chosen["clips"], "coverage": chosen["coverage"], "total_seconds": chosen["total_seconds"],
             "share": chosen["share"], "warnings": chosen["warnings"], "movie_source_id": source.id})


MOVIE_HANDLERS = (MovieSourceNodeHandler, MoviePrepareNodeHandler, VisualAnalysisNodeHandler,
                  MovieTimelineNodeHandler, ReviewScriptNodeHandler, ClipSelectNodeHandler)
