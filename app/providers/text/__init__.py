"""Text generation providers, chosen by a workspace's AI tools; keys stay in the server environment.

The AI tool task ``script`` (shown as "Text" in the Models page) selects the
provider and model for every text node. Credentials are read from
``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY`` or ``GEMINI_API_KEY`` and are never stored.
"""
from dataclasses import dataclass
import os

from app.providers.text.anthropic import AnthropicTextProvider
from app.providers.text.base import TextGenerationProvider, TextProviderError, TextResult, TextUsage
from app.providers.text.gemini import GeminiTextProvider
from app.providers.text.openai import OpenAITextProvider
from app import system_config

TEXT_TASK = "script"


@dataclass(frozen=True)
class TextProviderSpec:
    provider_type: type[TextGenerationProvider]
    key_env: str


TEXT_PROVIDERS = {
    "openai": TextProviderSpec(OpenAITextProvider, "OPENAI_API_KEY"),
    "anthropic": TextProviderSpec(AnthropicTextProvider, "ANTHROPIC_API_KEY"),
    "gemini": TextProviderSpec(GeminiTextProvider, "GEMINI_API_KEY"),
}


def text_provider_config_issue(provider_name: str) -> tuple[str, str] | None:
    spec = TEXT_PROVIDERS.get(provider_name)
    if spec is None:
        return "unsupported_provider", "Provider văn bản này chưa được hỗ trợ."
    if not system_config.env(spec.key_env).strip():
        return "missing_key", f"Server cần {spec.key_env}."
    return None


def create_text_provider(provider_name: str, *, http_client=None) -> TextGenerationProvider:
    spec = TEXT_PROVIDERS.get(provider_name)
    if spec is None:
        raise TextProviderError("unsupported_provider", f"Unsupported text provider {provider_name!r}")
    key = system_config.env(spec.key_env)
    if not key.strip():
        raise TextProviderError("missing_key", f"{spec.key_env} is not set")
    return spec.provider_type(key, http_client=http_client)


def text_credit_cost() -> int:
    """Credits held for, and charged by, one text generation (``TEXT_CREDITS_PER_GENERATION``)."""
    try:
        amount = int(system_config.env("TEXT_CREDITS_PER_GENERATION").strip() or "1")
    except ValueError as exc:
        raise RuntimeError("TEXT_CREDITS_PER_GENERATION must be a positive integer") from exc
    if not 1 <= amount <= 100000:
        raise RuntimeError("TEXT_CREDITS_PER_GENERATION must be between 1 and 100000")
    return amount


__all__ = ["TEXT_PROVIDERS", "TEXT_TASK", "TextGenerationProvider", "TextProviderError", "TextProviderSpec",
           "TextResult", "TextUsage", "create_text_provider", "text_credit_cost", "text_provider_config_issue"]
