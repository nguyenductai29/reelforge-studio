"""Node handlers, one module per executable node type."""
from app.workflow.nodes.assets import AssetsNodeHandler
from app.workflow.nodes.base import NodeHandler
from app.workflow.nodes.idea import IdeaNodeHandler
from app.workflow.nodes.image import ImageNodeHandler
from app.workflow.nodes.pending import PendingAITaskHandler, PendingServiceHandler, UnsupportedNodeHandler
from app.workflow.nodes.render import RenderNodeHandler
from app.workflow.nodes.review import ReviewNodeHandler
from app.workflow.nodes.scenes import ScenesNodeHandler
from app.workflow.nodes.subtitle import SubtitleNodeHandler
from app.workflow.nodes.text import (TEXT_HANDLERS, AIWriterNodeHandler, CTANodeHandler, HookNodeHandler,
                                     RewriteNodeHandler, SummarizeNodeHandler, TextNodeHandler, TitleNodeHandler,
                                     TranslateNodeHandler)
from app.workflow.nodes.video import VideoNodeHandler
from app.workflow.nodes.voice import VoiceNodeHandler

__all__ = ["AIWriterNodeHandler", "AssetsNodeHandler", "CTANodeHandler", "HookNodeHandler", "IdeaNodeHandler", "ImageNodeHandler",
           "NodeHandler", "PendingAITaskHandler", "PendingServiceHandler", "RenderNodeHandler", "ReviewNodeHandler",
           "RewriteNodeHandler", "ScenesNodeHandler", "SubtitleNodeHandler", "SummarizeNodeHandler", "TEXT_HANDLERS", "TextNodeHandler", "TitleNodeHandler",
           "TranslateNodeHandler", "UnsupportedNodeHandler", "VideoNodeHandler", "VoiceNodeHandler"]
