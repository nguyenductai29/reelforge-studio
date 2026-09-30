"""The contract every node type implements."""
from typing import Any, Mapping

from app.workflow.context import ExecutionContext, NodeInputs
from app.workflow.results import NodeExecutionResult, NodeReadiness

CONFIGURED = NodeReadiness("configured", "Bước này đã có trong sơ đồ.")


class NodeHandler:
    """Behavior of one node type.

    ``execute`` runs inside the caller's transaction once every parent has
    completed. It must not commit, and it should make database changes (such as
    a credit hold) only after every check that can block, because an exception
    turns the step into ``failed`` without undoing them. Work that takes longer
    than a request goes in ``result.job``; the executor enqueues it after the
    step row exists, and the worker that finishes it reports back through
    ``WorkflowExecutor.finish_step``. Raise ``RunRequestError`` only to reject
    the whole run request.
    """

    node_type: str = ""

    def execute(self, context: ExecutionContext, node: Mapping[str, Any], inputs: NodeInputs) -> NodeExecutionResult:
        raise NotImplementedError

    def readiness(self, context: ExecutionContext, node: Mapping[str, Any]) -> NodeReadiness:
        """What to show before a run starts; ``context.run`` is ``None`` here."""
        return CONFIGURED
