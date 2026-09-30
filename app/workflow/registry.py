"""Maps each node type to the one handler that executes it."""
from app.workflow.nodes import (TEXT_HANDLERS, AssetsNodeHandler, IdeaNodeHandler, ImageNodeHandler, NodeHandler,
                                PendingAITaskHandler, PublishNodeHandler, RenderNodeHandler, ReviewNodeHandler,
                                ScenesNodeHandler, SubtitleNodeHandler, UnsupportedNodeHandler, VideoNodeHandler,
                                VoiceNodeHandler)
from app.workflow.ports import AUDIO_ASSETS, BRIEF, TEXT, InputPort, OutputPort


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

    def handlers(self) -> dict[str, NodeHandler]:
        return dict(self._handlers)


def _placeholders():
    """Types without an executor yet, with the ports they will have."""
    return (
        PendingAITaskHandler("script", (InputPort("topic", (BRIEF, TEXT), multiple=True),),
                             (OutputPort("script", TEXT),)),
        PendingAITaskHandler("music", (InputPort("mood", (BRIEF, TEXT), multiple=True),),
                             (OutputPort("audio_assets", AUDIO_ASSETS),)),
    )


def build_default_registry() -> NodeRegistry:
    registry = NodeRegistry()
    for handler in (IdeaNodeHandler(), AssetsNodeHandler(), ScenesNodeHandler(), ImageNodeHandler(),
                    VideoNodeHandler(), VoiceNodeHandler(), SubtitleNodeHandler(), RenderNodeHandler(),
                    ReviewNodeHandler(), PublishNodeHandler(), *(handler_type() for handler_type in TEXT_HANDLERS),
                    *_placeholders()):
        registry.register(handler)
    return registry


# The node types a saved workflow may contain are exactly the registered ones.
default_registry = build_default_registry()
