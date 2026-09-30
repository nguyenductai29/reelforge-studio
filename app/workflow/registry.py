"""Maps each node type to the one handler that executes it."""
from app.workflow.nodes import (AssetsNodeHandler, IdeaNodeHandler, NodeHandler, PendingAITaskHandler,
                                PendingServiceHandler, ReviewNodeHandler, UnsupportedNodeHandler, VideoNodeHandler)


class NodeRegistry:
    def __init__(self, fallback: NodeHandler | None = None):
        self._handlers: dict[str, NodeHandler] = {}
        self._fallback = fallback or UnsupportedNodeHandler()

    def register(self, handler: NodeHandler) -> NodeHandler:
        if not handler.node_type:
            raise ValueError("Handler must declare a node_type")
        if handler.node_type in self._handlers:
            raise ValueError(f"Node type {handler.node_type!r} is already registered")
        self._handlers[handler.node_type] = handler
        return handler

    def resolve(self, node_type: str) -> NodeHandler:
        """The registered handler, or the fallback that blocks unknown types."""
        return self._handlers.get(node_type, self._fallback)

    def __contains__(self, node_type: str) -> bool:
        return node_type in self._handlers

    @property
    def node_types(self) -> frozenset[str]:
        return frozenset(self._handlers)


def build_default_registry() -> NodeRegistry:
    registry = NodeRegistry()
    for handler in (IdeaNodeHandler(), AssetsNodeHandler(), VideoNodeHandler(), ReviewNodeHandler()):
        registry.register(handler)
    for node_type in ("script", "image", "voice", "music"):
        registry.register(PendingAITaskHandler(node_type))
    for node_type in ("scenes", "subtitle", "render", "publish"):
        registry.register(PendingServiceHandler(node_type))
    return registry


# The node types a saved workflow may contain are exactly the registered ones.
default_registry = build_default_registry()
