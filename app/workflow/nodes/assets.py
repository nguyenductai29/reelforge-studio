"""Studio media: lists the workspace's assets, resolved locally."""
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import AUDIO_ASSETS, IMAGE_ASSETS, VIDEO_ASSETS, OutputPort
from app.workflow.results import NodeExecutionResult


def _of_kind(prefix):
    def extract(output):
        listed = output.get("assets")
        return [asset for asset in listed if isinstance(asset, dict)
                and str(asset.get("content_type", "")).startswith(prefix)] if isinstance(listed, list) else None
    return extract


class AssetsNodeHandler(NodeHandler):
    node_type = "assets"
    # Derived from the stored "assets" list, so older outputs expose the same ports.
    outputs = (OutputPort("video_assets", VIDEO_ASSETS, extract=_of_kind("video/")),
               OutputPort("image_assets", IMAGE_ASSETS, extract=_of_kind("image/")),
               OutputPort("audio_assets", AUDIO_ASSETS, extract=_of_kind("audio/")))

    def execute(self, context, node, inputs):
        listed = [{"id": asset.id, "filename": asset.filename, "content_type": asset.content_type}
                  for asset in context.assets]
        return NodeExecutionResult.completed("Đã liệt kê media trong studio.", {"assets": listed})
