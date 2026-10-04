"""Video: reserves credits and queues text-to-video jobs for the video worker.

The node's settings choose the model, aspect ratio, clip length and an
optional prompt override. "auto" keeps the earlier behavior: the aspect ratio
follows the workspace's video orientation and the length is the provider's
default.

Modes (explicit precedence):

1. the node's Prompt override, or a prompt sent when starting the run (older API
   clients) → one clip ("single");
2. connected scenes → one clip per scene ("scenes"), from each scene's
   ``visual_prompt`` (else its text); scene prompts are never joined;
3. connected text, then the project topic → one clip ("single").

Every clip has its own job and credit reservation: ``video-reserve:<step>:single``
or ``video-reserve:<step>:scene:<n>`` (app/workflow/nodes/media.py). Runs queued
before multi-scene video used one ``reserve:<run>`` reservation per run; the worker
and reconciliation still read those. A single clip keeps the step-level worker
path (app/video_worker.py); scene clips use the shared child-job engine
(app/media_jobs.py), and the step completes only when every clip is stored.
"""
from app import usage
from app.providers.catalog import (ORIENTATION_ASPECT, PROVIDER_ERRORS, VIDEO_PROVIDERS, video_credit_cost,
                                   video_duration_supported, video_provider_config_issue, video_request_defaults)
from app.workflow.config import SELECT, TEXT as TEXT_FIELD, TOOL, ConfigField
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, INVALID_CONFIG_DETAIL, NodeHandler
from app.workflow.nodes.media import (MAX_OPERATIONS, Operation, queue_operations, references, scene_mode_expected,
                                      scene_operations)
from app.workflow.ports import BRIEF, SCENES, TEXT, VIDEO_ASSETS, InputPort, OutputPort
from app.workflow.results import (JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError,
                                  produced_asset_ids)


MAX_CONNECTED_PROMPT_CHARS = 1000
MISSING_TOOL_DETAIL = "Chưa chọn model video được hỗ trợ cho bước video."
TOOL_UNAVAILABLE_DETAIL = "Model video đã chọn cho bước này không còn được bật; chọn model khác."
SCENES_QUEUED_DETAIL = "Đã xếp hàng tạo video cho từng cảnh."
TOO_MANY_DETAIL = "Quá nhiều cảnh cho một bước video (tối đa 20)."
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

    @staticmethod
    def _frozen(frozen, node):
        """The retried run's single-clip request for this node; scene clips are generated again.

        Payloads from before multi-scene video name no node; their workflows had one video node.
        """
        if not frozen or frozen.get("mode") not in (None, "single"):
            return None
        return frozen if frozen.get("node_id") in (None, node["id"]) else None

    @staticmethod
    def operations(context, config, inputs) -> tuple[str, list[Operation]]:
        """The mode and one operation per clip, following the precedence in the module docstring."""
        if config["prompt"] and config["prompt"].strip():
            prompt = config["prompt"].strip()
        elif context.options.prompt_override is not None:
            prompt = context.options.prompt_override.strip()
        else:
            scenes = scene_operations(inputs.get("scenes"))
            if scenes:
                return "scenes", scenes
            project = context.project
            prompt = clip_prompt(inputs.get("prompt")) or (project.topic or project.title or "").strip()
        return "single", [Operation("single", prompt)] if prompt else []

    def execute(self, context, node, inputs):
        options = context.options
        if frozen := self._frozen(options.frozen_video, node):
            # A retry repeats the original request and price, even if tools or settings changed since.
            provider_name = frozen.get("provider")
            model_id = frozen.get("model_id")
            tool_reference = frozen.get("tool_id")
            prompt = frozen.get("prompt", "").strip()
            mode, operations = "single", [Operation("single", prompt)] if prompt else []
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
            mode, operations = self.operations(context, config, inputs)
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
        if not operations:
            return NodeExecutionResult.blocked("Dự án cần có chủ đề hoặc prompt để tạo video.")
        if len(operations) > MAX_OPERATIONS:
            return NodeExecutionResult.blocked(TOO_MANY_DETAIL, NodeError("invalid_request", "Too many scenes"))
        if not aspect_ratio:
            return NodeExecutionResult.blocked("Model video hiện chưa hỗ trợ tỷ lệ vuông.")
        provider_module = VIDEO_PROVIDERS[provider_name].module
        try:
            # Every clip is checked before any credit is reserved.
            requests = [provider_module.VideoRequest(model_id=model_id, prompt=operation.prompt,
                                                     aspect_ratio=aspect_ratio, duration=duration,
                                                     resolution=resolution, generate_audio=generate_audio)
                        for operation in operations]
            for request in requests:
                provider_module.validate_video_request(request)
        except PROVIDER_ERRORS as exc:
            return NodeExecutionResult.blocked(str(exc))
        if not isinstance(cost, int) or not 1 <= cost <= 100000:
            raise RunRequestError(400, "Invalid video credit quote", code="invalid_quote")
        request = requests[0]
        settings = {"provider": provider_name, "tool_id": tool_reference, "model_id": model_id,
                    "aspect_ratio": aspect_ratio, "duration": request.duration, "resolution": request.resolution,
                    "generate_audio": request.generate_audio}
        if mode == "scenes":
            return queue_operations(context, node, kind="video", mode=mode, operations=operations, cost=cost,
                                    payload={"node_type": self.node_type, **settings}, detail=SCENES_QUEUED_DETAIL,
                                    output={"aspect_ratio": aspect_ratio, "duration": request.duration})
        step = context.step_for(node)
        refs = references("video", step.id, "single")
        try:
            usage.post_credit(context.db, context.workspace.id, -cost, "video_reserve", refs["reserve_reference"])
        except ValueError as exc:
            raise RunRequestError(402, "Not enough credits for this video", code="insufficient_credits",
                                  step_detail=INSUFFICIENT_CREDITS_DETAIL) from exc
        prompt = operations[0].prompt
        payload = {"kind": "video.generate", **settings, "prompt": prompt, "credits": cost, "node_id": node["id"],
                   "mode": "single", "operation": "single", "scene_index": None, **refs}
        return NodeExecutionResult.queued("Đã xếp hàng tạo video.",
                                          JobRequest("video", payload, logical_key=f"video:{step.id}:single"),
                                          {"prompt": prompt, "provider": provider_name, "model": model_id,
                                           "aspect_ratio": aspect_ratio, "duration": request.duration},
                                          metadata={"credits_reserved": cost,
                                                    "credit_reference": refs["reserve_reference"]})

    def readiness(self, context, node):
        quote = video_credit_cost()
        config = self.config_values(node.get("config"))
        tool = self._tool(context, config["tool_id"] or context.options.tool_id)
        # With scenes connected and no override, each scene is its own clip; the count is known only at run time.
        per_scene = not (config["prompt"] or "").strip() and scene_mode_expected(context, node)
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
        if per_scene:
            return NodeReadiness("ready", f"Sẵn sàng; giữ {quote} credits cho mỗi cảnh khi bước bắt đầu.",
                                 credits=quote, code="per_scene")
        return NodeReadiness("ready", f"Sẵn sàng tạo clip; dự kiến giữ {quote} credits.", credits=quote)
