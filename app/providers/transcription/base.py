"""Provider-neutral speech-to-text: one audio file in, text with timestamped segments out.

Adapters translate one vendor API into ``TranscriptResult``; nothing outside
``app/providers/transcription/`` sees a vendor response. Keys come from the
server environment and never leave the adapter.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import httpx

from app.providers.errors import ProviderError

DEFAULT_TIMEOUT = httpx.Timeout(600.0, connect=10.0)


class TranscriptionProviderError(ProviderError):
    """A local or provider error with a stable code and category (app/providers/errors.py)."""


@dataclass(frozen=True)
class TranscriptionModel:
    model_id: str
    max_bytes: int = 25 * 1024 * 1024
    extensions: tuple[str, ...] = (".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4")


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptResult:
    provider: str
    model: str
    text: str
    language: str | None
    duration: float | None
    segments: tuple[TranscriptSegment, ...] = field(default_factory=tuple)
    remote_request_id: str | None = None


class TranscriptionProvider(ABC):
    name: str = ""
    models: Mapping[str, TranscriptionModel] = {}

    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None,
                 timeout: httpx.Timeout = DEFAULT_TIMEOUT):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise TranscriptionProviderError("invalid_config", f"{self.name} API key is required")
        self._api_key = api_key.strip()
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def transcribe(self, path: Path, *, model: str, language: str | None = None) -> TranscriptResult:
        capabilities = self.models.get(model)
        if capabilities is None:
            raise TranscriptionProviderError("unsupported_model", "This transcription model is not supported")
        if not isinstance(path, Path) or not path.is_file() or path.suffix.lower() not in capabilities.extensions:
            raise TranscriptionProviderError("invalid_request", "An audio file is required")
        if path.stat().st_size > capabilities.max_bytes:
            raise TranscriptionProviderError("invalid_request", "The audio is larger than the provider accepts")
        if language is not None and (not isinstance(language, str) or not language.isalpha() or len(language) > 3):
            raise TranscriptionProviderError("invalid_request", "Language must be an ISO 639-1 code")
        return self._transcribe(path, model, language)

    @abstractmethod
    def _transcribe(self, path: Path, model: str, language: str | None) -> TranscriptResult:
        """Call the vendor API and normalize its response."""
