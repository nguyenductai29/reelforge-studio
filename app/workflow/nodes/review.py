"""Review: waits for a person to approve media made upstream."""
from datetime import datetime
import json

from app.models import WorkflowRunStep
from app.workflow.nodes.base import NodeHandler
from app.workflow.results import COMPLETED, NodeExecutionResult


class ReviewNodeHandler(NodeHandler):
    node_type = "review"

    def execute(self, context, node, inputs):
        if inputs.asset_ids:
            return NodeExecutionResult.awaiting_review("Video đã tạo; cần người dùng duyệt.")
        # Nothing upstream produced media, so there is nothing to approve.
        return NodeExecutionResult.blocked("Cần bước duyệt thủ công trước khi tiếp tục.")

    @staticmethod
    def approve(step: WorkflowRunStep, *, reviewer_id: str, now: datetime) -> None:
        step.status, step.detail, step.finished_at = COMPLETED, "Đã được duyệt để sử dụng.", now
        step.output = json.dumps({"approved_by": reviewer_id, "approved_at": now.isoformat()})
