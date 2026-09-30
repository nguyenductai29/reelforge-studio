"""Video: reserves credits and queues one text-to-video job for the video worker.

The node's settings choose the model, aspect ratio, clip length and an
optional prompt override. "auto" keeps the earlier behavior: the aspect ratio
follows the workspace's video orientation and the length is the provider's
default. The prompt is, in order: the node's prompt override, a prompt sent
when starting the run (older API clients), connected text, connected scenes,
then the project topic.
"""
from app import usage
from app.providers.catalog import (ORIENTATION_ASPECT, PROVIDER_ERRORS, VIDEO_PROVIDERS, video_credit_cost,
                                   video_duration_supported, video_provider_config_issue, video_request_defaults)
from app.workflow.config import SELECT, TEXT as TEXT_FIELD, TOOL, ConfigField
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, INVALID_CONFIG_DETAIL, NodeHandler
from app.workflow.nodes.pending import pending_ai_task
from app.workflow.ports import BRIEF, SCENES, TEXT, VIDEO_ASSETS, InputPort, OutputPort
from app.workflow.results import (JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError,
                                  produced_asset_ids)


MAX_CONNECTED_PROMPT_CHARS = 1000
MISSING_TOOL_DETAIL = "Chưa chọn model video được hỗ trợ cho bước video."
TOOL_UNAVAILABLE_DETAIL = "Model video đã chọn cho bước này không còn được bật; chọn model khác."
# Lengths every non-experimental model accepts; longer clips would cost the same credits.
DURATIONS = ("4s", "6s", "8s")


def clip_prompt(text) -> str:
    """Connected text as a clip prompt, cut at a word boundary to fit every provider (Runway allows 1000)."""
    if not isinstance(text, str) or not text.strip():
        return ""
    text = text.strip()
    if len(text) <= MAX_CONNECTED_PROMPT_CHARS:
        return text
    return " ".join(text.split())[:MAX_CONNECTED_PROMPT_CHARS].rsplit(" ", 1)[0]


def scenes_prompt(scenes) -> str:
    """One clip covers every scene until multi-clip rendering exists."""
    if not isinstance(scenes, list):
        return ""
    shots = [scene.get("visual_prompt") or scene.get("text") for scene in scenes if isinstance(scene, dict)]
    shots = [shot for shot in shots if isinstance(shot, str) and shot.strip()]
    if len(shots) == 1:
        return clip_prompt(shots[0])
    return clip_prompt(" ".join(f"Shot {index}: {shot.strip()}" for index, shot in enumerate(shots, 1)))


def _video_assets(output):
    assets = output.get("video_assets")
    if isinstance(assets, list) and assets:
        return assets
    return [{"id": asset_id, "filename": output.get("filename"), "content_type": "video/mp4"}
            for asset_id in produced_asset_ids(output)] or None


class VideoNodeHandler(NodeHandler):
    node_type = "video"
    inputs = (InputPort("prompt", (TEXT, BRIEF), multiple=True), InputPort("scenes", (SCENES,), multiple=True))
    outputs = (OutputPort("video_assets", VIDEO_ASSETS, extract=_video_assets),)
    config_fields = (
        ConfigField("tool_id", TOOL, label="model", task="video", providers=tuple(VIDEO_PROVIDERS),
                    code="unsupported_model"),
        ConfigField("aspect_ratio", SELECT, default="auto", options=("auto", *ORIENTATION_ASPECT.values()),
                    code="invalid_aspect_ratio"),
        ConfigField("duration", SELECT, default="auto", options=("auto", *DURATIONS), label="clip_duration",
                    code="invalid_duration"),
        ConfigField("prompt", TEXT_FIELD, max_length=MAX_CONNECTED_PROMPT_CHARS, multiline=True,
                    label="prompt_override", code="invalid_prompt"),
    )

    def _aspect_ratio(self, context, config):
        if config["aspect_ratio"] != "auto":
            return config["aspect_ratio"]
        return ORIENTATION_ASPECT.get(context.workspace_settings.get("video_orientation", "vertical"))

    @staticmethod
    def _duration(provider_name, config):
        return video_request_defaults(provider_name)[0] if config["duration"] == "auto" else config["duration"]

    def _tool(self, context, tool_id):
        return context.find_tool("video", VIDEO_PROVIDERS, tool_id)

    def execute(self, context, node, inputs):
        # One credit reservation and one job per run: see reserve:<run_id> below.
        if context.count_nodes(self.node_type) != 1:
            return pending_ai_task(context, self.node_type)
        options = context.options
        if options.frozen_video:
            # A retry repeats the original request and price, even if tools or settings changed since.
            frozen = options.frozen_video
            provider_name = frozen.get("provider")
            model_id = frozen.get("model_id")
            tool_reference = frozen.get("tool_id")
            prompt = frozen.get("prompt", "").strip()
            aspect_ratio = frozen.get("aspect_ratio")
            defaults = video_request_defaults(provider_name)
            duration = frozen.get("duration", defaults[0])
            resolution = frozen.get("resolution", defaults[1])
            generate_audio = frozen.get("generate_audio", defaults[2])
            cost = frozen.get("credits")
        else:
            config = self.config_values(inputs.config)
            if config["tool_id"]:
                tool = self._tool(context, config["tool_id"])
                if not tool:
                    return NodeExecutionResult.blocked(TOOL_UNAVAILABLE_DETAIL, NodeError(
                        "tool_unavailable", "The selected video model is not enabled"))
            else:
                tool = self._tool(context, options.tool_id or None)
                if options.tool_id and not tool:
                    raise RunRequestError(400, "Selected video tool is unavailable", code="tool_unavailable",
                                          step_detail=MISSING_TOOL_DETAIL)
            provider_name = tool.provider if tool else None
            model_id = tool.model if tool else None
            tool_reference = tool.id if tool else None
            project = context.project
            if config["prompt"] and config["prompt"].strip():
                prompt = config["prompt"].strip()
            elif options.prompt_override is not None:
                prompt = options.prompt_override.strip()
            else:
                prompt = (clip_prompt(inputs.get("prompt")) or scenes_prompt(inputs.get("scenes"))
                          or (project.topic or project.title or "").strip())
            aspect_ratio = self._aspect_ratio(context, config)
            _, resolution, generate_audio = video_request_defaults(provider_name)
            duration = self._duration(provider_name, config)
            # An unknown model is reported by the provider's own validation below.
            if (provider_name in VIDEO_PROVIDERS and model_id in VIDEO_PROVIDERS[provider_name].module.VIDEO_MODELS
                    and not video_duration_supported(provider_name, model_id, duration)):
                return NodeExecutionResult.blocked(INVALID_CONFIG_DETAIL, NodeError(
                    "invalid_duration", f"{model_id} does not support {duration} clips"),
                    output={"invalid_setting": "duration"})
            cost = video_credit_cost()
        if provider_name not in VIDEO_PROVIDERS:
            return NodeExecutionResult.blocked(MISSING_TOOL_DETAIL)
        if issue := video_provider_config_issue(provider_name):
            return NodeExecutionResult.blocked(issue[1])
        if not prompt:
            return NodeExecutionResult.blocked("Dự án cần có chủ đề hoặc prompt để tạo video.")
        if not aspect_ratio:
            return NodeExecutionResult.blocked("Model video hiện chưa hỗ trợ tỷ lệ vuông.")
        provider_module = VIDEO_PROVIDERS[provider_name].module
        request = provider_module.VideoRequest(model_id=model_id, prompt=prompt, aspect_ratio=aspect_ratio,
                                               duration=duration, resolution=resolution,
                                               generate_audio=generate_audio)
        try:
            provider_module.validate_video_request(request)
        except PROVIDER_ERRORS as exc:
            return NodeExecutionResult.blocked(str(exc))
        if not isinstance(cost, int) or not 1 <= cost <= 100000:
            raise RunRequestError(400, "Invalid video credit quote", code="invalid_quote")
        try:
            usage.post_credit(context.db, context.workspace.id, -cost, "video_reserve", f"reserve:{context.run.id}")
        except ValueError as exc:
            raise RunRequestError(402, "Not enough credits for this video", code="insufficient_credits",
                                  step_detail=INSUFFICIENT_CREDITS_DETAIL) from exc
        payload = {"kind": "video.generate", "provider": provider_name, "tool_id": tool_reference,
                   "prompt": prompt, "model_id": model_id, "aspect_ratio": aspect_ratio,
                   "duration": request.duration, "resolution": request.resolution,
                   "generate_audio": request.generate_audio, "credits": cost}
        return NodeExecutionResult.queued("Đã xếp hàng tạo video.", JobRequest("video", payload),
                                          {"prompt": prompt, "provider": provider_name, "model": model_id,
                                           "aspect_ratio": aspect_ratio, "duration": request.duration},
                                          metadata={"credits_reserved": cost})

    def readiness(self, context, node):
        quote = video_credit_cost()
        config = self.config_values(node.get("config"))
        tool = self._tool(context, config["tool_id"] or context.options.tool_id)
        if context.count_nodes(self.node_type) != 1:
            return NodeReadiness("unsupported_graph", "Hiện chỉ hỗ trợ một bước video trong mỗi workflow.",
                                 credits=quote)
        if not tool and config["tool_id"]:
            return NodeReadiness("tool_unavailable", TOOL_UNAVAILABLE_DETAIL, credits=quote, field="tool_id")
        if not tool:
            return NodeReadiness("missing_tool", "Chọn model video được hỗ trợ trong Công cụ AI.", credits=quote)
        if issue := video_provider_config_issue(tool.provider):
            return NodeReadiness(*issue, credits=quote)
        if not self._aspect_ratio(context, config):
            return NodeReadiness("unsupported_aspect", "Model video chưa hỗ trợ tỷ lệ vuông.", credits=quote)
        if tool.model not in VIDEO_PROVIDERS[tool.provider].module.VIDEO_MODELS:
            return NodeReadiness("unsupported_model", "Model video này chưa được hỗ trợ.", credits=quote,
                                 field="tool_id")
        if not video_duration_supported(tool.provider, tool.model, self._duration(tool.provider, config)):
            return NodeReadiness("invalid_settings", INVALID_CONFIG_DETAIL, credits=quote, code="invalid_duration",
                                 field="duration")
        if context.credit_balance < quote:
            return NodeReadiness("insufficient_credits", f"Cần {quote} credits; hiện có {context.credit_balance}.",
                                 credits=quote)
        return NodeReadiness("ready", f"Sẵn sàng tạo clip; dự kiến giữ {quote} credits.", credits=quote)
