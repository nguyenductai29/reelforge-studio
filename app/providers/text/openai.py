"""OpenAI Chat Completions adapter.

Contract: https://platform.openai.com/docs/api-reference/chat/create
Temperature is sent only when set, because reasoning models reject non-default values.
"""
from app.providers.text.base import (FINISH_FILTERED, FINISH_LENGTH, FINISH_OTHER, FINISH_STOP, TextGenerationProvider,
                                     TextProviderError, TextRequest, TextResult, TextUsage, finished_text, metadata,
                                     system_prompt_for)

API_URL = "https://api.openai.com/v1/chat/completions"
_FINISH = {"stop": FINISH_STOP, "length": FINISH_LENGTH, "content_filter": FINISH_FILTERED}


class OpenAITextProvider(TextGenerationProvider):
    name = "openai"

    def _generate(self, request: TextRequest) -> TextResult:
        messages = []
        system_prompt = system_prompt_for(request)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if request.images:
            content = [{"type": "text", "text": request.prompt}]
            content += [{"type": "image_url", "image_url": {"url": f"data:{image.mime_type};base64,{image.base64()}",
                                                            "detail": "low"}} for image in request.images]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": request.prompt})
        payload = {"model": request.model, "messages": messages, "max_completion_tokens": request.max_tokens}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        data = self._post_json(API_URL, headers={"Authorization": f"Bearer {self._api_key}"}, payload=payload)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise TextProviderError("invalid_response", "openai returned no choices")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        raw_finish = choice.get("finish_reason")
        finish = _FINISH.get(raw_finish, FINISH_OTHER)
        if message.get("refusal"):
            finish = FINISH_FILTERED
        text = finished_text(None if message.get("refusal") else message.get("content"), finish)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        model = data.get("model") if isinstance(data.get("model"), str) and data.get("model") else request.model
        return TextResult(text=text, provider=self.name, model=model,
                          usage=TextUsage.of(usage.get("prompt_tokens"), usage.get("completion_tokens"),
                                             usage.get("total_tokens")),
                          raw_metadata=metadata(data.get("id"), finish, raw_finish))
