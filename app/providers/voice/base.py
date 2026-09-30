"""Provider-neutral text-to-speech: capabilities, requests, normalized results and errors.

A voice request turns one piece of text into one audio file. Adapters translate
one vendor API into ``VoiceResult`` (the audio bytes, their type and duration,
and a remote request ID when the vendor returns one); nothing outside
``app/providers/voice/`` sees a vendor response. Keys come from the server
environment and never leave the adapter.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from app.providers.errors import ProviderError

DEFAULT_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
# Delivery presets a model may support; each adapter says how it applies them.
STYLES = ("neutral", "calm", "cheerful", "energetic", "serious", "slow")


class VoiceProviderError(ProviderError):
    """A local or provider error with a stable code and category (app/providers/errors.py)."""


@dataclass(frozen=True)
class VoiceModel:
    """What one model accepts; the node settings and readiness check against it."""

    model_id: str
    voices: tuple[str, ...]
    formats: tuple[str, ...] = ("wav",)
    styles: tuple[str, ...] = ("neutral",)
    max_text_chars: int = 5000


@dataclass(frozen=True)
class VoiceRequest:
    model: str
    text: str
    voice: str
    style: str = "neutral"
    format: str = "wav"


@dataclass(frozen=True)
class VoiceResult:
    provider: str
    model: str
    audio: bytes = field(repr=False)
    content_type: str
    duration: float | None = None
    remote_request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)


def validate_voice_request(model: VoiceModel | None, request: VoiceRequest) -> None:
    """Reject a request the model cannot serve, before anything is reserved or sent."""
    if model is None or request.model != model.model_id:
        raise VoiceProviderError("unsupported_model", "This voice model is not supported")
    text = request.text.strip() if isinstance(request.text, str) else ""
    if not text:
        raise VoiceProviderError("invalid_request", "There is no text to read")
    if len(text) > model.max_text_chars:
        raise VoiceProviderError("invalid_request", f"Text is longer than {model.max_text_chars} characters")
    if request.voice not in model.voices:
        raise VoiceProviderError("invalid_request", "This voice is not available for the model")
    if request.style not in model.styles:
        raise VoiceProviderError("invalid_request", "This delivery style is not supported")
    if request.format not in model.formats:
        raise VoiceProviderError("invalid_request", "This audio format is not supported")


class VoiceGenerationProvider(ABC):
    """One vendor's text-to-speech API."""

    name: str = ""
    models: Mapping[str, VoiceModel] = {}

    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None,
                 timeout: httpx.Timeout = DEFAULT_TIMEOUT):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise VoiceProviderError("invalid_config", f"{self.name} API key is required")
        self._api_key = api_key.strip()
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "VoiceGenerationProvider":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def capabilities(self, model: str) -> VoiceModel | None:
        return self.models.get(model)

    def validate(self, request: VoiceRequest) -> None:
        validate_voice_request(self.capabilities(request.model), request)

    def generate(self, request: VoiceRequest) -> VoiceResult:
        self.validate(request)
        return self._generate(request)

    @abstractmethod
    def _generate(self, request: VoiceRequest) -> VoiceResult:
        """Call the vendor API and normalize its response."""
