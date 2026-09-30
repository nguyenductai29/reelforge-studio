"""The contract every node type implements."""
from typing import Any, Mapping

from app.workflow.context import ExecutionContext, NodeInputs
from app.workflow.ports import InputPort, OutputPort
from app.workflow.results import NodeExecutionResult, NodeReadiness

CONFIGURED = NodeReadiness("configured", "Bước này đã có trong sơ đồ.")
INSUFFICIENT_CREDITS_DETAIL = "Không đủ credits cho bước này."
MISSING_INPUT_DETAIL = "Chưa có dữ liệu đầu vào bắt buộc cho bước này."


class NodeHandler:
    """Behavior of one node type.

    ``inputs`` and ``outputs`` declare the node's typed ports (see
    ``app/workflow/ports.py``); the executor resolves input values from edges,
    config and context before calling ``execute``, and blocks the step instead
    when a ``requires`` group has no value. ``execute`` runs inside the caller's
    transaction once every parent has completed. It must not commit, and it
    should make database changes (such as a credit hold) only after every check
    that can block, because an exception turns the step into ``failed`` without
    undoing them. Work that takes longer than a request goes in ``result.job``;
    the executor enqueues it after the step row exists, and the worker that
    finishes it reports back through ``WorkflowExecutor.finish_step``. Raise
    ``RunRequestError`` for problems the requester must fix, such as not enough credits.
    """

    node_type: str = ""
    inputs: tuple[InputPort, ...] = ()
    # The first output is the default for edges that do not name a port.
    outputs: tuple[OutputPort, ...] = ()
    # Each group needs at least one input with a value, e.g. (("prompt", "scenes"),).
    requires: tuple[tuple[str, ...], ...] = ()
    missing_input_detail = MISSING_INPUT_DETAIL

    def execute(self, context: ExecutionContext, node: Mapping[str, Any], inputs: NodeInputs) -> NodeExecutionResult:
        raise NotImplementedError

    def missing_inputs(self, context: ExecutionContext, inputs: NodeInputs) -> list[str]:
        """Ports of the first ``requires`` group with no value, or an empty list."""
        for group in self.requires:
            if not any(inputs.has(port) for port in group):
                return list(group)
        return []

    def readiness(self, context: ExecutionContext, node: Mapping[str, Any]) -> NodeReadiness:
        """What to show before a run starts; ``context.run`` is ``None`` here."""
        return CONFIGURED

    def validate_config(self, config: Mapping[str, Any]) -> None:
        """Reject settings this node type does not understand; raise ``ValueError`` with the reason."""
        if config:
            raise ValueError("this step type has no settings")
