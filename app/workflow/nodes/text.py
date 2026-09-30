"""Text nodes: each builds a prompt from its config and upstream text, then queues one generation.

The text worker (``app/text_worker.py``) calls the provider outside any
database transaction and reports back through ``WorkflowExecutor.finish_step``;
``output_from`` turns the normalized ``TextResult`` into the step output.

Credits: ``TEXT_CREDITS_PER_GENERATION`` (default 1) is held when the step is
queued (``text-reserve:<step>``), charged on success as a usage event
(``text:<step>``), and refunded if generation fails (``text-refund:<step>``).
"""
import math
import re
from typing import Any, Mapping

from app import usage
from app.providers.text import TEXT_PROVIDERS, TEXT_TASK, TextResult, text_credit_cost, text_provider_config_issue
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, NodeHandler
from app.workflow.ports import BRIEF, PROJECT_TOPIC, TEXT, InputPort, OutputPort
from app.workflow.results import JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError

QUEUED_DETAIL = "Đã xếp hàng tạo nội dung."
MISSING_TOOL_DETAIL = "Chọn model văn bản được hỗ trợ trong Công cụ AI."
MISSING_INPUT_DETAIL = "Chưa có nội dung đầu vào cho bước này."
MAX_SOURCE_CHARS = 60_000
WORDS_PER_SECOND = 2.5
LANGUAGES = {"vi": "Vietnamese", "en": "English", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
             "th": "Thai", "id": "Indonesian", "es": "Spanish", "fr": "French", "de": "German",
             "pt": "Portuguese", "it": "Italian", "ru": "Russian", "hi": "Hindi"}
_LANGUAGE_CODE = re.compile(r"[a-z]{2,3}(-[A-Za-z]{2,4})?\Z")
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def _text(limit):
    def check(value):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"must be text of at most {limit} characters")
    return check


def _integer(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"must be a whole number from {low} to {high}")
    return check


def _language(value):
    if not isinstance(value, str) or not _LANGUAGE_CODE.fullmatch(value):
        raise ValueError("must be a language code such as vi, en or ja")


def _temperature(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2:
        raise ValueError("must be a number from 0 to 2")


COMMON_SETTINGS = {"tool_id": _text(36), "language": _language, "tone": _text(60), "platform": _text(40),
                   "instructions": _text(1000), "max_tokens": _integer(64, 8192), "temperature": _temperature}


def language_name(code: str) -> str:
    return LANGUAGES.get(code.split("-")[0], code)


def _first_option(key):
    def extract(output):
        for value in (output.get(key), *(output.get("options") or [])[:1]):
            if isinstance(value, str) and value.strip():
                return value
        text = output.get("text")
        return text.strip().splitlines()[0] if isinstance(text, str) and text.strip() else None
    return extract


# Input ports shared by the text nodes. Written text (TEXT) goes to "source" or "text";
# a short idea (BRIEF, e.g. the project topic) goes to "prompt" or "topic".
SOURCE = InputPort("source", (TEXT,), multiple=True)
TEXT_IN = InputPort("text", (TEXT, BRIEF), multiple=True, context=PROJECT_TOPIC)
TOPIC_IN = InputPort("topic", (BRIEF, TEXT), multiple=True, context=PROJECT_TOPIC)


class TextNodeHandler(NodeHandler):
    """Shared behavior; subclasses declare ports and write the prompt in ``build_prompt``.

    The first output port names the key the generated text is stored under.
    """

    settings: Mapping[str, Any] = COMMON_SETTINGS
    inputs = (TEXT_IN,)
    outputs = (OutputPort("text", TEXT),)
    requires = (("text",),)
    missing_input_detail = MISSING_INPUT_DETAIL
    max_tokens = 2048
    system_prompt = ("You are a senior content writer for social video. Return only the requested content, "
                     "with no preamble, notes or markdown headings.")

    def validate_config(self, config):
        for key, value in config.items():
            check = self.settings.get(key)
            if check is None:
                raise ValueError(f"unknown setting {key!r}")
            try:
                check(value)
            except ValueError as exc:
                raise ValueError(f"{key} {exc}") from None

    # Inputs -----------------------------------------------------------------

    @staticmethod
    def text_input(inputs, port: str) -> str:
        """A text input's resolved value, trimmed and capped."""
        value = inputs.get(port)
        return value.strip()[:MAX_SOURCE_CHARS] if isinstance(value, str) else ""

    def language(self, context, config) -> str:
        return config.get("language") or context.workspace_settings.get("default_language") or "vi"

    @staticmethod
    def extras(config) -> list[str]:
        lines = []
        if config.get("tone"):
            lines.append(f"Tone: {config['tone']}.")
        if config.get("platform"):
            lines.append(f"Platform: {config['platform']}.")
        if config.get("instructions"):
            lines.append(f"Additional instructions: {config['instructions']}")
        return lines

    @staticmethod
    def quoted(label: str, text: str) -> str:
        return f'{label}:\n"""\n{text}\n"""'

    def build_prompt(self, context, config, inputs) -> str | None:
        """The user prompt, or ``None`` when the node has nothing to work from."""
        raise NotImplementedError

    def output_from(self, payload: Mapping[str, Any], result: TextResult) -> dict[str, Any]:
        return {self.outputs[0].name: result.text, "provider": result.provider, "model": result.model,
                "usage": result.usage.as_dict(), "language": payload.get("language")}

    # Execution --------------------------------------------------------------

    def _tool(self, context, config):
        return context.find_tool(TEXT_TASK, TEXT_PROVIDERS, config.get("tool_id"))

    def execute(self, context, node, inputs):
        config = inputs.config
        tool = self._tool(context, config)
        if tool is None:
            return NodeExecutionResult.blocked(MISSING_TOOL_DETAIL, NodeError("missing_tool", "No enabled text tool"))
        if issue := text_provider_config_issue(tool.provider):
            return NodeExecutionResult.blocked(issue[1], NodeError(issue[0], issue[1]))
        prompt = self.build_prompt(context, config, inputs)
        if not prompt:
            return NodeExecutionResult.blocked(MISSING_INPUT_DETAIL, NodeError("missing_input", "Nothing to work from"))
        cost = text_credit_cost()
        step = context.step_for(node)
        try:
            usage.post_credit(context.db, context.workspace.id, -cost, "text_reserve", f"text-reserve:{step.id}")
        except ValueError as exc:
            raise RunRequestError(402, "Not enough credits for this step", code="insufficient_credits",
                                  step_detail=INSUFFICIENT_CREDITS_DETAIL) from exc
        language = self.language(context, config)
        payload = {"kind": "text.generate", "node_type": self.node_type, "provider": tool.provider,
                   "model": tool.model, "tool_id": tool.id, "system_prompt": self.system_prompt, "prompt": prompt,
                   "temperature": config.get("temperature"), "max_tokens": config.get("max_tokens", self.max_tokens),
                   "response_format": "text", "language": language, "credits": cost}
        return NodeExecutionResult.queued(QUEUED_DETAIL, JobRequest("text", payload),
                                          {"provider": tool.provider, "model": tool.model},
                                          metadata={"credits_reserved": cost})

    def readiness(self, context, node):
        config = node.get("config") if isinstance(node.get("config"), Mapping) else {}
        cost = text_credit_cost()
        tool = self._tool(context, config)
        if tool is None:
            return NodeReadiness("missing_tool", MISSING_TOOL_DETAIL, credits=cost)
        if issue := text_provider_config_issue(tool.provider):
            return NodeReadiness(issue[0], issue[1], credits=cost)
        if context.credit_balance < cost:
            return NodeReadiness("insufficient_credits", f"Cần {cost} credits; hiện có {context.credit_balance}.",
                                 credits=cost)
        return NodeReadiness("ready", f"Sẵn sàng tạo nội dung; dự kiến giữ {cost} credits.", credits=cost)


class AIWriterNodeHandler(TextNodeHandler):
    node_type = "ai_writer"
    settings = {**COMMON_SETTINGS, "prompt": _text(3000), "duration": _integer(5, 3600)}
    # The brief comes from a connected idea, else the "prompt" setting, else the project topic.
    inputs = (InputPort("prompt", (BRIEF, TEXT), multiple=True, config_key="prompt", context=PROJECT_TOPIC), SOURCE)
    outputs = (OutputPort("script", TEXT, keys=("script", "text")),)
    requires = (("prompt", "source"),)

    def build_prompt(self, context, config, inputs):
        brief = self.text_input(inputs, "prompt")
        source = self.text_input(inputs, "source")
        if not brief and not source:
            return None
        lines = [f"Write engaging, original video content in {language_name(self.language(context, config))}."]
        if config.get("duration"):
            seconds = config["duration"]
            lines.append(f"Target spoken length: about {seconds} seconds (roughly {round(seconds * WORDS_PER_SECOND)} words).")
        lines += self.extras(config)
        if brief:
            lines.append(f"Brief: {brief}")
        if source:
            lines.append(self.quoted("Source material", source))
        return "\n".join(lines)


class SummarizeNodeHandler(TextNodeHandler):
    node_type = "summarize"
    outputs = (OutputPort("summary", TEXT, keys=("summary", "text")),)
    max_tokens = 1024
    system_prompt = "You summarize content accurately. Never add facts that are not in the source."

    def build_prompt(self, context, config, inputs):
        source = self.text_input(inputs, "text")
        if not source:
            return None
        lines = [f"Summarize the text below in {language_name(self.language(context, config))}. "
                 "Keep every key point and drop repetition.", *self.extras(config), self.quoted("Text", source)]
        return "\n".join(lines)


class RewriteNodeHandler(TextNodeHandler):
    node_type = "rewrite"
    system_prompt = "You are an editor. Keep the meaning and facts of the source; change only how it is written."

    def build_prompt(self, context, config, inputs):
        source = self.text_input(inputs, "text")
        if not source:
            return None
        style = config.get("instructions") or "clearer, more natural and more engaging"
        lines = [f"Rewrite the text below in {language_name(self.language(context, config))}.",
                 f"Style: {style}."]
        lines += [line for line in self.extras(config) if not line.startswith("Additional instructions")]
        lines.append(self.quoted("Text", source))
        return "\n".join(lines)


class TranslateNodeHandler(TextNodeHandler):
    node_type = "translate"
    settings = {**COMMON_SETTINGS, "target_language": _language}
    max_tokens = 4096
    system_prompt = "You are a professional translator for video scripts and captions."

    def language(self, context, config) -> str:
        return config.get("target_language") or super().language(context, config)

    def build_prompt(self, context, config, inputs):
        source = self.text_input(inputs, "text")
        if not source:
            return None
        lines = [f"Translate the text below into {language_name(self.language(context, config))}. "
                 "Keep the meaning, tone, formatting and line breaks. Return only the translation.",
                 *self.extras(config), self.quoted("Text", source)]
        return "\n".join(lines)


class ListNodeHandler(TextNodeHandler):
    """Writes several short alternatives, one per line; the output also lists them."""

    settings = {**COMMON_SETTINGS, "count": _integer(1, 10)}
    inputs = (TOPIC_IN, SOURCE)
    requires = (("topic", "source"),)
    max_tokens = 1024
    default_count = 3
    ask = ""

    def build_prompt(self, context, config, inputs):
        topic = self.text_input(inputs, "topic")
        source = self.text_input(inputs, "source")
        if not topic and not source:
            return None
        count = config.get("count", self.default_count)
        lines = [self.ask.format(count=count, language=language_name(self.language(context, config))),
                 "Return exactly one per line, without numbering, bullets or quotes.", *self.extras(config)]
        if topic:
            lines.append(f"Topic: {topic}")
        if source:
            lines.append(self.quoted("Content", source))
        return "\n".join(lines)

    def output_from(self, payload, result):
        options = [line for line in (_LIST_MARKER.sub("", raw).strip().strip('"') for raw in result.text.splitlines())
                   if line]
        # The first alternative is the node's main output; "text" keeps all of them.
        return {self.outputs[0].name: options[0] if options else result.text, "text": result.text,
                "options": options, "provider": result.provider, "model": result.model,
                "usage": result.usage.as_dict(), "language": payload.get("language")}


def _list_outputs(key):
    return (OutputPort(key, TEXT, extract=_first_option(key)), OutputPort("text", TEXT))


class HookNodeHandler(ListNodeHandler):
    node_type = "hook"
    outputs = _list_outputs("hook")
    ask = ("Write {count} alternative opening hooks in {language} for the first seconds of a short video. "
           "Each is one sentence of at most 15 words that makes viewers keep watching.")


class TitleNodeHandler(ListNodeHandler):
    node_type = "title"
    outputs = _list_outputs("title")
    default_count = 5
    ask = "Write {count} alternative video titles in {language}, each at most 70 characters, specific and clickable."


class CTANodeHandler(ListNodeHandler):
    node_type = "cta"
    outputs = _list_outputs("cta")
    ask = ("Write {count} alternative calls to action in {language} for the end of a video, "
           "each one short sentence that asks viewers to do one clear thing.")


TEXT_HANDLERS = (AIWriterNodeHandler, SummarizeNodeHandler, RewriteNodeHandler, TranslateNodeHandler,
                 HookNodeHandler, TitleNodeHandler, CTANodeHandler)
