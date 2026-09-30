"""Google Gemini API (generateContent) adapter.

Contract: https://ai.google.dev/api/generate-content
The key goes in the x-goog-api-key header, never in the URL.
"""
import re

from app.providers.text.base import (FINISH_FILTERED, FINISH_LENGTH, FINISH_OTHER, FINISH_STOP, TextGenerationProvider,
                                     TextProviderError, TextRequest, TextResult, TextUsage, finished_text, metadata,
                                     system_prompt_for)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_FINISH = {"STOP": FINISH_STOP, "MAX_TOKENS": FINISH_LENGTH, "SAFETY": FINISH_FILTERED,
           "RECITATION": FINISH_FILTERED, "BLOCKLIST": FINISH_FILTERED, "PROHIBITED_CONTENT": FINISH_FILTERED,
           "SPII": FINISH_FILTERED}


class GeminiTextProvider(TextGenerationProvider):
    name = "gemini"
    # The model is part of the URL path, so only plain names (optionally "models/<name>") are allowed.
    model_pattern = re.compile(r"(models/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")

    def _generate(self, request: TextRequest) -> TextResult:
        model = request.model.removeprefix("models/")
        config = {"maxOutputTokens": request.max_tokens}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.response_format == "json":
            config["responseMimeType"] = "application/json"
        payload = {"contents": [{"role": "user", "parts": [{"text": request.prompt}]}], "generationConfig": config}
        system_prompt = system_prompt_for(request)
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        data = self._post_json(f"{API_BASE}/{model}:generateContent",
                               headers={"x-goog-api-key": self._api_key}, payload=payload)

        feedback = data.get("promptFeedback") if isinstance(data.get("promptFeedback"), dict) else {}
        if feedback.get("blockReason"):
            raise TextProviderError("content_rejected", "gemini blocked the prompt")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise TextProviderError("invalid_response", "gemini returned no candidates")
        candidate = candidates[0]
        raw_finish = candidate.get("finishReason")
        finish = _FINISH.get(raw_finish, FINISH_OTHER)
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        # Thought summaries are not part of the answer.
        text = finished_text("".join(part["text"] for part in parts if isinstance(part, dict)
                                     and isinstance(part.get("text"), str) and not part.get("thought")), finish)
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        version = data.get("modelVersion") if isinstance(data.get("modelVersion"), str) and data.get("modelVersion") else model
        return TextResult(text=text, provider=self.name, model=version,
                          usage=TextUsage.of(usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
                                             usage.get("totalTokenCount")),
                          raw_metadata=metadata(data.get("responseId"), finish, raw_finish))
