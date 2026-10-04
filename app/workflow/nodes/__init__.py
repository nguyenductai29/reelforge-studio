"""Node handlers, one module per executable node type."""
from app.workflow.nodes.assets import AssetsNodeHandler
from app.workflow.nodes.base import NodeHandler
from app.workflow.nodes.idea import IdeaNodeHandler
from app.workflow.nodes.image import ImageNodeHandler
from app.workflow.nodes.movie import MOVIE_HANDLERS
from app.workflow.nodes.pending import PendingAITaskHandler, PendingServiceHandler, UnsupportedNodeHandler
from app.workflow.nodes.publish import PublishNodeHandler
from app.workflow.nodes.recap import RECAP_HANDLERS
from app.workflow.nodes.render import RenderNodeHandler
from app.workflow.nodes.review import ReviewNodeHandler
from app.workflow.nodes.scenes import ScenesNodeHandler
from app.workflow.nodes.sources import SOURCE_HANDLERS
from app.workflow.nodes.subtitle import SubtitleNodeHandler
from app.workflow.nodes.text import (TEXT_HANDLERS, AIWriterNodeHandler, CTANodeHandler, HookNodeHandler,
                                     MetadataNodeHandler, RewriteNodeHandler, SummarizeNodeHandler, TextNodeHandler, TitleNodeHandler,
                                     TranslateNodeHandler)
from app.workflow.nodes.video import VideoNodeHandler
from app.workflow.nodes.voice import VoiceNodeHandler

__all__ = ["AIWriterNodeHandler", "AssetsNodeHandler", "CTANodeHandler", "HookNodeHandler", "IdeaNodeHandler", "ImageNodeHandler",
           "MOVIE_HANDLERS", "NodeHandler", "PendingAITaskHandler", "MetadataNodeHandler", "PendingServiceHandler", "PublishNodeHandler", "RECAP_HANDLERS", "RenderNodeHandler", "ReviewNodeHandler",
           "RewriteNodeHandler", "SOURCE_HANDLERS", "ScenesNodeHandler", "SubtitleNodeHandler", "SummarizeNodeHandler", "TEXT_HANDLERS", "TextNodeHandler", "TitleNodeHandler",
           "TranslateNodeHandler", "UnsupportedNodeHandler", "VideoNodeHandler", "VoiceNodeHandler"]
