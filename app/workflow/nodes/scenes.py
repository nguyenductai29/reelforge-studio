"""Scene splitter: turns a script into an ordered list of scenes, locally and for free.

It is rule-based, not an AI call. Paragraphs are the author's scenes; a
paragraph much longer than the target scene duration is split at sentence
ends, and the result is merged back down to ``max_scenes``. Each scene is
``{index, text, visual_prompt, duration}``, where ``duration`` estimates spoken
seconds at 2.5 words per second and ``visual_prompt`` carries the visual style.
"""
import re

from app.workflow.config import INTEGER, SELECT, ConfigField
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import BRIEF, SCENES, TEXT, InputPort, OutputPort
from app.workflow.results import NodeExecutionResult

DEFAULT_SCENE_SECONDS = 6
DEFAULT_MAX_SCENES = 12
WORDS_PER_SECOND = 2.5
MAX_VISUAL_PROMPT_CHARS = 500
# Appended to each visual prompt; "generic" adds nothing.
VISUAL_STYLES = {"generic": "", "cinematic": "Cinematic style.", "realistic": "Photorealistic style.",
                 "anime": "Anime style.", "documentary": "Documentary style.", "minimal": "Minimalist style."}
_LABEL = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?(?:scene|cảnh|shot|シーン)\s*\d+\s*[:.\-–—)]*\s*(?:\*\*)?\s*", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def _words(text: str) -> int:
    # Scripts without spaces (e.g. Japanese) are counted by characters instead.
    return max(len(text.split()), len(text) // 6, 1)


def _groups(items: list[str], count: int) -> list[str]:
    """``items`` joined into ``count`` consecutive groups whose sizes differ by at most one."""
    count = max(1, min(count, len(items)))
    size, extra = divmod(len(items), count)
    groups, start = [], 0
    for index in range(count):
        end = start + size + (index < extra)
        groups.append(" ".join(items[start:end]))
        start = end
    return groups


def _visual_prompt(text: str, style: str) -> str:
    suffix = VISUAL_STYLES.get(style, "")
    if not suffix:
        return text[:MAX_VISUAL_PROMPT_CHARS]
    return f"{text[:MAX_VISUAL_PROMPT_CHARS - len(suffix) - 1]} {suffix}"


def split_scenes(script: str, max_scenes: int = DEFAULT_MAX_SCENES, scene_seconds: int = DEFAULT_SCENE_SECONDS,
                 visual_style: str = "generic") -> list[dict]:
    target_words = scene_seconds * WORDS_PER_SECOND
    paragraphs = [_LABEL.sub("", part).strip() for part in re.split(r"\n\s*\n", script.strip())]
    pieces = []
    for paragraph in filter(None, paragraphs):
        sentences = [part.strip() for part in _SENTENCE_END.split(paragraph) if part.strip()]
        pieces += _groups(sentences, round(_words(paragraph) / target_words))
    if len(pieces) > max_scenes:
        pieces = _groups(pieces, max_scenes)
    scenes = []
    for index, text in enumerate(pieces, 1):
        text = " ".join(text.split())
        scenes.append({"index": index, "text": text, "visual_prompt": _visual_prompt(text, visual_style),
                       "duration": max(1, round(_words(text) / WORDS_PER_SECOND))})
    return scenes


class ScenesNodeHandler(NodeHandler):
    node_type = "scenes"
    inputs = (InputPort("script", (TEXT, BRIEF), multiple=True),)
    outputs = (OutputPort("scenes", SCENES),)
    requires = (("script",),)
    missing_input_detail = "Chưa có kịch bản để chia cảnh."
    config_fields = (
        ConfigField("scene_duration", INTEGER, default=DEFAULT_SCENE_SECONDS, minimum=2, maximum=60,
                    presets=(3, 6, 10, 15), code="invalid_duration"),
        ConfigField("max_scenes", INTEGER, default=DEFAULT_MAX_SCENES, minimum=1, maximum=20,
                    code="invalid_scene_limit"),
        ConfigField("visual_style", SELECT, default="generic", options=tuple(VISUAL_STYLES), code="invalid_style"),
    )

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        scenes = split_scenes(inputs.get("script"), config["max_scenes"], config["scene_duration"],
                              config["visual_style"])
        return NodeExecutionResult.completed("Đã chia kịch bản thành các cảnh.", {"scenes": scenes})
