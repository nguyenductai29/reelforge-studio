"""Image: queues paid image generations, one durable job per image, for the image worker.

Modes (explicit precedence):

1. the node's Prompt override → ``count`` images of that prompt;
2. connected prompt text → ``count`` images of it;
3. connected scenes → one image per scene, from its ``visual_prompt`` (else its text);
4. otherwise the project topic → ``count`` images.

Scene prompts are never joined into one request. Each image has its own job
and credit reservation (app/workflow/nodes/media.py); the step completes only
when every image is stored (app/media_jobs.py).
"""
from app.providers.image import (ASPECT_RATIOS, IMAGE_PROVIDERS, IMAGE_TASK, QUALITIES, ImageProviderError,
                                 ImageRequest, image_credit_cost, image_model, image_provider_config_issue,
                                 validate_image_request)
from app.workflow.config import INTEGER, SELECT, TEXT as TEXT_FIELD, TOOL, ConfigField
from app.workflow.nodes.base import INVALID_CONFIG_DETAIL, NodeHandler
from app.workflow.nodes.media import (MAX_OPERATIONS, Operation, clip, connected_inputs, queue_operations,
                                      scene_mode_expected, scene_operations)
from app.workflow.ports import BRIEF, IMAGE_ASSETS, SCENES, TEXT, InputPort, OutputPort
from app.workflow.results import NodeError, NodeExecutionResult, NodeReadiness

QUEUED_DETAIL = "Đã xếp hàng tạo ảnh."
MISSING_TOOL_DETAIL = "Chọn model ảnh được hỗ trợ trong Model AI."
TOOL_UNAVAILABLE_DETAIL = "Model ảnh đã chọn cho bước này không còn được bật; chọn model khác."
UNSUPPORTED_MODEL_DETAIL = "Model ảnh này chưa được hỗ trợ."
MISSING_INPUT_DETAIL = "Chưa có prompt, cảnh hoặc chủ đề dự án để tạo ảnh."
TOO_MANY_DETAIL = "Quá nhiều ảnh cho một bước (tối đa 20)."
# Aspect ratio for "auto", from the workspace's video orientation setting.
WORKSPACE_ASPECT = {"vertical": "9:16", "horizontal": "16:9", "square": "1:1"}


def _image_assets(output):
    assets = output.get("image_assets")
    return assets if isinstance(assets, list) and assets else None


class ImageNodeHandler(NodeHandler):
    node_type = "image"
    inputs = (InputPort("prompt", (TEXT, BRIEF), multiple=True), InputPort("scenes", (SCENES,), multiple=True))
    outputs = (OutputPort("image_assets", IMAGE_ASSETS, extract=_image_assets),)
    config_fields = (
        ConfigField("tool_id", TOOL, label="model", task=IMAGE_TASK, providers=tuple(IMAGE_PROVIDERS),
                    code="unsupported_model"),
        ConfigField("aspect_ratio", SELECT, default="auto", options=("auto", *ASPECT_RATIOS),
                    code="invalid_aspect_ratio"),
        ConfigField("count", INTEGER, default=1, minimum=1, maximum=4, label="image_count", code="invalid_count"),
        ConfigField("quality", SELECT, default="standard", options=QUALITIES, code="invalid_quality"),
        ConfigField("prompt", TEXT_FIELD, max_length=1000, multiline=True, label="prompt_override",
                    code="invalid_prompt"),
        ConfigField("seed", INTEGER, minimum=0, maximum=4294967295, advanced=True, code="invalid_seed"),
    )

    # Settings ----------------------------------------------------------------

    def _tool(self, context, config):
        return context.find_tool(IMAGE_TASK, IMAGE_PROVIDERS, config["tool_id"] or None)

    @staticmethod
    def _aspect_ratio(context, config):
        if config["aspect_ratio"] != "auto":
            return config["aspect_ratio"]
        return WORKSPACE_ASPECT.get(context.workspace_settings.get("video_orientation", "vertical"), "9:16")

    def _problem(self, context, config, tool):
        """A readiness/execution problem as (status, detail, code, field), or ``None``."""
        if tool is None and config["tool_id"]:
            return "tool_unavailable", TOOL_UNAVAILABLE_DETAIL, "tool_unavailable", "tool_id"
        if tool is None:
            return "missing_tool", MISSING_TOOL_DETAIL, None, None
        if issue := image_provider_config_issue(tool.provider):
            return issue[0], issue[1], issue[0], None
        model = image_model(tool.provider, tool.model)
        if model is None:
            return "unsupported_model", UNSUPPORTED_MODEL_DETAIL, "unsupported_model", "tool_id"
        if self._aspect_ratio(context, config) not in model.aspect_ratios:
            return "invalid_settings", INVALID_CONFIG_DETAIL, "invalid_aspect_ratio", "aspect_ratio"
        if config["quality"] not in model.qualities:
            return "invalid_settings", INVALID_CONFIG_DETAIL, "invalid_quality", "quality"
        if config.get("seed") is not None and not model.supports_seed:
            return "invalid_settings", INVALID_CONFIG_DETAIL, "invalid_seed", "seed"
        return None

    # Execution ---------------------------------------------------------------

    @staticmethod
    def operations(context, config, inputs) -> tuple[str, list[Operation]]:
        """The mode and one operation per image, following the precedence in the module docstring."""
        prompt = clip(config["prompt"]) or clip(inputs.get("prompt"))
        if not prompt:
            scenes = scene_operations(inputs.get("scenes"))
            if scenes:
                return "scenes", scenes
            project = context.project
            prompt = clip((project.topic or project.title or "") if project else "")
        if not prompt:
            return "prompt", []
        return "prompt", [Operation(f"image:{number}", prompt) for number in range(1, config["count"] + 1)]

    def execute(self, context, node, inputs):
        config = self.config_values(inputs.config)
        tool = self._tool(context, config)
        if problem := self._problem(context, config, tool):
            status, detail, code, _ = problem
            return NodeExecutionResult.blocked(detail, NodeError(code or status, detail))
        mode, operations = self.operations(context, config, inputs)
        if not operations:
            return NodeExecutionResult.blocked(MISSING_INPUT_DETAIL, NodeError("missing_input", "Nothing to draw"))
        if len(operations) > MAX_OPERATIONS:
            return NodeExecutionResult.blocked(TOO_MANY_DETAIL, NodeError("invalid_request", "Too many images"))
        aspect_ratio = self._aspect_ratio(context, config)
        model = image_model(tool.provider, tool.model)
        try:
            for operation in operations:
                validate_image_request(model, ImageRequest(tool.model, operation.prompt, aspect_ratio,
                                                           config["quality"], seed=config.get("seed")))
        except ImageProviderError as exc:
            return NodeExecutionResult.blocked(INVALID_CONFIG_DETAIL, NodeError(exc.code, str(exc)))
        return queue_operations(context, node, kind="image", mode=mode, operations=operations,
                                cost=image_credit_cost(), detail=QUEUED_DETAIL,
                                payload={"node_type": self.node_type, "provider": tool.provider, "model": tool.model,
                                         "tool_id": tool.id, "aspect_ratio": aspect_ratio,
                                         "quality": config["quality"], "seed": config.get("seed")})

    def readiness(self, context, node):
        config = self.config_values(node.get("config"))
        cost = image_credit_cost()
        tool = self._tool(context, config)
        # Connected prompt text comes before scenes (see the module docstring).
        per_scene = (not clip(config["prompt"]) and scene_mode_expected(context, node)
                     and "prompt" not in connected_inputs(context, node))
        credits = cost if per_scene else cost * config["count"]
        if problem := self._problem(context, config, tool):
            status, detail, code, field = problem
            return NodeReadiness(status, detail, credits=credits, code=code, field=field)
        if context.credit_balance < credits:
            return NodeReadiness("insufficient_credits", f"Cần {credits} credits; hiện có {context.credit_balance}.",
                                 credits=credits)
        if per_scene:
            return NodeReadiness("ready", f"Sẵn sàng; giữ {cost} credits cho mỗi cảnh khi bước bắt đầu.",
                                 credits=credits, code="per_scene")
        return NodeReadiness("ready", f"Sẵn sàng tạo ảnh; dự kiến giữ {credits} credits.", credits=credits)
