"""Maps each node type to the one handler that executes it."""
from app.workflow.nodes import (TEXT_HANDLERS, AssetsNodeHandler, IdeaNodeHandler, ImageNodeHandler, NodeHandler,
                                PendingAITaskHandler, PendingServiceHandler, ReviewNodeHandler, ScenesNodeHandler,
                                UnsupportedNodeHandler, VideoNodeHandler)
from app.workflow.ports import (AUDIO_ASSETS, BRIEF, IMAGE_ASSETS, SUBTITLE_ASSET, TEXT, VIDEO_ASSETS,
                                InputPort, OutputPort)


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
    text = (TEXT, BRIEF)
    return (
        PendingAITaskHandler("script", (InputPort("topic", (BRIEF, TEXT), multiple=True),),
                             (OutputPort("script", TEXT),)),
        PendingAITaskHandler("voice", (InputPort("script", text, multiple=True),),
                             (OutputPort("audio_assets", AUDIO_ASSETS),)),
        PendingAITaskHandler("music", (InputPort("mood", (BRIEF, TEXT), multiple=True),),
                             (OutputPort("audio_assets", AUDIO_ASSETS),)),
        PendingServiceHandler("subtitle", (InputPort("script", text, multiple=True),
                                           InputPort("video", (VIDEO_ASSETS,))),
                              (OutputPort("subtitle_asset", SUBTITLE_ASSET),)),
        PendingServiceHandler("render", (InputPort("media", (VIDEO_ASSETS, IMAGE_ASSETS), multiple=True),
                                         InputPort("audio", (AUDIO_ASSETS,), multiple=True),
                                         InputPort("subtitle", (SUBTITLE_ASSET,))),
                              (OutputPort("rendered_video", VIDEO_ASSETS),)),
        # Publishing ends a workflow, so the node has no output yet (PUBLICATION is reserved for it).
        PendingServiceHandler("publish", (InputPort("video", (VIDEO_ASSETS,)), InputPort("title", text),
                                          InputPort("description", (TEXT,)))),
    )


def build_default_registry() -> NodeRegistry:
    registry = NodeRegistry()
    for handler in (IdeaNodeHandler(), AssetsNodeHandler(), ScenesNodeHandler(), ImageNodeHandler(),
                    VideoNodeHandler(), ReviewNodeHandler(), *(handler_type() for handler_type in TEXT_HANDLERS), *_placeholders()):
        registry.register(handler)
    return registry


# The node types a saved workflow may contain are exactly the registered ones.
default_registry = build_default_registry()
