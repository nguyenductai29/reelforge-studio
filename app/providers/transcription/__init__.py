"""Speech-to-text providers, chosen by a workspace's AI tools (task ``transcription``).

The first provider is OpenAI ``whisper-1``: one documented HTTP call that returns
timestamped segments, which Movie Recap scene matching needs. Other providers
can be added to ``TRANSCRIPTION_PROVIDERS`` without touching the workflow code.
"""
from dataclasses import dataclass
import os

from app.providers.transcription.base import (TranscriptionModel, TranscriptionProvider, TranscriptionProviderError,
                                              TranscriptResult, TranscriptSegment)
from app.providers.transcription.openai import OpenAITranscriptionProvider

TRANSCRIPTION_TASK = "transcription"


@dataclass(frozen=True)
class TranscriptionProviderSpec:
    provider_type: type[TranscriptionProvider]
    key_env: str

    @property
    def models(self):
        return self.provider_type.models


TRANSCRIPTION_PROVIDERS = {"openai": TranscriptionProviderSpec(OpenAITranscriptionProvider, "OPENAI_API_KEY")}


def transcription_model(provider: str | None, model: str | None) -> TranscriptionModel | None:
    spec = TRANSCRIPTION_PROVIDERS.get(provider or "")
    return spec.models.get(model or "") if spec else None


def transcription_config_issue(provider_name: str) -> tuple[str, str] | None:
    spec = TRANSCRIPTION_PROVIDERS.get(provider_name)
    if spec is None:
        return "unsupported_provider", "Provider phiên âm này chưa được hỗ trợ."
    if not os.environ.get(spec.key_env, "").strip():
        return "missing_key", f"Server cần {spec.key_env}."
    return None


def create_transcription_provider(provider_name: str, **kwargs) -> TranscriptionProvider:
    spec = TRANSCRIPTION_PROVIDERS.get(provider_name)
    if spec is None:
        raise TranscriptionProviderError("unsupported_provider", f"Unsupported transcription provider {provider_name!r}")
    key = os.environ.get(spec.key_env, "")
    if not key.strip():
        raise TranscriptionProviderError("missing_key", f"{spec.key_env} is not set")
    return spec.provider_type(key, **kwargs)


def transcription_credit_cost() -> int:
    """Credits for one transcription (``TRANSCRIPTION_CREDITS_PER_JOB``, default 2)."""
    try:
        amount = int(os.environ.get("TRANSCRIPTION_CREDITS_PER_JOB", "").strip() or "2")
    except ValueError as exc:
        raise RuntimeError("TRANSCRIPTION_CREDITS_PER_JOB must be a positive integer") from exc
    if not 1 <= amount <= 100000:
        raise RuntimeError("TRANSCRIPTION_CREDITS_PER_JOB must be between 1 and 100000")
    return amount


__all__ = ["TRANSCRIPTION_PROVIDERS", "TRANSCRIPTION_TASK", "OpenAITranscriptionProvider", "TranscriptResult",
           "TranscriptSegment", "TranscriptionModel", "TranscriptionProvider", "TranscriptionProviderError",
           "create_transcription_provider", "transcription_config_issue", "transcription_credit_cost",
           "transcription_model"]
