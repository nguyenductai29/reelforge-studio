"""Starter workflows for the full social-video pipeline (Phase 9).

Each template is a graph with typed edges and node settings:

    Idea → AI Writer → Scene Splitter ─┬→ Video ────────────┐
                                        ├→ Voice ─┬──────────┤
                                        └→ Subtitle ←┘ (audio)├→ Render → Review → Publish
    AI Writer → Metadata ───────────────────────────────────────────────────────────┘

Templates never name an AI tool: every AI step leaves ``tool_id`` empty, so the
run uses the first enabled compatible model for its task (text, video, voice),
in the order the workspace added them (``ExecutionContext.find_tool``). A model
chosen later in a step's settings is kept. The project's title and topic reach
the Idea step at run time; they are never copied into the workflow.
"""
from typing import Any

# name → (platform, AI Writer seconds, scene seconds, max scenes, clip length, aspect ratio, subtitle line length)
TEMPLATES: dict[str, dict[str, Any]] = {
    "youtube_short": {"platform": "youtube_shorts", "script_seconds": 50, "scene_seconds": 5, "max_scenes": 12,
                      "clip": "6s", "aspect_ratio": "9:16", "line_chars": 32},
    "youtube_landscape": {"platform": "youtube", "script_seconds": 120, "scene_seconds": 7, "max_scenes": 20,
                          "clip": "8s", "aspect_ratio": "16:9", "line_chars": 42},
}
X = 300


def _node(node_id: str, node_type: str, column: float, row: float, config: dict | None = None) -> dict:
    node = {"id": node_id, "type": node_type, "x": column * X, "y": 80 + row * 190}
    if config:
        node["config"] = config
    return node


def _edge(source: str, source_handle: str, target: str, target_handle: str) -> dict:
    return {"source": source, "target": target, "sourceHandle": source_handle, "targetHandle": target_handle}


def template_graph(template_id: str) -> dict:
    """The workflow graph of a starter template; ``KeyError`` for an unknown name."""
    settings = TEMPLATES[template_id]
    nodes = [
        _node("idea", "idea", 0, 1),
        _node("writer", "ai_writer", 1, 1, {"platform": settings["platform"], "duration": settings["script_seconds"]}),
        _node("scenes", "scenes", 2, 1, {"scene_duration": settings["scene_seconds"],
                                         "max_scenes": settings["max_scenes"]}),
        _node("video", "video", 3, 0, {"aspect_ratio": settings["aspect_ratio"], "duration": settings["clip"]}),
        _node("voice", "voice", 3, 1),
        _node("subtitle", "subtitle", 4, 2, {"max_chars": settings["line_chars"]}),
        _node("render", "render", 5, 1),
        _node("review", "review", 6, 1),
        _node("metadata", "metadata", 3, 3, {"platform": settings["platform"]}),
        _node("publish", "publish", 7, 1),
    ]
    edges = [
        _edge("idea", "topic", "writer", "prompt"),
        _edge("writer", "script", "scenes", "script"),
        _edge("scenes", "scenes", "video", "scenes"),
        _edge("scenes", "scenes", "voice", "scenes"),
        _edge("scenes", "scenes", "subtitle", "scenes"),
        _edge("voice", "audio_assets", "subtitle", "audio"),
        _edge("video", "video_assets", "render", "media"),
        _edge("voice", "audio_assets", "render", "audio"),
        _edge("subtitle", "subtitle_asset", "render", "subtitle"),
        _edge("render", "rendered_video", "review", "media"),
        _edge("review", "video_assets", "publish", "video"),
        _edge("idea", "topic", "metadata", "topic"),
        _edge("writer", "script", "metadata", "source"),
        _edge("metadata", "metadata", "publish", "metadata"),
    ]
    return {"nodes": nodes, "edges": edges}


def describe_templates() -> list[dict]:
    return [{"id": template_id, **settings, "node_types": [node["type"] for node in template_graph(template_id)["nodes"]]}
            for template_id, settings in TEMPLATES.items()]
