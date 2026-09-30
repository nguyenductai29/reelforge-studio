"""Scene splitter: turns a script into an ordered list of scenes, locally and for free.

Paragraphs become scenes. A script written as one paragraph is split into
sentences and regrouped. Each scene is ``{index, text, visual_prompt,
duration}``, where ``duration`` estimates spoken seconds at 2.5 words per second.
"""
import math
import re

from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import BRIEF, SCENES, TEXT, InputPort, OutputPort
from app.workflow.results import NodeExecutionResult

DEFAULT_MAX_SCENES = 8
WORDS_PER_SCENE = 40
WORDS_PER_SECOND = 2.5
MAX_VISUAL_PROMPT_CHARS = 500
_LABEL = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?(?:scene|cảnh|shot|シーン)\s*\d+\s*[:.\-–—)]*\s*(?:\*\*)?\s*", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def _words(text: str) -> int:
    # Scripts without spaces (e.g. Japanese) are counted by characters instead.
    return max(len(text.split()), len(text) // 6, 1)


def _groups(items: list[str], count: int) -> list[str]:
    """``items`` joined into ``count`` consecutive groups of similar size."""
    size = math.ceil(len(items) / count)
    return [" ".join(items[start:start + size]) for start in range(0, len(items), size)]


def split_scenes(script: str, max_scenes: int = DEFAULT_MAX_SCENES) -> list[dict]:
    paragraphs = [_LABEL.sub("", part).strip() for part in re.split(r"\n\s*\n", script.strip())]
    paragraphs = [part for part in paragraphs if part]
    if len(paragraphs) == 1:
        sentences = [part.strip() for part in _SENTENCE_END.split(paragraphs[0]) if part.strip()]
        target = min(max_scenes, len(sentences), max(1, round(_words(paragraphs[0]) / WORDS_PER_SCENE)))
        paragraphs = _groups(sentences, target)
    elif len(paragraphs) > max_scenes:
        paragraphs = _groups(paragraphs, max_scenes)
    scenes = []
    for index, text in enumerate(paragraphs, 1):
        text = " ".join(text.split())
        scenes.append({"index": index, "text": text, "visual_prompt": text[:MAX_VISUAL_PROMPT_CHARS],
                       "duration": max(1, round(_words(text) / WORDS_PER_SECOND))})
    return scenes


def _max_scenes(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
        raise ValueError("max_scenes must be a whole number from 1 to 20")


class ScenesNodeHandler(NodeHandler):
    node_type = "scenes"
    inputs = (InputPort("script", (TEXT, BRIEF), multiple=True),)
    outputs = (OutputPort("scenes", SCENES),)
    requires = (("script",),)
    missing_input_detail = "Chưa có kịch bản để chia cảnh."

    def validate_config(self, config):
        for key, value in config.items():
            if key != "max_scenes":
                raise ValueError(f"unknown setting {key!r}")
            _max_scenes(value)

    def execute(self, context, node, inputs):
        scenes = split_scenes(inputs.get("script"), inputs.config.get("max_scenes", DEFAULT_MAX_SCENES))
        return NodeExecutionResult.completed("Đã chia kịch bản thành các cảnh.", {"scenes": scenes})
