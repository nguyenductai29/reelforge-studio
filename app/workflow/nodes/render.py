"""Render: joins scene clips, narration and subtitles into one final MP4 with FFmpeg, in the render worker.

Before anything is queued, every input is checked: each clip, narration and
subtitle file must be an asset of this workspace with its file on disk, FFmpeg
and ffprobe must be installed (and the subtitle font, when subtitles are
connected), and the workspace must have storage left. The job's payload
freezes the asset IDs, their scene order and the subtitle cues, so a later edit
cannot change a queued render. Rendering is free by default
(``RENDER_CREDITS_PER_JOB``); a price, when set, is reserved per render and
refunded if the render fails. See app/render.py for the audio and timing rules.
"""
from sqlalchemy import select

from app import render, subtitles, usage
from app.media_paths import asset_path, stored_bytes, workspace_media_quota
from app.models import Asset
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, NodeHandler
from app.workflow.nodes.media import connected_inputs, references
from app.workflow.ports import AUDIO_ASSETS, IMAGE_ASSETS, SUBTITLE_ASSET, VIDEO_ASSETS, InputPort, OutputPort
from app.workflow.results import JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError

QUEUED_DETAIL = "Đã xếp hàng render video."
MISSING_INPUT_DETAIL = "Chưa nối clip video vào bước Render."
IMAGES_DETAIL = "Render hiện chỉ ghép video; trình chiếu từ ảnh chưa được hỗ trợ."
MISSING_FILE_DETAIL = "Không tìm thấy tệp đầu vào của bước Render trong kho media."
STORAGE_FULL_DETAIL = "Kho media của workspace đã đầy; chưa render được."
READY_DETAIL = "Sẵn sàng render bằng FFmpeg trên máy chủ."
VIDEO_TYPES = frozenset({"video/mp4"})
AUDIO_TYPES = frozenset({"audio/wav", "audio/mpeg"})


def _rendered(output):
    assets = output.get("video_assets")
    return assets if isinstance(assets, list) and assets else None


def _entries(value) -> list[dict]:
    """Media entries with an ID, first occurrence only, in the order they arrived."""
    seen, entries = set(), []
    for entry in value if isinstance(value, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"] not in seen:
            seen.add(entry["id"])
            entries.append(entry)
    return entries


def _scene(entry) -> int | None:
    value = entry.get("scene_index")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class RenderNodeHandler(NodeHandler):
    node_type = "render"
    # Port names are the placeholder's, so saved edges still connect.
    inputs = (InputPort("media", (VIDEO_ASSETS, IMAGE_ASSETS), multiple=True),
              InputPort("audio", (AUDIO_ASSETS,), multiple=True),
              InputPort("subtitle", (SUBTITLE_ASSET,)))
    outputs = (OutputPort("rendered_video", VIDEO_ASSETS, extract=_rendered),)
    requires = (("media",),)
    missing_input_detail = MISSING_INPUT_DETAIL

    @staticmethod
    def _problem(check_font: bool):
        if issue := render.tools_issue():
            return issue
        if check_font and (issue := render.font_issue()):
            return issue
        return None

    def execute(self, context, node, inputs):
        db, workspace_id = context.db, context.workspace.id
        media, voices = _entries(inputs.get("media")), _entries(inputs.get("audio"))
        subtitle = inputs.get("subtitle") if isinstance(inputs.get("subtitle"), dict) else None
        ids = [entry["id"] for entry in media + voices] + ([subtitle["id"]] if subtitle and subtitle.get("id") else [])
        rows = {row.id: row for row in db.scalars(select(Asset).where(Asset.workspace_id == workspace_id,
                                                                       Asset.id.in_(ids)))} if ids else {}
        if any(rows.get(entry["id"]) is not None and rows[entry["id"]].content_type.startswith("image/")
               for entry in media):
            return NodeExecutionResult.blocked(IMAGES_DETAIL, NodeError("unsupported_media", "Image slideshows"))
        clips = [entry for entry in media if rows.get(entry["id"]) is not None
                 and rows[entry["id"]].content_type in VIDEO_TYPES]
        if not clips:
            return NodeExecutionResult.blocked(MISSING_INPUT_DETAIL, NodeError("missing_input", "No video clips"))
        tracks = [entry for entry in voices if rows.get(entry["id"]) is not None
                  and rows[entry["id"]].content_type in AUDIO_TYPES]
        subtitle_row = rows.get(subtitle.get("id")) if subtitle else None
        if (len(clips) != len(media) or len(tracks) != len(voices)
                or (subtitle and (subtitle_row is None or subtitle_row.content_type not in subtitles.CONTENT_TYPES))
                or not all(asset_path(db, workspace_id, entry["id"]).is_file() for entry in clips + tracks)
                or (subtitle_row is not None and not asset_path(db, workspace_id, subtitle_row.id).is_file())):
            return NodeExecutionResult.blocked(MISSING_FILE_DETAIL, NodeError("input_missing", "Input file missing"))
        if problem := self._problem(subtitle is not None):
            return NodeExecutionResult.blocked(problem[1], NodeError(problem[0], problem[1]))
        if stored_bytes(db, workspace_id) >= workspace_media_quota():
            return NodeExecutionResult.blocked(STORAGE_FULL_DETAIL, NodeError("storage_limit_exceeded", "Storage full"))
        step, cost = context.step_for(node), render.render_credit_cost()
        payload = {"kind": "render.generate", "node_type": self.node_type, "node_id": node["id"], "provider": "ffmpeg",
                   "model": "local", "credits": cost,
                   "clips": [{"asset_id": entry["id"], "scene_index": _scene(entry), "duration": entry.get("duration")}
                             for entry in clips],
                   "audio": [{"asset_id": entry["id"], "scene_index": _scene(entry), "duration": entry.get("duration")}
                             for entry in tracks],
                   "subtitle": {"asset_id": subtitle_row.id, "timing": subtitle.get("timing"),
                                "style": subtitle.get("style"), "cues": subtitle.get("cues") or [],
                                "segments": subtitle.get("segments") or []} if subtitle_row else None}
        metadata = {}
        if cost:
            refs = references("render", step.id, "final")
            try:
                usage.post_credit(db, workspace_id, -cost, "render_reserve", refs["reserve_reference"])
            except ValueError as exc:
                raise RunRequestError(402, "Not enough credits for this render", code="insufficient_credits",
                                      step_detail=INSUFFICIENT_CREDITS_DETAIL) from exc
            payload.update(operation="final", **refs)
            metadata = {"credits_reserved": cost, "credit_reference": refs["reserve_reference"]}
        output = {"clip_count": len(clips), "audio_count": len(tracks), "subtitles": subtitle_row is not None,
                  "audio_policy": "voice" if tracks else "clips"}
        return NodeExecutionResult.queued(QUEUED_DETAIL, JobRequest("render", payload,
                                                                    logical_key=f"render:{step.id}:final"),
                                          output, metadata=metadata)

    def readiness(self, context, node):
        cost = render.render_credit_cost()
        if problem := self._problem("subtitle" in connected_inputs(context, node)):
            return NodeReadiness(problem[0], problem[1], credits=cost, code=problem[0])
        if cost and context.credit_balance < cost:
            return NodeReadiness("insufficient_credits", f"Cần {cost} credits; hiện có {context.credit_balance}.",
                                 credits=cost)
        return NodeReadiness("configured", READY_DETAIL, credits=cost)
