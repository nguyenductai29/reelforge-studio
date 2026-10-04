"""Anthropic Messages API adapter.

Contract: https://docs.anthropic.com/en/api/messages
The API has no JSON response flag here, so JSON output is requested in the system prompt.
"""
from app.providers.text.base import (FINISH_FILTERED, FINISH_LENGTH, FINISH_OTHER, FINISH_STOP, TextGenerationProvider,
                                     TextProviderError, TextRequest, TextResult, TextUsage, finished_text, metadata,
                                     system_prompt_for)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
_FINISH = {"end_turn": FINISH_STOP, "stop_sequence": FINISH_STOP, "max_tokens": FINISH_LENGTH,
           "refusal": FINISH_FILTERED}


class AnthropicTextProvider(TextGenerationProvider):
    name = "anthropic"

    def _generate(self, request: TextRequest) -> TextResult:
        content = request.prompt
        if request.images:
            content = [{"type": "image", "source": {"type": "base64", "media_type": image.mime_type,
                                                    "data": image.base64()}} for image in request.images]
            content.append({"type": "text", "text": request.prompt})
        payload = {"model": request.model, "max_tokens": request.max_tokens,
                   "messages": [{"role": "user", "content": content}]}
        system_prompt = system_prompt_for(request)
        if system_prompt:
            payload["system"] = system_prompt
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        data = self._post_json(API_URL, headers={"x-api-key": self._api_key, "anthropic-version": API_VERSION},
                               payload=payload)

        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise TextProviderError("invalid_response", "anthropic returned no content")
        raw_finish = data.get("stop_reason")
        finish = _FINISH.get(raw_finish, FINISH_OTHER)
        text = finished_text("".join(block["text"] for block in blocks if isinstance(block, dict)
                                     and block.get("type") == "text" and isinstance(block.get("text"), str)), finish)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        model = data.get("model") if isinstance(data.get("model"), str) and data.get("model") else request.model
        return TextResult(text=text, provider=self.name, model=model,
                          usage=TextUsage.of(usage.get("input_tokens"), usage.get("output_tokens")),
                          raw_metadata=metadata(data.get("id"), finish, raw_finish))
