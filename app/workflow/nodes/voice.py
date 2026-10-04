"""Voice: text-to-speech narration, one durable job per narration file, for the voice worker.

Modes (explicit precedence):

1. the node's Text override → one narration ("single");
2. connected script or text → one narration of it;
3. connected scenes → one narration per scene, from each scene's ``text``
   (never its visual prompt); scenes are never joined, and ``scene_index`` is kept.

The project topic is never read aloud: it is a title, not narration, so a Voice
step without text or scenes is blocked. Text is never cut: text longer than the
model accepts blocks the step before any credit is held. Each narration has its
own job and credit reservation (app/workflow/nodes/media.py); the step completes
only when every narration is stored (app/media_jobs.py).
"""
from app.providers.voice import (ALL_VOICES, STYLES, VOICE_PROVIDERS, VOICE_TASK, VoiceProviderError, VoiceRequest,
                                 validate_voice_request, voice_credit_cost, voice_model, voice_provider_config_issue)
from app.providers.voice.gemini import DEFAULT_VOICE
from app.workflow.config import SELECT, TEXT as TEXT_FIELD, TOOL, ConfigField
from app.workflow.nodes.base import INVALID_CONFIG_DETAIL, NodeHandler
from app.workflow.nodes.media import (MAX_OPERATIONS, Operation, connected_inputs, normalize, queue_operations,
                                      scene_mode_expected, scene_operations)
from app.workflow.ports import AUDIO_ASSETS, BRIEF, SCENES, TEXT, InputPort, OutputPort
from app.workflow.results import NodeError, NodeExecutionResult, NodeReadiness

MAX_TEXT_CHARS = 5000
QUEUED_DETAIL = "Đã xếp hàng tạo giọng đọc."
MISSING_TOOL_DETAIL = "Chọn model giọng đọc được hỗ trợ trong Model AI."
TOOL_UNAVAILABLE_DETAIL = "Model giọng đọc đã chọn cho bước này không còn được bật; chọn model khác."
UNSUPPORTED_MODEL_DETAIL = "Model giọng đọc này chưa được hỗ trợ."
MISSING_INPUT_DETAIL = "Chưa có kịch bản, văn bản hoặc cảnh để đọc."
TOO_MANY_DETAIL = "Quá nhiều đoạn đọc cho một bước (tối đa 20)."
TOO_LONG_DETAIL = "Văn bản quá dài cho một lần đọc (tối đa 5000 ký tự); hãy nối Scene Splitter để đọc từng cảnh."
AUDIO_FORMAT = "wav"


def _audio_assets(output):
    assets = output.get("audio_assets")
    return assets if isinstance(assets, list) and assets else None


class VoiceNodeHandler(NodeHandler):
    node_type = "voice"
    # "script" keeps the port name of the earlier placeholder, so saved edges still connect.
    inputs = (InputPort("script", (TEXT, BRIEF), multiple=True, config_key="text"),
              InputPort("scenes", (SCENES,), multiple=True))
    outputs = (OutputPort("audio_assets", AUDIO_ASSETS, extract=_audio_assets),)
    requires = (("script", "scenes"),)
    missing_input_detail = MISSING_INPUT_DETAIL
    config_fields = (
        ConfigField("tool_id", TOOL, label="model", task=VOICE_TASK, providers=tuple(VOICE_PROVIDERS),
                    code="unsupported_model"),
        ConfigField("voice", SELECT, default=DEFAULT_VOICE, options=ALL_VOICES, code="invalid_voice"),
        ConfigField("style", SELECT, default="neutral", options=STYLES, label="voice_style", code="invalid_style"),
        ConfigField("text", TEXT_FIELD, max_length=MAX_TEXT_CHARS, multiline=True, label="text_override",
                    code="invalid_text"),
    )

    def _tool(self, context, config):
        return context.find_tool(VOICE_TASK, VOICE_PROVIDERS, config["tool_id"] or None)

    def _problem(self, config, tool):
        """A readiness/execution problem as (status, detail, code, field), or ``None``."""
        if tool is None and config["tool_id"]:
            return "tool_unavailable", TOOL_UNAVAILABLE_DETAIL, "tool_unavailable", "tool_id"
        if tool is None:
            return "missing_tool", MISSING_TOOL_DETAIL, None, None
        if issue := voice_provider_config_issue(tool.provider):
            return issue[0], issue[1], issue[0], None
        model = voice_model(tool.provider, tool.model)
        if model is None:
            return "unsupported_model", UNSUPPORTED_MODEL_DETAIL, "unsupported_model", "tool_id"
        if config["voice"] not in model.voices:
            return "invalid_settings", INVALID_CONFIG_DETAIL, "invalid_voice", "voice"
        if config["style"] not in model.styles:
            return "invalid_settings", INVALID_CONFIG_DETAIL, "invalid_style", "style"
        return None

    @staticmethod
    def operations(config, inputs) -> tuple[str, list[Operation]]:
        """The mode and one operation per narration, following the precedence in the module docstring."""
        text = normalize(config["text"]) or normalize(inputs.get("script"))
        if text:
            return "prompt", [Operation("single", text)]
        scenes = scene_operations(inputs.get("scenes"), keys=("text",), shorten=normalize)
        return ("scenes", scenes) if scenes else ("prompt", [])

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        tool = self._tool(context, config)
        if problem := self._problem(config, tool):
            status, detail, code, _ = problem
            return NodeExecutionResult.blocked(detail, NodeError(code or status, detail))
        mode, operations = self.operations(config, inputs)
        if not operations:
            return NodeExecutionResult.blocked(MISSING_INPUT_DETAIL, NodeError("missing_input", "Nothing to read"))
        if len(operations) > MAX_OPERATIONS:
            return NodeExecutionResult.blocked(TOO_MANY_DETAIL, NodeError("invalid_request", "Too many narrations"))
        model = voice_model(tool.provider, tool.model)
        try:
            # Every narration is checked before any credit is reserved.
            for operation in operations:
                validate_voice_request(model, VoiceRequest(tool.model, operation.prompt, config["voice"],
                                                           config["style"], AUDIO_FORMAT))
        except VoiceProviderError as exc:
            too_long = any(len(operation.prompt) > model.max_text_chars for operation in operations)
            return NodeExecutionResult.blocked(TOO_LONG_DETAIL if too_long else INVALID_CONFIG_DETAIL,
                                               NodeError(exc.code, str(exc)))
        return queue_operations(context, node, kind="voice", mode=mode, operations=operations,
                                cost=voice_credit_cost(), detail=QUEUED_DETAIL,
                                payload={"node_type": self.node_type, "provider": tool.provider, "model": tool.model,
                                         "tool_id": tool.id, "voice": config["voice"], "style": config["style"],
                                         "format": AUDIO_FORMAT})

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        cost = voice_credit_cost()
        tool = self._tool(context, config)
        # Connected text comes before scenes (see the module docstring).
        per_scene = (not normalize(config["text"]) and scene_mode_expected(context, node)
                     and "script" not in connected_inputs(context, node))
        if problem := self._problem(config, tool):
            status, detail, code, field = problem
            return NodeReadiness(status, detail, credits=cost, code=code, field=field)
        if context.credit_balance < cost:
            return NodeReadiness("insufficient_credits", f"Cần {cost} credits; hiện có {context.credit_balance}.",
                                 credits=cost)
        if per_scene:
            return NodeReadiness("ready", f"Sẵn sàng; giữ {cost} credits cho mỗi cảnh khi bước bắt đầu.",
                                 credits=cost, code="per_scene")
        return NodeReadiness("ready", f"Sẵn sàng tạo giọng đọc; dự kiến giữ {cost} credits.", credits=cost)
