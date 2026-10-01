"""Source steps (Phase 10): bring existing content into a workflow to repurpose it.

* **Text Source** (``source_text``): a title and pasted text; completes at once.
* **URL Source** (``source_url``): one public web page, fetched by the source worker
  (``app/source_worker.py``) with the SSRF rules of ``app/sources.py``. No
  JavaScript runs and no paywall or bot check is bypassed; a page without
  readable text fails with ``no_text``.
* **Uploaded Media Source** (``source_media``): one file of the workspace. TXT, MD,
  SRT and VTT documents are read at once (subtitle cues keep their times); audio
  and video files are passed on for the Transcript step, and a video also feeds
  Render or Match Source Scenes as the source clip. An image (PNG, JPEG, WEBP)
  feeds Render as a still (Phase 16).
* **Transcript** (``transcribe``): speech to text with timed segments, through the
  workspace's transcription model (task ``transcription``), as one durable paid
  job. A source that already has text (a document, a page or subtitles) passes
  through for free.

Every source step outputs a ``SOURCE`` value (see ``app/sources.py``) and its
plain ``text``, so text steps (Summarize, Rewrite, AI Writer…) can read it.
Transcription credits (``TRANSCRIPTION_CREDITS_PER_JOB``, default 2) are held
when the step is queued (``transcription-reserve:<step>:single``), charged when
the transcript is stored and refunded when the provider definitely failed.
"""
from sqlalchemy import select

from app import render, sources, usage
from app.media_paths import asset_path
from app.models import Asset
from app.providers.transcription import (TRANSCRIPTION_PROVIDERS, TRANSCRIPTION_TASK, transcription_config_issue,
                                         transcription_credit_cost, transcription_model)
from app.workflow.config import ASSET, SELECT, TEXT as TEXT_FIELD, TOOL, ConfigField
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, NodeHandler
from app.workflow.nodes.media import references
from app.workflow.ports import AUDIO_ASSETS, IMAGE_ASSETS, SOURCE, TEXT, VIDEO_ASSETS, InputPort, OutputPort, bind_edges
from app.workflow.results import JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError

LANGUAGES = ("auto", "vi", "en", "ja")
DOCUMENT_TYPES = tuple(sources.DOCUMENT_TYPES)
VIDEO_TYPES = ("video/mp4", "video/webm")
AUDIO_TYPES = ("audio/mpeg", "audio/wav", "audio/ogg")
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
MEDIA_TYPES = DOCUMENT_TYPES + VIDEO_TYPES + AUDIO_TYPES + IMAGE_TYPES
MAX_TEXT_CHARS = 7000  # a node's settings hold at most 8,000 characters; upload a TXT file for more

TEXT_DONE_DETAIL = "Đã nhận văn bản nguồn."
NO_TEXT_DETAIL = "Nhập văn bản nguồn cho bước này."
URL_MISSING_DETAIL = "Nhập địa chỉ https:// của trang cần lấy nội dung."
URL_BLOCKED_DETAIL = "Chỉ lấy được trang web công khai qua https://; địa chỉ nội bộ hoặc riêng tư bị chặn."
URL_QUEUED_DETAIL = "Đã xếp hàng tải nội dung trang web."
URL_READY_DETAIL = "Sẵn sàng tải trang web (miễn phí, không chạy JavaScript)."
MEDIA_MISSING_DETAIL = "Chọn tệp nguồn (TXT, MD, SRT, VTT, ảnh, âm thanh hoặc video) đã tải lên."
MEDIA_UNAVAILABLE_DETAIL = "Không tìm thấy tệp nguồn đã chọn trong kho media."
DOCUMENT_DONE_DETAIL = "Đã đọc tài liệu nguồn."
MEDIA_DONE_DETAIL = "Đã chọn tệp nguồn; nối bước Phiên âm để lấy lời thoại."
MEDIA_READY_DETAIL = "Sẵn sàng dùng tệp nguồn (miễn phí)."
TRANSCRIPT_QUEUED_DETAIL = "Đã xếp hàng phiên âm."
TRANSCRIPT_PASS_DETAIL = "Nguồn đã có văn bản; không cần phiên âm."
TRANSCRIPT_MISSING_DETAIL = "Chưa nối nguồn âm thanh hoặc video để phiên âm."
MISSING_TOOL_DETAIL = "Chọn model phiên âm được hỗ trợ trong Model AI."
TOOL_UNAVAILABLE_DETAIL = "Model phiên âm đã chọn cho bước này không còn được bật; chọn model khác."
UNSUPPORTED_MODEL_DETAIL = "Model phiên âm này chưa được hỗ trợ."
FREE_DETAIL = "Nguồn đã có văn bản; bước này không tốn credits."


def _source_text(output):
    source = output.get("source")
    text = output.get("text") or (source.get("text") if isinstance(source, dict) else None)
    return text if isinstance(text, str) and text.strip() else None


def _source(key):
    def extract(output):
        value = output.get(key)
        return value if isinstance(value, dict) and value else None
    return extract


def _language(config) -> str | None:
    return None if config.get("language") in (None, "auto") else config["language"]


def _completed(detail: str, source: dict, **extra) -> NodeExecutionResult:
    return NodeExecutionResult.completed(detail, {"source": source, "text": source["text"], "title": source["title"],
                                                  **extra})


def _workspace_asset(context, asset_id) -> Asset | None:
    if not isinstance(asset_id, str) or not asset_id:
        return None
    return context.db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == context.workspace.id))


class TextSourceNodeHandler(NodeHandler):
    node_type = "source_text"
    outputs = (OutputPort("source", SOURCE, extract=_source("source")), OutputPort("text", TEXT, extract=_source_text))
    config_fields = (
        ConfigField("title", TEXT_FIELD, max_length=200, label="source_title", code="invalid_title"),
        ConfigField("text", TEXT_FIELD, max_length=MAX_TEXT_CHARS, multiline=True, label="source_text",
                    code="invalid_text"),
        ConfigField("language", SELECT, default="auto", options=LANGUAGES, code="invalid_language"),
    )

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        if not (config["text"] or "").strip():
            return NodeExecutionResult.blocked(NO_TEXT_DETAIL, NodeError("missing_input", "No source text"))
        return _completed(TEXT_DONE_DETAIL, sources.make_source("text", title=config["title"] or "",
                                                                text=config["text"], language=_language(config)))

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        if not (config["text"] or "").strip():
            return NodeReadiness("missing_input", NO_TEXT_DETAIL, code="missing_input", field="text")
        return NodeReadiness("configured", TEXT_DONE_DETAIL)


class URLSourceNodeHandler(NodeHandler):
    node_type = "source_url"
    outputs = (OutputPort("source", SOURCE, extract=_source("source")), OutputPort("text", TEXT, extract=_source_text))
    config_fields = (
        ConfigField("url", TEXT_FIELD, max_length=2048, label="source_url", code="invalid_url"),
    )

    @staticmethod
    def _problem(url: str):
        if not url:
            return "missing_input", URL_MISSING_DETAIL, "missing_input"
        try:
            sources.check_url(url)
        except sources.SourceError as exc:
            return "invalid_settings", URL_BLOCKED_DETAIL, exc.code
        return None

    def execute(self, context, node, inputs):
        url = (self.config_values(inputs.config)["url"] or "").strip()
        if problem := self._problem(url):
            return NodeExecutionResult.blocked(problem[1], NodeError(problem[2], problem[1]))
        step = context.step_for(node)
        payload = {"kind": "source.fetch", "node_type": self.node_type, "node_id": node["id"], "url": url}
        return NodeExecutionResult.queued(URL_QUEUED_DETAIL,
                                          JobRequest("source", payload, logical_key=f"source:{step.id}:url"),
                                          {"source_url": url})

    def readiness(self, context, node):
        url = (self.config_values(node.get("config"))["url"] or "").strip()
        if problem := self._problem(url):
            return NodeReadiness(problem[0], problem[1], code=problem[2], field="url")
        return NodeReadiness("configured", URL_READY_DETAIL)


class MediaSourceNodeHandler(NodeHandler):
    node_type = "source_media"
    outputs = (OutputPort("source", SOURCE, extract=_source("source")), OutputPort("text", TEXT, extract=_source_text),
               OutputPort("video", VIDEO_ASSETS, keys=("video_assets",)),
               OutputPort("audio", AUDIO_ASSETS, keys=("audio_assets",)),
               OutputPort("image", IMAGE_ASSETS, keys=("image_assets",)))
    config_fields = (
        ConfigField("asset_id", ASSET, label="source_file", content_types=MEDIA_TYPES, code="invalid_asset"),
        ConfigField("language", SELECT, default="auto", options=LANGUAGES, code="invalid_language"),
    )

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        if not config["asset_id"]:
            return NodeExecutionResult.blocked(MEDIA_MISSING_DETAIL, NodeError("missing_input", "No source file"))
        asset = _workspace_asset(context, config["asset_id"])
        path = asset_path(context.db, context.workspace.id, asset.id) if asset else None
        if asset is None or asset.content_type not in MEDIA_TYPES or not path.is_file():
            return NodeExecutionResult.blocked(MEDIA_UNAVAILABLE_DETAIL, NodeError("input_missing", "Source missing"))
        language = _language(config)
        if asset.content_type in DOCUMENT_TYPES:
            try:
                if asset.bytes > sources.MAX_DOCUMENT_BYTES:
                    raise sources.SourceError("too_large", "The document is larger than 2 MB")
                source = sources.document_source(path.read_bytes(), asset.content_type, title=asset.filename,
                                                 asset_id=asset.id)
            except sources.SourceError as exc:
                return NodeExecutionResult.failed(NodeError(exc.code, str(exc)), detail=str(exc))
            source["language"] = language
            return _completed(DOCUMENT_DONE_DETAIL, source)
        media = ("video" if asset.content_type in VIDEO_TYPES
                 else "image" if asset.content_type in IMAGE_TYPES else "audio")
        source = sources.make_source(media, title=asset.filename, language=language, asset_id=asset.id,
                                     metadata={"content_type": asset.content_type, "bytes": asset.bytes})
        entry = {"id": asset.id, "asset_id": asset.id, "filename": asset.filename,
                 "content_type": asset.content_type, "scene_index": None, "source": True}
        return _completed(MEDIA_DONE_DETAIL, source, **{f"{media}_assets": [entry]})

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        if not config["asset_id"]:
            return NodeReadiness("missing_input", MEDIA_MISSING_DETAIL, code="missing_input", field="asset_id")
        asset = _workspace_asset(context, config["asset_id"])
        if asset is None or asset.content_type not in MEDIA_TYPES:
            return NodeReadiness("invalid_settings", MEDIA_UNAVAILABLE_DETAIL, code="invalid_asset", field="asset_id")
        return NodeReadiness("configured", MEDIA_READY_DETAIL)


def _media_entry(value):
    for entry in value if isinstance(value, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            return entry
    return None


class TranscribeNodeHandler(NodeHandler):
    node_type = "transcribe"
    inputs = (InputPort("source", (SOURCE,)), InputPort("media", (VIDEO_ASSETS, AUDIO_ASSETS)))
    outputs = (OutputPort("transcript", SOURCE, extract=_source("transcript")),
               OutputPort("text", TEXT, extract=lambda output: _source_text({"source": output.get("transcript")})))
    requires = (("source", "media"),)
    missing_input_detail = TRANSCRIPT_MISSING_DETAIL
    config_fields = (
        ConfigField("tool_id", TOOL, label="model", task=TRANSCRIPTION_TASK, providers=tuple(TRANSCRIPTION_PROVIDERS),
                    code="unsupported_model"),
        ConfigField("language", SELECT, default="auto", options=LANGUAGES, label="spoken_language",
                    code="invalid_language"),
    )

    def _tool(self, context, config):
        return context.find_tool(TRANSCRIPTION_TASK, TRANSCRIPTION_PROVIDERS, config["tool_id"] or None)

    def _problem(self, config, tool):
        """(status, detail, code, field) of a model or server problem, or ``None``."""
        if tool is None and config["tool_id"]:
            return "tool_unavailable", TOOL_UNAVAILABLE_DETAIL, "tool_unavailable", "tool_id"
        if tool is None:
            return "missing_tool", MISSING_TOOL_DETAIL, "missing_tool", None
        if issue := transcription_config_issue(tool.provider):
            return issue[0], issue[1], issue[0], None
        if transcription_model(tool.provider, tool.model) is None:
            return "unsupported_model", UNSUPPORTED_MODEL_DETAIL, "unsupported_model", "tool_id"
        if issue := render.tools_issue():
            return issue[0], issue[1], issue[0], None
        return None

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        source = inputs.get("source") if isinstance(inputs.get("source"), dict) else None
        if source and source.get("source_type") not in ("video", "audio") and (source.get("text") or "").strip():
            return NodeExecutionResult.completed(TRANSCRIPT_PASS_DETAIL, {"transcript": source, "text": source["text"],
                                                                         "credits": 0})
        asset_id = source.get("asset_id") if source else None
        if not asset_id and (entry := _media_entry(inputs.get("media"))):
            asset_id = entry["id"]
        asset = _workspace_asset(context, asset_id)
        if (asset is None or asset.content_type not in VIDEO_TYPES + AUDIO_TYPES
                or not asset_path(context.db, context.workspace.id, asset.id).is_file()):
            return NodeExecutionResult.blocked(MEDIA_UNAVAILABLE_DETAIL, NodeError("input_missing", "Media missing"))
        tool = self._tool(context, config)
        if problem := self._problem(config, tool):
            return NodeExecutionResult.blocked(problem[1], NodeError(problem[2], problem[1]))
        step, cost = context.step_for(node), transcription_credit_cost()
        refs = references("transcription", step.id, "single")
        try:
            usage.post_credit(context.db, context.workspace.id, -cost, "transcription_reserve",
                              refs["reserve_reference"])
        except ValueError as exc:
            raise RunRequestError(402, "Not enough credits for this step", code="insufficient_credits",
                                  step_detail=INSUFFICIENT_CREDITS_DETAIL) from exc
        language = _language(config) or (source.get("language") if source else None)
        payload = {"kind": "transcription.generate", "node_type": self.node_type, "node_id": node["id"],
                   "provider": tool.provider, "model": tool.model, "tool_id": tool.id, "asset_id": asset.id,
                   "title": asset.filename, "language": language if language in LANGUAGES[1:] else None,
                   "credits": cost, "operation": "single", **refs}
        return NodeExecutionResult.queued(
            TRANSCRIPT_QUEUED_DETAIL, JobRequest("source", payload, logical_key=f"source:{step.id}:transcript"),
            {"provider": tool.provider, "model": tool.model, "source_asset_id": asset.id},
            metadata={"credits_reserved": cost, "credit_references": [refs["reserve_reference"]]})

    @staticmethod
    def _text_upstream(context, node) -> bool:
        """Whether every connected source already has text (a text, page or document source): no job then."""
        types = {item["id"]: item for item in context.graph["nodes"]}
        from app.workflow.registry import default_registry
        parents = [types[binding.source] for binding in bind_edges(context.graph, default_registry)
                   if binding is not None and binding.target == node["id"]]
        if not parents:
            return False
        for parent in parents:
            if parent["type"] in ("source_text", "source_url"):
                continue
            config = parent.get("config") if isinstance(parent.get("config"), dict) else {}
            asset = _workspace_asset(context, config.get("asset_id")) if parent["type"] == "source_media" else None
            if asset is None or asset.content_type not in DOCUMENT_TYPES:
                return False
        return True

    def readiness(self, context, node):
        if self._text_upstream(context, node):
            return NodeReadiness("configured", FREE_DETAIL)
        config = self.config_values(node.get("config"))
        cost = transcription_credit_cost()
        tool = self._tool(context, config)
        if problem := self._problem(config, tool):
            status, detail, code, field = problem
            return NodeReadiness(status, detail, credits=cost, code=code, field=field)
        if context.credit_balance < cost:
            return NodeReadiness("insufficient_credits", f"Cần {cost} credits; hiện có {context.credit_balance}.",
                                 credits=cost)
        return NodeReadiness("ready", f"Sẵn sàng phiên âm; dự kiến giữ {cost} credits.", credits=cost)


SOURCE_HANDLERS = (TextSourceNodeHandler, URLSourceNodeHandler, MediaSourceNodeHandler, TranscribeNodeHandler)
