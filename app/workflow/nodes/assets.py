"""Studio media: lists the workspace's assets, resolved locally."""
from app.workflow.nodes.base import NodeHandler
from app.workflow.results import NodeExecutionResult


class AssetsNodeHandler(NodeHandler):
    node_type = "assets"

    def execute(self, context, node, inputs):
        listed = [{"id": asset.id, "filename": asset.filename, "content_type": asset.content_type}
                  for asset in context.assets]
        return NodeExecutionResult.completed("Đã liệt kê media trong studio.", {"assets": listed})
