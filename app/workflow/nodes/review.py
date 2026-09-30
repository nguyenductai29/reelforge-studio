"""Review: waits for a person to approve media made upstream."""
from datetime import datetime
import json

from app.models import WorkflowRunStep
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import IMAGE_ASSETS, VIDEO_ASSETS, InputPort, OutputPort
from app.workflow.results import COMPLETED, NodeExecutionResult, produced_asset_ids


def _approved_media(output):
    return [{"id": asset_id} for asset_id in produced_asset_ids(output)] or None


class ReviewNodeHandler(NodeHandler):
    node_type = "review"
    inputs = (InputPort("media", (VIDEO_ASSETS, IMAGE_ASSETS), multiple=True),)
    outputs = (OutputPort("video_assets", VIDEO_ASSETS, extract=_approved_media),)

    def execute(self, context, node, inputs):
        # Only media generated in this run can be approved; studio uploads listed upstream do not count.
        if inputs.asset_ids:
            return NodeExecutionResult.awaiting_review("Video đã tạo; cần người dùng duyệt.",
                                                       asset_ids=inputs.asset_ids)
        return NodeExecutionResult.blocked("Cần bước duyệt thủ công trước khi tiếp tục.")

    @staticmethod
    def approve(step: WorkflowRunStep, *, reviewer_id: str, now: datetime) -> None:
        try:
            output = json.loads(step.output) if step.output else {}
        except ValueError:
            output = {}
        output = output if isinstance(output, dict) else {}
        step.status, step.detail, step.finished_at = COMPLETED, "Đã được duyệt để sử dụng.", now
        step.output = json.dumps({**output, "approved_by": reviewer_id, "approved_at": now.isoformat()})
