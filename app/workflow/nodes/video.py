"""Video: reserves credits and queues one text-to-video job for the video worker."""
from app import usage
from app.providers.catalog import (ORIENTATION_ASPECT, PROVIDER_ERRORS, VIDEO_PROVIDERS, video_credit_cost,
                                   video_provider_config_issue, video_request_defaults)
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL, NodeHandler
from app.workflow.nodes.pending import pending_ai_task
from app.workflow.results import JobRequest, NodeExecutionResult, NodeReadiness, RunRequestError


class VideoNodeHandler(NodeHandler):
    node_type = "video"

    def _aspect_ratio(self, context):
        return ORIENTATION_ASPECT.get(context.workspace_settings.get("video_orientation", "vertical"))

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
            tool = self._tool(context, options.tool_id or None)
            if options.tool_id and not tool:
                raise RunRequestError(400, "Selected video tool is unavailable", code="tool_unavailable",
                                      step_detail="Chưa chọn model video được hỗ trợ cho bước video.")
            provider_name = tool.provider if tool else None
            model_id = tool.model if tool else None
            tool_reference = tool.id if tool else None
            project = context.project
            prompt = (options.prompt_override if options.prompt_override is not None
                      else project.topic or project.title).strip()
            aspect_ratio = self._aspect_ratio(context)
            duration, resolution, generate_audio = video_request_defaults(provider_name)
            cost = video_credit_cost()
        if provider_name not in VIDEO_PROVIDERS:
            return NodeExecutionResult.blocked("Chưa chọn model video được hỗ trợ cho bước video.")
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
                                          {"prompt": prompt, "provider": provider_name, "model": model_id},
                                          metadata={"credits_reserved": cost})

    def readiness(self, context, node):
        quote = video_credit_cost()
        tool = self._tool(context, context.options.tool_id)
        if context.count_nodes(self.node_type) != 1:
            status, detail = "unsupported_graph", "Hiện chỉ hỗ trợ một bước video trong mỗi workflow."
        elif not tool:
            status, detail = "missing_tool", "Chọn model video được hỗ trợ trong Công cụ AI."
        elif issue := video_provider_config_issue(tool.provider):
            status, detail = issue
        elif not self._aspect_ratio(context):
            status, detail = "unsupported_aspect", "Model video chưa hỗ trợ tỷ lệ vuông."
        elif tool.model not in VIDEO_PROVIDERS[tool.provider].module.VIDEO_MODELS:
            status, detail = "unsupported_model", "Model video này chưa được hỗ trợ."
        elif context.credit_balance < quote:
            status, detail = "insufficient_credits", f"Cần {quote} credits; hiện có {context.credit_balance}."
        else:
            status, detail = "ready", f"Sẵn sàng tạo clip; dự kiến giữ {quote} credits."
        return NodeReadiness(status, detail, credits=quote)
