"""Text nodes: each builds a prompt from its config and upstream text, then queues one generation.

The text worker (``app/text_worker.py``) calls the provider outside any
database transaction and reports back through ``WorkflowExecutor.finish_step``;
``output_from`` turns the normalized ``TextResult`` into the step output.

Credits: ``TEXT_CREDITS_PER_GENERATION`` (default 1) is held when the step is
queued (``text-reserve:<step>``), charged on success as a usage event
(``text:<step>``), and refunded if generation fails (``text-refund:<step>``).
"""
import json
import re
from typing import Any, Mapping

from app import usage
from app.providers.text import TEXT_PROVIDERS, TEXT_TASK, TextResult, text_credit_cost, text_provider_config_issue
from app.workflow.config import INTEGER, NUMBER, SELECT, TEXT as TEXT_FIELD, TOOL, ConfigField, advanced
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, NodeHandler
from app.workflow.ports import BRIEF, PROJECT_TOPIC, PUBLISH_METADATA, TEXT, InputPort, OutputPort
from app.workflow.results import JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError

QUEUED_DETAIL = "Đã xếp hàng tạo nội dung."
MISSING_TOOL_DETAIL = "Chọn model văn bản được hỗ trợ trong Công cụ AI."
TOOL_UNAVAILABLE_DETAIL = "Model đã chọn cho bước này không còn được bật; chọn model khác."
MISSING_INPUT_DETAIL = "Chưa có nội dung đầu vào cho bước này."
MAX_SOURCE_CHARS = 60_000
WORDS_PER_SECOND = 2.5
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")

# Setting values are stable codes; these are the words the prompt uses for them.
LANGUAGES = {"vi": "Vietnamese", "en": "English", "ja": "Japanese"}
TONES = {"neutral": None, "casual": "casual and conversational", "professional": "professional",
         "cinematic": "cinematic", "storytelling": "storytelling", "documentary": "documentary",
         "dramatic": "dramatic", "funny": "funny and light-hearted"}
PLATFORMS = {"generic": None, "youtube": "YouTube (long-form)", "youtube_shorts": "YouTube Shorts (vertical, short)",
             "tiktok": "TikTok (vertical, short)", "facebook": "Facebook"}
SUMMARY_LENGTHS = {"short": "in 2 to 3 sentences", "medium": "in one paragraph of about 5 to 7 sentences",
                   "detailed": "in detail, with a short paragraph for each key point"}
REWRITE_LENGTHS = {"shorter": "Make it noticeably shorter, about 30% fewer words.",
                   "same": "Keep about the same length.",
                   "longer": "Expand it by about 30% without inventing facts."}
TITLE_STYLES = {"catchy": "catchy and curiosity-driven", "descriptive": "clear and descriptive",
                "question": "phrased as a question", "listicle": "list-style, with a number",
                "seo": "search-friendly, with the main keyword near the start"}

# Fields shared by the text nodes; "auto" languages follow the workspace's default language.
MODEL = ConfigField("tool_id", TOOL, label="model", task=TEXT_TASK, providers=tuple(TEXT_PROVIDERS),
                    code="unsupported_model")
LANGUAGE = ConfigField("language", SELECT, default="auto", options=("auto", *LANGUAGES), code="invalid_language")
TONE = ConfigField("tone", SELECT, default="neutral", options=tuple(TONES), code="invalid_tone")
PLATFORM = ConfigField("platform", SELECT, default="generic", options=tuple(PLATFORMS), code="invalid_platform")
INSTRUCTIONS = ConfigField("instructions", TEXT_FIELD, max_length=1000, multiline=True, code="invalid_instructions")
TEMPERATURE = ConfigField("temperature", NUMBER, minimum=0, maximum=2, advanced=True, code="invalid_temperature")


def max_tokens_field(default: int) -> ConfigField:
    return ConfigField("max_tokens", INTEGER, default=default, minimum=64, maximum=8192, advanced=True,
                       code="invalid_max_tokens")


def count_field(label: str, default: int) -> ConfigField:
    return ConfigField("count", INTEGER, default=default, minimum=1, maximum=10, label=label, code="invalid_count")


def language_name(code: str) -> str:
    return LANGUAGES.get(code, code)


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
    """Shared behavior; subclasses declare ports and settings and write the prompt in ``build_prompt``.

    The first output port names the key the generated text is stored under.
    ``build_prompt`` receives ``config`` with every setting filled in.
    """

    inputs = (TEXT_IN,)
    outputs = (OutputPort("text", TEXT),)
    requires = (("text",),)
    missing_input_detail = MISSING_INPUT_DETAIL
    config_fields = (LANGUAGE, INSTRUCTIONS, MODEL, advanced(TONE), advanced(PLATFORM), TEMPERATURE,
                     max_tokens_field(2048))
    system_prompt = ("You are a senior content writer for social video. Return only the requested content, "
                     "with no preamble, notes or markdown headings.")
    # "json" asks the provider for one JSON object (see app/providers/text/base.py).
    response_format = "text"

    # Inputs -----------------------------------------------------------------

    @staticmethod
    def text_input(inputs, port: str) -> str:
        """A text input's resolved value, trimmed and capped."""
        value = inputs.get(port)
        return value.strip()[:MAX_SOURCE_CHARS] if isinstance(value, str) else ""

    def language(self, context, config) -> str:
        chosen = config.get("language")
        if chosen and chosen != "auto":
            return chosen
        return context.workspace_settings.get("default_language") or "vi"

    def extras(self, config) -> list[str]:
        lines = []
        if tone := TONES.get(config.get("tone") or "neutral"):
            lines.append(f"Tone: {tone}.")
        if platform := PLATFORMS.get(config.get("platform") or "generic"):
            lines.append(f"Platform: {platform}.")
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
        config = self.config_values(inputs.config)
        tool = self._tool(context, config)
        if tool is None and config.get("tool_id"):
            return NodeExecutionResult.blocked(TOOL_UNAVAILABLE_DETAIL,
                                               NodeError("tool_unavailable", "The selected text model is not enabled"))
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
                   "temperature": config.get("temperature"), "max_tokens": config["max_tokens"],
                   "response_format": self.response_format, "language": language, "credits": cost}
        return NodeExecutionResult.queued(QUEUED_DETAIL, JobRequest("text", payload),
                                          {"provider": tool.provider, "model": tool.model},
                                          metadata={"credits_reserved": cost,
                                                    "credit_reference": f"text-reserve:{step.id}"})

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        cost = text_credit_cost()
        tool = self._tool(context, config)
        if tool is None and config.get("tool_id"):
            return NodeReadiness("tool_unavailable", TOOL_UNAVAILABLE_DETAIL, credits=cost, field="tool_id")
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
    # The brief comes from a connected idea, else the "prompt" setting, else the project topic.
    inputs = (InputPort("prompt", (BRIEF, TEXT), multiple=True, config_key="prompt", context=PROJECT_TOPIC), SOURCE)
    outputs = (OutputPort("script", TEXT, keys=("script", "text")),)
    requires = (("prompt", "source"),)
    config_fields = (
        LANGUAGE, TONE, PLATFORM,
        ConfigField("duration", INTEGER, minimum=5, maximum=3600, presets=(30, 60, 180, 300, 600),
                    label="target_duration", code="invalid_duration"),
        ConfigField("prompt", TEXT_FIELD, max_length=3000, multiline=True, label="brief", code="invalid_prompt"),
        INSTRUCTIONS, MODEL, TEMPERATURE, max_tokens_field(2048),
    )

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
    config_fields = (
        ConfigField("length", SELECT, default="medium", options=tuple(SUMMARY_LENGTHS), label="summary_length",
                    code="invalid_length"),
        LANGUAGE, INSTRUCTIONS, MODEL, advanced(TONE), advanced(PLATFORM), TEMPERATURE, max_tokens_field(1024),
    )
    system_prompt = "You summarize content accurately. Never add facts that are not in the source."

    def build_prompt(self, context, config, inputs):
        source = self.text_input(inputs, "text")
        if not source:
            return None
        lines = [f"Summarize the text below in {language_name(self.language(context, config))}, "
                 f"{SUMMARY_LENGTHS[config['length']]}. Keep every key point and drop repetition.",
                 *self.extras(config), self.quoted("Text", source)]
        return "\n".join(lines)


class RewriteNodeHandler(TextNodeHandler):
    node_type = "rewrite"
    config_fields = (
        TONE, PLATFORM,
        ConfigField("length", SELECT, default="same", options=tuple(REWRITE_LENGTHS), label="target_length",
                    code="invalid_length"),
        INSTRUCTIONS, MODEL, advanced(LANGUAGE), TEMPERATURE, max_tokens_field(2048),
    )
    system_prompt = "You are an editor. Keep the meaning and facts of the source; change only how it is written."

    def build_prompt(self, context, config, inputs):
        source = self.text_input(inputs, "text")
        if not source:
            return None
        style = config.get("instructions") or "clearer, more natural and more engaging"
        lines = [f"Rewrite the text below in {language_name(self.language(context, config))}.",
                 f"Style: {style}.", REWRITE_LENGTHS[config["length"]]]
        lines += [line for line in self.extras(config) if not line.startswith("Additional instructions")]
        lines.append(self.quoted("Text", source))
        return "\n".join(lines)


class TranslateNodeHandler(TextNodeHandler):
    node_type = "translate"
    config_fields = (
        ConfigField("target_language", SELECT, default="auto", options=("auto", *LANGUAGES), code="invalid_language"),
        MODEL, advanced(INSTRUCTIONS), TEMPERATURE, max_tokens_field(4096),
    )
    system_prompt = "You are a professional translator for video scripts and captions."

    def language(self, context, config) -> str:
        return super().language(context, {"language": config.get("target_language")})

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

    inputs = (TOPIC_IN, SOURCE)
    requires = (("topic", "source"),)
    ask = ""

    def build_prompt(self, context, config, inputs):
        topic = self.text_input(inputs, "topic")
        source = self.text_input(inputs, "source")
        if not topic and not source:
            return None
        lines = [self.ask.format(count=config["count"], language=language_name(self.language(context, config))),
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
    config_fields = (count_field("hook_count", 3), TONE, PLATFORM, MODEL, advanced(LANGUAGE), advanced(INSTRUCTIONS),
                     TEMPERATURE, max_tokens_field(1024))
    ask = ("Write {count} alternative opening hooks in {language} for the first seconds of a short video. "
           "Each is one sentence of at most 15 words that makes viewers keep watching.")


class TitleNodeHandler(ListNodeHandler):
    node_type = "title"
    outputs = _list_outputs("title")
    config_fields = (count_field("title_count", 5), PLATFORM,
                     ConfigField("style", SELECT, default="catchy", options=tuple(TITLE_STYLES), label="title_style",
                                 code="invalid_style"),
                     MODEL, advanced(LANGUAGE), advanced(TONE), advanced(INSTRUCTIONS), TEMPERATURE,
                     max_tokens_field(1024))
    ask = "Write {count} alternative video titles in {language}, each at most 70 characters, specific and clickable."

    def extras(self, config):
        return [f"Title style: {TITLE_STYLES[config['style']]}.", *super().extras(config)]


class CTANodeHandler(ListNodeHandler):
    node_type = "cta"
    outputs = _list_outputs("cta")
    config_fields = (count_field("option_count", 3), TONE, PLATFORM, MODEL, advanced(LANGUAGE), advanced(INSTRUCTIONS),
                     TEMPERATURE, max_tokens_field(1024))
    ask = ("Write {count} alternative calls to action in {language} for the end of a video, "
           "each one short sentence that asks viewers to do one clear thing.")


def parse_metadata(text: str) -> dict[str, Any]:
    """Title, description and tags from a JSON reply (code fences allowed), fitted to YouTube's limits.

    A reply that is not the expected JSON still gives usable values: its first
    line becomes the title and the rest the description, with no tags.
    """
    from app.publications import fit_metadata

    body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    try:
        value = json.loads(body)
    except ValueError:
        value = None
    if isinstance(value, dict) and isinstance(value.get("title"), str):
        return fit_metadata(value.get("title"), value.get("description"), value.get("tags"))
    lines = (text or "").strip().splitlines()
    return fit_metadata(lines[0] if lines else "", "\n".join(lines[1:]), [])


def parse_platforms(text: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Per-channel metadata (YouTube, TikTok, Facebook) from the same JSON reply (Phase 12)."""
    from app.publications import platform_metadata

    body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    try:
        value = json.loads(body)
    except ValueError:
        value = None
    value = value if isinstance(value, dict) else {}
    return platform_metadata({**metadata, "privacy_status": "private"}, tiktok_caption=value.get("tiktok_caption"),
                             facebook_description=value.get("facebook_description"))


def _publish_metadata(output):
    """The Metadata port: YouTube's values, with every channel's under ``platforms`` when present."""
    metadata = output.get("metadata")
    if not isinstance(metadata, dict) or not metadata:
        return None
    platforms = output.get("platforms")
    return {**metadata, "platforms": platforms} if isinstance(platforms, dict) else metadata


class MetadataNodeHandler(TextNodeHandler):
    """Title, description and tags for the finished video, per channel, in one JSON generation (Phase 9, 12).

    ``metadata`` keeps YouTube's values; ``platforms`` adds a short TikTok caption
    and a Facebook description derived from the same reply.
    """

    node_type = "metadata"
    inputs = (TOPIC_IN, SOURCE)
    outputs = (OutputPort("metadata", PUBLISH_METADATA, extract=_publish_metadata),
               OutputPort("title", TEXT), OutputPort("description", TEXT))
    requires = (("topic", "source"),)
    response_format = "json"
    config_fields = (
        LANGUAGE,
        ConfigField("platform", SELECT, default="youtube", options=tuple(PLATFORMS), code="invalid_platform"),
        ConfigField("tag_count", INTEGER, default=8, minimum=0, maximum=20, code="invalid_count"),
        ConfigField("cta", SELECT, default="include", options=("include", "omit"), label="metadata_cta",
                    code="invalid_cta"),
        MODEL, advanced(TONE), advanced(INSTRUCTIONS), TEMPERATURE, max_tokens_field(1024),
    )
    system_prompt = "You write social video publishing metadata. Reply with one JSON object and nothing else."

    def build_prompt(self, context, config, inputs):
        topic = self.text_input(inputs, "topic")
        source = self.text_input(inputs, "source")
        if not topic and not source:
            return None
        ending = " End it with one short call to action." if config["cta"] == "include" else ""
        lines = [f"Write YouTube publishing metadata in {language_name(self.language(context, config))} "
                 "for the video described below.",
                 'Return exactly: {"title": string, "description": string, "tags": [string], '
                 '"tiktok_caption": string, "facebook_description": string}.',
                 "title: one line of at most 90 characters, specific and clickable, without < or >.",
                 f"description: 2 to 4 short paragraphs, at most 1500 characters, without < or >.{ending}",
                 f"tags: {config['tag_count']} short search keywords or phrases, without # or commas.",
                 "tiktok_caption: one or two short lines for TikTok, at most 150 characters, without hashtags.",
                 "facebook_description: one short paragraph for a Facebook Reel, at most 500 characters.",
                 *self.extras(config)]
        if topic:
            lines.append(f"Topic: {topic}")
        if source:
            lines.append(self.quoted("Video script", source))
        return "\n".join(lines)

    def output_from(self, payload, result):
        metadata = parse_metadata(result.text)
        return {"metadata": metadata, "platforms": parse_platforms(result.text, metadata),
                "title": metadata["title"], "description": metadata["description"],
                "tags": metadata["tags"], "text": result.text, "provider": result.provider, "model": result.model,
                "usage": result.usage.as_dict(), "language": payload.get("language")}


TEXT_HANDLERS = (AIWriterNodeHandler, SummarizeNodeHandler, RewriteNodeHandler, TranslateNodeHandler,
                 HookNodeHandler, TitleNodeHandler, CTANodeHandler, MetadataNodeHandler)
