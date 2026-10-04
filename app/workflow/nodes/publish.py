"""Publish: the hand-off from an approved video to the Publishing page. It never uploads anything.

Publishing stays a deliberate human action: after Review is approved, this step
completes with the video to publish (the final render when the run has one,
else its clip) and prepared YouTube metadata. The owner then reviews that
metadata on the Publishing page and presses Publish, which queues the existing
durable YouTube upload (app/publications.py, app/youtube_worker.py).

Metadata, in order: this step's own settings (values the user typed are never
replaced), then a connected Metadata step, then connected title/description
text, then the project title. Everything is fitted to YouTube's limits.

``platforms`` (Phase 12) prepares every channel the same way: YouTube's
values, a TikTok caption and a Facebook description, each from this step's own
settings first, then a connected Metadata step, then YouTube's values. Nothing
is uploaded until the owner publishes from the Publishing page.
"""
from app.models import WorkflowRunStep
from app.publications import final_video, fit_metadata, fit_social, platform_metadata
from app.publishers.youtube import PRIVACY_STATUSES
from app.workflow.config import SELECT, TEXT as TEXT_FIELD, ConfigField
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import BRIEF, PUBLISH_METADATA, TEXT, VIDEO_ASSETS, InputPort
from app.workflow.results import NodeError, NodeExecutionResult, NodeReadiness

READY_DETAIL = "Sẵn sàng đăng: xem lại thông tin và bấm Đăng trong mục Đăng tải."
NO_VIDEO_DETAIL = "Chưa có video hoàn chỉnh để đăng."
HANDOFF_DETAIL = "Bàn giao sang Đăng tải sau khi duyệt; bước này không tự đăng."


def _first_line(value) -> str:
    return value.strip().splitlines()[0] if isinstance(value, str) and value.strip() else ""


class PublishNodeHandler(NodeHandler):
    node_type = "publish"
    # The first three ports are the placeholder's, so saved edges still connect.
    inputs = (InputPort("video", (VIDEO_ASSETS,)), InputPort("title", (TEXT, BRIEF)), InputPort("description", (TEXT,)),
              InputPort("metadata", (PUBLISH_METADATA,)))
    requires = (("video",),)
    missing_input_detail = NO_VIDEO_DETAIL
    config_fields = (
        ConfigField("privacy_status", SELECT, default="private", options=PRIVACY_STATUSES, label="privacy",
                    code="invalid_privacy"),
        ConfigField("title", TEXT_FIELD, max_length=100, label="publish_title", code="invalid_title"),
        ConfigField("description", TEXT_FIELD, max_length=5000, multiline=True, label="publish_description",
                    code="invalid_description"),
        ConfigField("tags", TEXT_FIELD, max_length=500, label="publish_tags", code="invalid_tags"),
        ConfigField("tiktok_caption", TEXT_FIELD, max_length=2200, multiline=True, advanced=True,
                    code="invalid_description"),
        ConfigField("facebook_description", TEXT_FIELD, max_length=2000, multiline=True, advanced=True,
                    code="invalid_description"),
    )

    def execute(self, context, node, inputs):
        asset = final_video(context.db, context.run)
        if asset is None:
            return NodeExecutionResult.blocked(NO_VIDEO_DETAIL, NodeError("missing_input", "No finished video"))
        config = self.config_values(inputs.config)
        prepared = inputs.get("metadata") if isinstance(inputs.get("metadata"), dict) else {}
        project = context.project
        choices = {
            "title": [("setting", config["title"]), ("metadata", prepared.get("title")),
                      ("text", _first_line(inputs.get("title"))), ("project", project.title if project else "")],
            "description": [("setting", config["description"]), ("metadata", prepared.get("description")),
                            ("text", inputs.get("description"))],
            "tags": [("setting", [tag for tag in (config["tags"] or "").split(",")]),
                     ("metadata", prepared.get("tags"))],
        }
        values, sources = {}, {}
        for key, options in choices.items():
            source, value = next(((name, value) for name, value in options
                                  if (value.strip() if isinstance(value, str) else
                                      any(isinstance(item, str) and item.strip() for item in value or []))),
                                 (None, [] if key == "tags" else ""))
            values[key], sources[key] = value, source
        metadata = {**fit_metadata(values["title"], values["description"], values["tags"]),
                    "privacy_status": config["privacy_status"]}
        suggested = prepared.get("platforms") if isinstance(prepared.get("platforms"), dict) else {}
        platforms = platform_metadata(metadata)
        for channel, key in (("tiktok", "tiktok_caption"), ("facebook", "facebook_description")):
            if (config[key] or "").strip():
                # A value the owner typed wins over anything generated.
                platforms[channel] = fit_social(channel, metadata["title"], config[key], metadata["tags"])
                sources[channel] = "setting"
            elif isinstance(suggested.get(channel), dict):
                platforms[channel] = fit_social(channel, metadata["title"], suggested[channel].get("description"),
                                                metadata["tags"])
                sources[channel] = "metadata"
            else:
                sources[channel] = "youtube"
        source_step = context.db.get(WorkflowRunStep, asset.step_id)
        video = {"asset_id": asset.id, "filename": asset.filename,
                 "final": bool(source_step and source_step.node_type == "render")}
        return NodeExecutionResult.completed(READY_DETAIL, {"channel": "youtube", "video": video,
                                                            "metadata": metadata, "platforms": platforms,
                                                            "sources": sources})

    def readiness(self, context, node):
        return NodeReadiness("configured", HANDOFF_DETAIL)
