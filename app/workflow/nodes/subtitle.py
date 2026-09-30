"""Subtitle: turns scenes or a script into an SRT or WebVTT file, locally and for free.

It runs inside the executor pass, with no job and no credits: timing is
deterministic (app/subtitles.py). Inputs, in order of preference: connected
scenes (their text and timing), else connected script/text. Connected narration
(``audio``) or clips (``video``) replace the scenes' estimated durations with
real ones. The file is stored as a private workspace asset with its lineage;
the output also carries the cues and each scene's time span, which Render uses
to place the burned-in subtitles on its own timeline, and the burn-in style.
"""
import uuid

from sqlalchemy import event

from app import subtitles
from app.media_paths import media_root, stored_bytes, workspace_media_quota
from app.models import Asset
from app.workflow.config import INTEGER, SELECT, ConfigField
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import AUDIO_ASSETS, BRIEF, SCENES, SUBTITLE_ASSET, TEXT, VIDEO_ASSETS, InputPort, OutputPort
from app.workflow.results import NodeError, NodeExecutionResult, NodeReadiness

DONE_DETAIL = "Đã tạo phụ đề."
NOTHING_DETAIL = "Không có lời thoại nào để làm phụ đề."
MISSING_INPUT_DETAIL = "Chưa có cảnh hoặc kịch bản để làm phụ đề."
STORAGE_FULL_DETAIL = "Kho media của workspace đã đầy; chưa lưu được phụ đề."


def _subtitle_asset(output):
    value = output.get("subtitle_asset")
    return value if isinstance(value, dict) and value.get("id") else None


def _keep_only_if_committed(db, path):
    """Delete the written file if the transaction that records it rolls back."""
    state = {"settled": False}

    def committed(session):
        state["settled"] = True

    def rolled_back(session):
        if not state["settled"]:
            state["settled"] = True
            path.unlink(missing_ok=True)

    event.listen(db, "after_commit", committed)
    event.listen(db, "after_rollback", rolled_back)


class SubtitleNodeHandler(NodeHandler):
    node_type = "subtitle"
    # "script" and "video" keep the placeholder's port names, so saved edges still connect.
    inputs = (InputPort("script", (TEXT, BRIEF), multiple=True), InputPort("video", (VIDEO_ASSETS,)),
              InputPort("scenes", (SCENES,), multiple=True), InputPort("audio", (AUDIO_ASSETS,), multiple=True))
    outputs = (OutputPort("subtitle_asset", SUBTITLE_ASSET, extract=_subtitle_asset),)
    requires = (("script", "scenes"),)
    missing_input_detail = MISSING_INPUT_DETAIL
    config_fields = (
        ConfigField("format", SELECT, default="srt", options=tuple(subtitles.FORMATS), label="subtitle_format",
                    code="invalid_format"),
        ConfigField("max_chars", INTEGER, default=42, minimum=16, maximum=80, label="max_chars_per_line",
                    code="invalid_line_length"),
        ConfigField("max_lines", INTEGER, default=2, minimum=1, maximum=3, code="invalid_max_lines"),
        ConfigField("style", SELECT, default="classic", options=subtitles.STYLES, label="subtitle_style",
                    code="invalid_style"),
        ConfigField("font_size", SELECT, default="medium", options=subtitles.FONT_SIZES, code="invalid_font_size"),
    )

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        audio, video = inputs.get("audio"), inputs.get("video")
        segments, timing = subtitles.plan_scenes(inputs.get("scenes"), audio, video)
        if not any(segment.text for segment in segments):
            segments, timing = subtitles.plan_text(inputs.get("script"), audio, video)
        cues = subtitles.build_cues(segments, max_chars=config["max_chars"], max_lines=config["max_lines"])
        if not cues:
            return NodeExecutionResult.blocked(NOTHING_DETAIL, NodeError("missing_input", "No text to subtitle"))
        content_type, extension = subtitles.FORMATS[config["format"]]
        data = subtitles.render(cues, config["format"])
        db, workspace_id, step = context.db, context.workspace.id, context.step_for(node)
        if stored_bytes(db, workspace_id) + len(data) > workspace_media_quota():
            return NodeExecutionResult.blocked(STORAGE_FULL_DETAIL, NodeError("storage_limit_exceeded",
                                                                              "Workspace media quota reached"))
        asset_id = str(uuid.uuid4())
        folder = media_root(db) / workspace_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / asset_id
        target.write_bytes(data)
        _keep_only_if_committed(db, target)
        filename = f"subtitle-{asset_id[:8]}.{extension}"
        db.add(Asset(id=asset_id, workspace_id=workspace_id, project_id=context.project.id if context.project else None,
                     run_id=context.run.id, step_id=step.id, provider="local", model="subtitle", filename=filename,
                     content_type=content_type, bytes=len(data)))
        duration = segments[-1].end / 1000
        asset = {"id": asset_id, "asset_id": asset_id, "filename": filename, "content_type": content_type,
                 "format": config["format"], "cue_count": len(cues), "duration": duration, "timing": timing,
                 "style": {"preset": config["style"], "font_size": config["font_size"]},
                 "cues": [subtitles.cue_dict(cue) for cue in cues],
                 "segments": [subtitles.segment_dict(segment) for segment in segments]}
        output = {"subtitle_asset": asset, "format": config["format"], "cue_count": len(cues),
                  "duration": duration, "timing": timing}
        return NodeExecutionResult.completed(DONE_DETAIL, output, asset_ids=(asset_id,))

    def readiness(self, context, node):
        return NodeReadiness("configured", "Tạo phụ đề trên máy chủ, miễn phí.")
