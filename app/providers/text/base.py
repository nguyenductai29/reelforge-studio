"""Provider-neutral text generation: request limits, normalized results and errors.

Adapters translate one vendor API into ``TextResult``; nothing outside
``app/providers/text/`` sees a vendor response.
"""
from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass, field
import math
import re
from typing import Any, Mapping

import httpx

from app.providers.errors import ProviderError, response_detail, status_error

RESPONSE_FORMATS = frozenset({"text", "json"})
MAX_PROMPT_CHARS = 100_000
MAX_SYSTEM_CHARS = 20_000
MAX_OUTPUT_TOKENS = 32_768
# Images sent with a prompt (the Visual Analysis step's frames): a few small JPEG, PNG or WebP files.
MAX_IMAGES = 20
MAX_IMAGE_BYTES = 4 * 1024 * 1024
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
JSON_INSTRUCTION = "Respond with one valid JSON object and nothing else."

# Normalized finish reasons.
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_FILTERED = "content_filter"
FINISH_OTHER = "other"


class TextProviderError(ProviderError):
    """A local or provider error with a stable code and category; it never carries a key or a response body."""


@dataclass(frozen=True)
class TextImage:
    """One picture sent with the prompt; adapters encode it as their API expects."""

    data: bytes = field(repr=False)
    mime_type: str = "image/jpeg"

    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True)
class TextRequest:
    model: str
    prompt: str
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int = 1024
    response_format: str = "text"
    images: tuple[TextImage, ...] = ()


@dataclass(frozen=True)
class TextUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def of(cls, input_tokens: Any, output_tokens: Any, total_tokens: Any = None) -> "TextUsage":
        def count(value):
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        inputs, outputs, total = count(input_tokens), count(output_tokens), count(total_tokens)
        if total is None and inputs is not None and outputs is not None:
            total = inputs + outputs
        return cls(inputs, outputs, total)

    def as_dict(self) -> dict[str, int | None]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens}


@dataclass(frozen=True)
class TextResult:
    text: str
    usage: TextUsage
    provider: str
    model: str
    # Small, JSON-safe facts about the response: response_id, finish_reason, provider_finish_reason.
    raw_metadata: Mapping[str, str | None] = field(default_factory=dict)


def validate_request(request: TextRequest, *, model_pattern) -> None:
    if not isinstance(request.model, str) or not model_pattern.fullmatch(request.model):
        raise TextProviderError("invalid_request", "Model name is invalid")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise TextProviderError("invalid_request", "Prompt is required")
    if len(request.prompt) > MAX_PROMPT_CHARS:
        raise TextProviderError("invalid_request", "Prompt is too long")
    if request.system_prompt is not None and (not isinstance(request.system_prompt, str)
                                              or len(request.system_prompt) > MAX_SYSTEM_CHARS):
        raise TextProviderError("invalid_request", "System prompt is invalid")
    if request.temperature is not None and (isinstance(request.temperature, bool)
                                            or not isinstance(request.temperature, (int, float))
                                            or not math.isfinite(request.temperature)
                                            or not 0 <= request.temperature <= 2):
        raise TextProviderError("invalid_request", "Temperature must be between 0 and 2")
    if (isinstance(request.max_tokens, bool) or not isinstance(request.max_tokens, int)
            or not 1 <= request.max_tokens <= MAX_OUTPUT_TOKENS):
        raise TextProviderError("invalid_request", "max_tokens is out of range")
    if request.response_format not in RESPONSE_FORMATS:
        raise TextProviderError("invalid_request", "Unsupported response format")
    if len(request.images) > MAX_IMAGES or any(
            not isinstance(image, TextImage) or image.mime_type not in IMAGE_TYPES
            or not isinstance(image.data, bytes) or not 0 < len(image.data) <= MAX_IMAGE_BYTES
            for image in request.images):
        raise TextProviderError("invalid_request", "Images must be at most 20 JPEG, PNG or WebP files of 4 MB")


def system_prompt_for(request: TextRequest) -> str | None:
    """The system prompt, with the JSON instruction added when JSON output is requested."""
    if request.response_format != "json":
        return request.system_prompt
    return f"{request.system_prompt}\n\n{JSON_INSTRUCTION}" if request.system_prompt else JSON_INSTRUCTION


def finished_text(text: Any, finish_reason: str) -> str:
    """The generated text, or a clear error instead of an empty result."""
    if isinstance(text, str) and text.strip():
        return text.strip()
    if finish_reason == FINISH_FILTERED:
        raise TextProviderError("content_rejected", "The provider declined to generate this content")
    if finish_reason == FINISH_LENGTH:
        raise TextProviderError("empty_output", "The token limit was reached before any text was produced")
    raise TextProviderError("empty_output", "The provider returned no text")


def http_error(provider: str, status: int, detail: str | None = None) -> TextProviderError:
    code, retryable = status_error(status)
    return TextProviderError(code, f"{provider} returned HTTP {status}", retryable=retryable, http_status=status,
                             provider_detail=detail)


def metadata(response_id: Any, finish_reason: str, provider_finish_reason: Any) -> dict[str, str | None]:
    def text(value):
        return value[:200] if isinstance(value, str) else None

    return {"response_id": text(response_id), "finish_reason": finish_reason,
            "provider_finish_reason": text(provider_finish_reason)}


class TextGenerationProvider(ABC):
    """One vendor's text API. Keys come from the server environment and never leave this object."""

    name: str = ""
    # Same characters the AI tool form accepts; adapters that put the model in a URL narrow it.
    model_pattern: re.Pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}\Z")

    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None,
                 timeout: httpx.Timeout = DEFAULT_TIMEOUT):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise TextProviderError("invalid_config", f"{self.name} API key is required")
        self._api_key = api_key.strip()
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "TextGenerationProvider":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def generate(self, *, model: str, prompt: str, system_prompt: str | None = None,
                 temperature: float | None = None, max_tokens: int = 1024,
                 response_format: str = "text", images: tuple[TextImage, ...] = ()) -> TextResult:
        request = TextRequest(model, prompt, system_prompt, temperature, max_tokens, response_format, tuple(images))
        self.validate(request)
        return self._generate(request)

    def validate(self, request: TextRequest) -> None:
        validate_request(request, model_pattern=self.model_pattern)

    @abstractmethod
    def _generate(self, request: TextRequest) -> TextResult:
        """Call the vendor API and normalize its response."""

    def _post_json(self, url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any]) -> dict:
        try:
            response = self.http_client.post(url, headers=dict(headers), json=payload, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise TextProviderError("timeout", f"{self.name} did not answer in time", retryable=True) from exc
        except httpx.RequestError as exc:
            raise TextProviderError("network_error", f"Could not contact {self.name}", retryable=True) from exc
        if not 200 <= response.status_code < 300:
            raise http_error(self.name, response.status_code, response_detail(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise TextProviderError("invalid_response", f"{self.name} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise TextProviderError("invalid_response", f"{self.name} returned an invalid response")
        return data
