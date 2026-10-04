"""Stable error categories shared by every text and video provider adapter.

Each adapter raises its own ``ProviderError`` subclass with a specific ``code``
(for example ``submission_unknown`` or ``not_found``) and a ``category`` from
the small set below. Workers, logs and the smoke test report the category;
the code keeps the detail. The message never contains a key or a response
body, so it is safe in step outputs. ``provider_detail`` keeps a short excerpt
of the provider's error response for server logs only.
"""
import re
from typing import Any

import httpx

PROVIDER_DETAIL_CHARS = 200

AUTHENTICATION_ERROR = "authentication_error"
RATE_LIMITED = "rate_limited"
INVALID_REQUEST = "invalid_request"
CONTENT_REJECTED = "content_rejected"
PROVIDER_UNAVAILABLE = "provider_unavailable"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
EMPTY_OUTPUT = "empty_output"
INVALID_RESPONSE = "invalid_response"
# The provider account has no credit left (HTTP 402).
BILLING_ERROR = "billing_error"
# The provider accepted a job, then reported that generating it failed.
GENERATION_FAILED = "generation_failed"
# Found before any request: a missing key, or an unsupported provider or model.
CONFIGURATION_ERROR = "configuration_error"

CATEGORIES = frozenset({AUTHENTICATION_ERROR, RATE_LIMITED, INVALID_REQUEST, CONTENT_REJECTED, PROVIDER_UNAVAILABLE,
                        TIMEOUT, NETWORK_ERROR, EMPTY_OUTPUT, INVALID_RESPONSE, BILLING_ERROR, GENERATION_FAILED,
                        CONFIGURATION_ERROR})

_CATEGORY_BY_CODE = {
    **{category: category for category in CATEGORIES},
    "not_found": INVALID_REQUEST,
    "unsafe_url": INVALID_RESPONSE,
    "provider_failed": GENERATION_FAILED,
    "missing_key": CONFIGURATION_ERROR,
    "invalid_config": CONFIGURATION_ERROR,
    "unsupported_provider": CONFIGURATION_ERROR,
    "unsupported_model": CONFIGURATION_ERROR,
    # A submit whose outcome is unknown; adapters pass the precise category when they know it.
    "submission_unknown": NETWORK_ERROR,
}


def error_category(code: str | None) -> str:
    """The category of an adapter code, or of a failure type a provider reported for a finished job."""
    if code in _CATEGORY_BY_CODE:
        return _CATEGORY_BY_CODE[code]
    lowered = (code or "").lower()
    if any(word in lowered for word in ("policy", "moderation", "safety", "nsfw", "content", "blocked")):
        return CONTENT_REJECTED
    if "timeout" in lowered or "timed_out" in lowered:
        return TIMEOUT
    if "ratelimit" in lowered.replace("_", "") or "quota" in lowered:
        return RATE_LIMITED
    return GENERATION_FAILED


def status_error(status: int) -> tuple[str, bool]:
    """The ``(code, retryable)`` for an HTTP error status, before any adapter-specific rule."""
    if status in (400, 413, 422):
        return INVALID_REQUEST, False
    if status in (401, 403):
        return AUTHENTICATION_ERROR, False
    if status == 402:
        return BILLING_ERROR, False
    if status == 404:
        return "not_found", False
    if status == 408:
        return TIMEOUT, True
    if status == 429:
        return RATE_LIMITED, True
    if status >= 500:
        return PROVIDER_UNAVAILABLE, True
    return INVALID_RESPONSE, False


class ProviderError(Exception):
    """A local or provider error with a stable ``code`` and ``category``."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None,
                 category: str | None = None, provider_detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.category = category or error_category(code)
        # Server logs and the smoke test only; never stored in a step or returned by the API.
        self.provider_detail = provider_detail

    def describe(self) -> dict:
        """Facts for logs and reports (log output is scrubbed of secret values)."""
        facts = {"code": self.code, "category": self.category, "retryable": self.retryable,
                 "http_status": self.http_status}
        if self.provider_detail:
            facts["provider_detail"] = self.provider_detail
        return facts


def response_detail(response: Any) -> str | None:
    """The provider's own error message, shortened, for diagnosing a failed request in the server log."""
    try:
        data = response.json()
    except (ValueError, httpx.ResponseNotRead):
        data = None
    message = None
    if isinstance(data, dict):
        error = data.get("error")
        errors = data.get("errors")
        candidates = (error.get("message") if isinstance(error, dict) else error, data.get("message"),
                      data.get("detail"), data.get("title"),
                      errors[0].get("message") if isinstance(errors, list) and errors and isinstance(errors[0], dict) else None)
        message = next((value for value in candidates if isinstance(value, str) and value.strip()), None)
    if message is None:
        try:
            message = response.text
        except (httpx.ResponseNotRead, UnicodeDecodeError):
            return None
    message = re.sub(r"\s+", " ", message or "").strip()
    if not message:
        return None
    return message if len(message) <= PROVIDER_DETAIL_CHARS else message[:PROVIDER_DETAIL_CHARS - 1] + "…"
