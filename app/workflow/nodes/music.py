"""Background Music: an uploaded audio file that Render mixes under the video at a chosen volume.

Use music you own or are licensed to use. The step calls no provider and costs
nothing: it checks that the file is an MP3, WAV or OGG upload of this workspace
and passes ``{asset_id, filename, volume, mode}`` to Render (port type ``music_track``).
Render lowers the track to ``volume`` percent and mixes it under the narration (or under
the clips' own sound). A track shorter than the video loops (``mode`` ``loop``, the
default) or plays once and leaves silence (``once``); a longer one is cut at the video's end.

This replaces the earlier placeholder of the same type. Old edges into its
"mood" input no longer carry data; they keep ordering the run.
"""
from sqlalchemy import select

from app.media_paths import asset_path
from app.models import Asset
from app.workflow.config import ASSET, INTEGER, SELECT, ConfigField
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import MUSIC, OutputPort
from app.workflow.results import NodeError, NodeExecutionResult, NodeReadiness

MUSIC_TYPES = ("audio/mpeg", "audio/wav", "audio/ogg")
DEFAULT_VOLUME = 15
MODES = ("loop", "once")
MISSING_DETAIL = "Chọn tệp nhạc (MP3, WAV, OGG) đã tải lên."
UNAVAILABLE_DETAIL = "Không tìm thấy tệp nhạc đã chọn trong kho media."
DONE_DETAIL = "Đã chọn nhạc nền."
READY_DETAIL = "Nhạc nền được trộn khi render (miễn phí). Chỉ dùng nhạc bạn có quyền sử dụng."


def _music(output):
    value = output.get("music")
    return value if isinstance(value, dict) and value.get("asset_id") else None


class MusicNodeHandler(NodeHandler):
    node_type = "music"
    outputs = (OutputPort("music", MUSIC, extract=_music),)
    config_fields = (
        ConfigField("asset_id", ASSET, label="music_file", content_types=MUSIC_TYPES, code="invalid_asset"),
        ConfigField("volume", INTEGER, default=DEFAULT_VOLUME, minimum=1, maximum=100, label="music_volume",
                    code="invalid_volume"),
        ConfigField("mode", SELECT, default="loop", options=MODES, label="music_mode", code="invalid_mode"),
    )

    def _asset(self, context, asset_id):
        if not isinstance(asset_id, str) or not asset_id:
            return None
        return context.db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == context.workspace.id))

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        if not config["asset_id"]:
            return NodeExecutionResult.blocked(MISSING_DETAIL, NodeError("missing_input", "No music file"))
        asset = self._asset(context, config["asset_id"])
        if (asset is None or asset.content_type not in MUSIC_TYPES
                or not asset_path(context.db, context.workspace.id, asset.id).is_file()):
            return NodeExecutionResult.blocked(UNAVAILABLE_DETAIL, NodeError("input_missing", "Music file missing"))
        return NodeExecutionResult.completed(DONE_DETAIL, {"music": {
            "asset_id": asset.id, "filename": asset.filename, "content_type": asset.content_type,
            "volume": config["volume"], "mode": config["mode"]}})

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        if not config["asset_id"]:
            return NodeReadiness("missing_input", MISSING_DETAIL, code="missing_input", field="asset_id")
        asset = self._asset(context, config["asset_id"])
        if asset is None or asset.content_type not in MUSIC_TYPES:
            return NodeReadiness("invalid_settings", UNAVAILABLE_DETAIL, code="invalid_asset", field="asset_id")
        return NodeReadiness("configured", READY_DETAIL)
