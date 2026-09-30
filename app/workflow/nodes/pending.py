"""Node types the editor offers but the server cannot execute yet, and unknown types."""
from app.workflow.nodes.base import NodeHandler
from app.workflow.results import NodeError, NodeExecutionResult, NodeReadiness


def pending_ai_task(context, task: str) -> NodeExecutionResult:
    if task in context.enabled_tasks:
        return NodeExecutionResult.blocked("Đã chọn model, nhưng chưa kết nối API provider.")
    return NodeExecutionResult.blocked("Chưa chọn công cụ AI cho tác vụ này.")


class PendingAITaskHandler(NodeHandler):
    """An AI task (script, image, voice, music) whose provider is not connected yet."""

    def __init__(self, node_type: str):
        self.node_type = node_type

    def execute(self, context, node, inputs):
        return pending_ai_task(context, self.node_type)

    def readiness(self, context, node):
        if self.node_type in context.enabled_tasks:
            return NodeReadiness("needs_connection", "Đã chọn model; cần kết nối provider trước khi chạy.")
        return NodeReadiness("missing_tool", "Chưa chọn công cụ AI cho tác vụ này.")


class PendingServiceHandler(NodeHandler):
    """A processing step (scenes, subtitle, render, publish) without an executor yet."""

    def __init__(self, node_type: str):
        self.node_type = node_type

    def execute(self, context, node, inputs):
        return NodeExecutionResult.blocked("Bước này chưa có bộ thực thi.")

    def readiness(self, context, node):
        return NodeReadiness("needs_connection", "Cần kết nối dịch vụ thực thi trước khi chạy.")


UNSUPPORTED_DETAIL = "Loại bước này chưa được hỗ trợ."


class UnsupportedNodeHandler(NodeHandler):
    """Fallback for a type with no registered handler, e.g. in an old snapshot."""

    def execute(self, context, node, inputs):
        return NodeExecutionResult.blocked(UNSUPPORTED_DETAIL, NodeError(
            "unsupported_node_type", f"No handler is registered for node type {node.get('type')!r}"))

    def readiness(self, context, node):
        return NodeReadiness("unsupported_node", UNSUPPORTED_DETAIL)
