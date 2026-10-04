"""Starter workflows (Phase 9, 10, 11, 12).

Social video templates (YouTube Short, YouTube Landscape, TikTok Short, Facebook Reel)::

    Idea → AI Writer → Scene Splitter ─┬→ Video ────────────┐
                                        ├→ Voice ─┬──────────┤
                                        └→ Subtitle ←┘ (audio)├→ Render → Review → Publish
    AI Writer → Metadata ───────────────────────────────────────────────────────────┘

Repurpose Existing Content: the same pipeline, written from a URL Source's text
instead of an idea (the project topic still guides the AI Writer).

Movie Recap / Review (use only content you are authorized to use)::

    Uploaded Media Source → Transcript → Story Analysis → Recap Script ─┬→ Voice ─────────┐
                     │            └──────────────────────┬──────────────┤                  │
                     └──────────────────→ Match Source Scenes ←──────────┘                  │
                                          → Extract Source Clips → Render ←── Subtitle ←────┘
                                          → Review → Publish  (Recap Script → Metadata → Publish)

Movie Review is the same graph with the Recap Script set to a review with light
spoilers.

Movie Review and Movie Recap from a movie source (migration 0027, app/workflow/nodes/movie.py): the
movie stays in the operator's Google Drive and the movie worker prepares it::

    Movie Source → Prepare Movie ─audio─→ Transcript ────────────┐
                        └─frames─→ Visual Analysis ──────────────┴→ Movie Timeline → Story Analysis
                                                                        └────────────┬→ Review Script
    Review Script ─┬→ Voice ─┬→ Clip Selector → Extract Source Clips → Render → Review → Publish
                   └→ Subtitle ←┘ (audio)                    Review Script → Metadata → Publish

Article to Video and Product Video illustrate each scene with an AI
image instead of an AI clip (Render shows the images as stills)::

    URL Source | Idea → AI Writer → Scene Splitter ─┬→ Image ─────────────┐
                                                     ├→ Voice ─┬───────────┤
                                                     └→ Subtitle ←┘ (audio) ├→ Render → Review → Publish

Templates never name an AI tool: every AI step leaves ``tool_id`` empty, so the
run uses the workspace's default model for its task, else the first enabled
compatible one (``ExecutionContext.find_tool``). Source steps are left empty:
the owner picks the URL or the uploaded file before running.
"""
from typing import Any

# The notice every Movie Recap workflow shows; the API returns it with the template.
RIGHTS_NOTICE = "Use only content you are authorized to use."

TEMPLATES: dict[str, dict[str, Any]] = {
    "youtube_short": {"kind": "social", "platform": "youtube_shorts", "script_seconds": 50, "scene_seconds": 5,
                      "max_scenes": 12, "clip": "6s", "aspect_ratio": "9:16", "line_chars": 32},
    "youtube_landscape": {"kind": "social", "platform": "youtube", "script_seconds": 120, "scene_seconds": 7,
                          "max_scenes": 20, "clip": "8s", "aspect_ratio": "16:9", "line_chars": 42},
    "tiktok_short": {"kind": "social", "platform": "tiktok", "script_seconds": 45, "scene_seconds": 5,
                     "max_scenes": 10, "clip": "6s", "aspect_ratio": "9:16", "line_chars": 32},
    "facebook_reel": {"kind": "social", "platform": "facebook", "script_seconds": 45, "scene_seconds": 5,
                      "max_scenes": 10, "clip": "6s", "aspect_ratio": "9:16", "line_chars": 32},
    "repurpose": {"kind": "repurpose", "platform": "youtube_shorts", "script_seconds": 60, "scene_seconds": 5,
                  "max_scenes": 12, "clip": "6s", "aspect_ratio": "9:16", "line_chars": 32},
    "movie_recap": {"kind": "movie_recap", "platform": "youtube_shorts", "script_seconds": 90, "scene_count": 8,
                    "line_chars": 32, "notice": RIGHTS_NOTICE},
    "movie_review": {"kind": "movie_recap", "platform": "youtube_shorts", "script_seconds": 90, "scene_count": 8,
                     "line_chars": 32, "style": "review", "spoiler_level": "light", "notice": RIGHTS_NOTICE},
    "movie_source_review": {"kind": "movie_auto", "platform": "youtube_shorts", "mode": "review",
                            "spoiler_level": "light", "script_seconds": 90, "section_count": 8, "line_chars": 32,
                            "notice": RIGHTS_NOTICE},
    "movie_source_recap": {"kind": "movie_auto", "platform": "youtube_shorts", "mode": "recap",
                           "spoiler_level": "full", "script_seconds": 120, "section_count": 10, "line_chars": 32,
                           "notice": RIGHTS_NOTICE},
    "article_to_video": {"kind": "slideshow", "source": "source_url", "platform": "youtube_shorts",
                         "script_seconds": 60, "scene_seconds": 6, "max_scenes": 10, "aspect_ratio": "9:16",
                         "line_chars": 32},
    "product_video": {"kind": "slideshow", "source": "idea", "platform": "youtube_shorts", "script_seconds": 30,
                      "scene_seconds": 5, "max_scenes": 6, "aspect_ratio": "9:16", "line_chars": 32,
                      "instructions": "A product promo: a hook, three concrete benefits, then one call to action."},
}
X = 300


def _node(node_id: str, node_type: str, column: float, row: float, config: dict | None = None) -> dict:
    node = {"id": node_id, "type": node_type, "x": column * X, "y": 80 + row * 190}
    if config:
        node["config"] = config
    return node


def _edge(source: str, source_handle: str, target: str, target_handle: str) -> dict:
    return {"source": source, "target": target, "sourceHandle": source_handle, "targetHandle": target_handle}


def _social(settings: dict, *, repurpose: bool) -> dict:
    first = _node("source", "source_url", 0, 1) if repurpose else _node("idea", "idea", 0, 1)
    nodes = [
        first,
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
        _edge("source", "text", "writer", "source") if repurpose else _edge("idea", "topic", "writer", "prompt"),
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
        *([] if repurpose else [_edge("idea", "topic", "metadata", "topic")]),
        _edge("writer", "script", "metadata", "source"),
        _edge("metadata", "metadata", "publish", "metadata"),
    ]
    return {"nodes": nodes, "edges": edges}


def _slideshow(settings: dict) -> dict:
    """A source or idea → script → scenes → one AI image, narration and subtitles per scene → render."""
    from_page = settings["source"] == "source_url"
    writer = {"platform": settings["platform"], "duration": settings["script_seconds"]}
    if settings.get("instructions"):
        writer["instructions"] = settings["instructions"]
    nodes = [
        _node("source", "source_url", 0, 1) if from_page else _node("idea", "idea", 0, 1),
        _node("writer", "ai_writer", 1, 1, writer),
        _node("scenes", "scenes", 2, 1, {"scene_duration": settings["scene_seconds"],
                                         "max_scenes": settings["max_scenes"]}),
        _node("image", "image", 3, 0, {"aspect_ratio": settings["aspect_ratio"]}),
        _node("voice", "voice", 3, 1),
        _node("subtitle", "subtitle", 4, 2, {"max_chars": settings["line_chars"]}),
        _node("render", "render", 5, 1),
        _node("review", "review", 6, 1),
        _node("metadata", "metadata", 3, 3, {"platform": settings["platform"]}),
        _node("publish", "publish", 7, 1),
    ]
    edges = [
        _edge("source", "text", "writer", "source") if from_page else _edge("idea", "topic", "writer", "prompt"),
        _edge("writer", "script", "scenes", "script"),
        _edge("scenes", "scenes", "image", "scenes"),
        _edge("scenes", "scenes", "voice", "scenes"),
        _edge("scenes", "scenes", "subtitle", "scenes"),
        _edge("voice", "audio_assets", "subtitle", "audio"),
        _edge("image", "image_assets", "render", "media"),
        _edge("voice", "audio_assets", "render", "audio"),
        _edge("subtitle", "subtitle_asset", "render", "subtitle"),
        _edge("render", "rendered_video", "review", "media"),
        _edge("review", "video_assets", "publish", "video"),
        *([] if from_page else [_edge("idea", "topic", "metadata", "topic")]),
        _edge("writer", "script", "metadata", "source"),
        _edge("metadata", "metadata", "publish", "metadata"),
    ]
    return {"nodes": nodes, "edges": edges}


def _movie_recap(settings: dict) -> dict:
    recap = {"platform": settings["platform"], "duration": settings["script_seconds"],
             "scene_count": settings["scene_count"]}
    recap.update({key: settings[key] for key in ("style", "spoiler_level") if key in settings})
    nodes = [
        _node("source", "source_media", 0, 1),
        _node("transcript", "transcribe", 1, 1),
        _node("analysis", "story_analysis", 2, 0),
        _node("recap", "recap_script", 3, 1, recap),
        _node("voice", "voice", 4, 0),
        _node("match", "match_scenes", 4, 2),
        _node("clips", "extract_clips", 5, 2),
        _node("subtitle", "subtitle", 5, 0, {"max_chars": settings["line_chars"]}),
        _node("render", "render", 6, 1),
        _node("review", "review", 7, 1),
        _node("metadata", "metadata", 4, 3, {"platform": settings["platform"]}),
        _node("publish", "publish", 8, 1),
    ]
    edges = [
        _edge("source", "source", "transcript", "source"),
        _edge("transcript", "transcript", "analysis", "source"),
        _edge("analysis", "analysis", "recap", "analysis"),
        _edge("transcript", "transcript", "recap", "source"),
        _edge("recap", "scenes", "voice", "scenes"),
        _edge("recap", "scenes", "match", "scenes"),
        _edge("transcript", "transcript", "match", "transcript"),
        _edge("source", "video", "match", "video"),
        _edge("match", "source_clips", "clips", "source_clips"),
        _edge("recap", "scenes", "subtitle", "scenes"),
        _edge("voice", "audio_assets", "subtitle", "audio"),
        _edge("clips", "video_assets", "render", "media"),
        _edge("voice", "audio_assets", "render", "audio"),
        _edge("subtitle", "subtitle_asset", "render", "subtitle"),
        _edge("render", "rendered_video", "review", "media"),
        _edge("review", "video_assets", "publish", "video"),
        _edge("recap", "title", "metadata", "topic"),
        _edge("recap", "script", "metadata", "source"),
        _edge("metadata", "metadata", "publish", "metadata"),
    ]
    return {"nodes": nodes, "edges": edges}


def _movie_auto(settings: dict) -> dict:
    """A movie source → prepared once → transcript + described frames → timeline → story → review script →
    narration, excerpts, subtitles → render. The Movie Source step is left empty (or set by the caller)."""
    script = {"mode": settings["mode"], "spoiler_level": settings["spoiler_level"],
              "duration": settings["script_seconds"], "section_count": settings["section_count"],
              "platform": settings["platform"]}
    nodes = [
        _node("movie", "movie_source", 0, 1),
        _node("prepare", "movie_prepare", 1, 1),
        _node("transcript", "transcribe", 2, 0),
        _node("visual", "visual_analysis", 2, 2),
        _node("timeline", "movie_timeline", 3, 1),
        _node("analysis", "story_analysis", 4, 0),
        _node("script", "review_script", 5, 1, script),
        _node("voice", "voice", 6, 0),
        _node("subtitle", "subtitle", 7, 0, {"max_chars": settings["line_chars"]}),
        _node("select", "clip_select", 7, 2),
        _node("clips", "extract_clips", 8, 2),
        _node("render", "render", 9, 1),
        _node("review", "review", 10, 1),
        _node("metadata", "metadata", 6, 3, {"platform": settings["platform"]}),
        _node("publish", "publish", 11, 1),
    ]
    edges = [
        _edge("movie", "movie", "prepare", "movie"),
        _edge("prepare", "audio", "transcript", "media"),
        _edge("prepare", "frames", "visual", "frames"),
        _edge("prepare", "movie", "visual", "movie"),
        _edge("transcript", "transcript", "timeline", "transcript"),
        _edge("visual", "visual", "timeline", "visual"),
        _edge("prepare", "movie", "timeline", "movie"),
        _edge("timeline", "source", "analysis", "source"),
        _edge("analysis", "analysis", "script", "analysis"),
        _edge("timeline", "timeline", "script", "timeline"),
        _edge("prepare", "movie", "script", "movie"),
        _edge("script", "scenes", "voice", "scenes"),
        _edge("script", "scenes", "subtitle", "scenes"),
        _edge("voice", "audio_assets", "subtitle", "audio"),
        _edge("script", "scenes", "select", "scenes"),
        _edge("prepare", "movie", "select", "movie"),
        _edge("timeline", "timeline", "select", "timeline"),
        _edge("voice", "audio_assets", "select", "audio"),
        _edge("select", "source_clips", "clips", "source_clips"),
        _edge("clips", "video_assets", "render", "media"),
        _edge("voice", "audio_assets", "render", "audio"),
        _edge("subtitle", "subtitle_asset", "render", "subtitle"),
        _edge("render", "rendered_video", "review", "media"),
        _edge("review", "video_assets", "publish", "video"),
        _edge("script", "title", "metadata", "topic"),
        _edge("script", "script", "metadata", "source"),
        _edge("metadata", "metadata", "publish", "metadata"),
    ]
    return {"nodes": nodes, "edges": edges}


def template_graph(template_id: str) -> dict:
    """The workflow graph of a starter template; ``KeyError`` for an unknown name."""
    settings = TEMPLATES[template_id]
    if settings["kind"] == "movie_auto":
        return _movie_auto(settings)
    if settings["kind"] == "movie_recap":
        return _movie_recap(settings)
    if settings["kind"] == "slideshow":
        return _slideshow(settings)
    return _social(settings, repurpose=settings["kind"] == "repurpose")


def describe_templates() -> list[dict]:
    return [{"id": template_id, **settings, "node_types": [node["type"] for node in template_graph(template_id)["nodes"]]}
            for template_id, settings in TEMPLATES.items()]
