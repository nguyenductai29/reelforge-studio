"""Text-to-speech providers, chosen by a workspace's AI tools (task ``voice``); keys stay in the environment.

The first provider is Google Gemini TTS: it uses the ``GEMINI_API_KEY`` that
text generation already uses (and that was verified live), needs no new
dependency, and returns the speech in the same HTTP response, which suits short
narration segments. Other providers can be added to ``VOICE_PROVIDERS``
without touching the workflow code.
"""
from dataclasses import dataclass
import os

from app.providers.voice.base import (STYLES, VoiceGenerationProvider, VoiceModel, VoiceProviderError, VoiceRequest,
                                      VoiceResult, validate_voice_request)
from app.providers.voice.gemini import DEFAULT_VOICE, GeminiVoiceProvider

VOICE_TASK = "voice"


@dataclass(frozen=True)
class VoiceProviderSpec:
    provider_type: type[VoiceGenerationProvider]
    key_env: str
    default_voice: str

    @property
    def models(self):
        return self.provider_type.models


VOICE_PROVIDERS = {
    "gemini": VoiceProviderSpec(GeminiVoiceProvider, "GEMINI_API_KEY", DEFAULT_VOICE),
}
# Every voice any provider offers, for the node's settings; readiness checks the chosen model's own list.
ALL_VOICES = tuple(dict.fromkeys(voice for spec in VOICE_PROVIDERS.values() for model in spec.models.values()
                                 for voice in model.voices))


def voice_model(provider: str | None, model: str | None) -> VoiceModel | None:
    spec = VOICE_PROVIDERS.get(provider or "")
    return spec.models.get(model or "") if spec else None


def voice_provider_config_issue(provider_name: str) -> tuple[str, str] | None:
    """Operator configuration problems, checked without any request."""
    spec = VOICE_PROVIDERS.get(provider_name)
    if spec is None:
        return "unsupported_provider", "Provider giọng đọc này chưa được hỗ trợ."
    if not os.environ.get(spec.key_env, "").strip():
        return "missing_key", f"Server cần {spec.key_env}."
    return None


def create_voice_provider(provider_name: str, **kwargs) -> VoiceGenerationProvider:
    spec = VOICE_PROVIDERS.get(provider_name)
    if spec is None:
        raise VoiceProviderError("unsupported_provider", f"Unsupported voice provider {provider_name!r}")
    key = os.environ.get(spec.key_env, "")
    if not key.strip():
        raise VoiceProviderError("missing_key", f"{spec.key_env} is not set")
    return spec.provider_type(key, **kwargs)


def voice_credit_cost() -> int:
    """Credits held for, and charged by, one narration file (``VOICE_CREDITS_PER_GENERATION``, default 1)."""
    try:
        amount = int(os.environ.get("VOICE_CREDITS_PER_GENERATION", "").strip() or "1")
    except ValueError as exc:
        raise RuntimeError("VOICE_CREDITS_PER_GENERATION must be a positive integer") from exc
    if not 1 <= amount <= 100000:
        raise RuntimeError("VOICE_CREDITS_PER_GENERATION must be between 1 and 100000")
    return amount


__all__ = ["ALL_VOICES", "STYLES", "VOICE_PROVIDERS", "VOICE_TASK", "GeminiVoiceProvider", "VoiceGenerationProvider",
           "VoiceModel", "VoiceProviderError", "VoiceProviderSpec", "VoiceRequest", "VoiceResult",
           "create_voice_provider", "validate_voice_request", "voice_credit_cost", "voice_model",
           "voice_provider_config_issue"]
