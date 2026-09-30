"""Small, safe provider facts persisted for credit reconciliation."""
from datetime import datetime, timezone
import json
import re

from app.providers.errors import CATEGORIES

SAFE_ERROR_CODES = CATEGORIES | frozenset({
    "not_found", "unsafe_url", "provider_failed", "provider_canceled", "missing_key", "invalid_config",
    "unsupported_provider", "unsupported_model", "submission_unknown", "not_ready", "result_expired",
    "storage_limit_exceeded", "attempts_exhausted", "worker_error", "invalid_step_state", "job_expired",
})
PROVIDER_STATES = frozenset({"queued", "running", "completed", "failed", "cancelled", "unknown"})


def safe_error_code(code, *, fallback="provider_failed"):
    return code if isinstance(code, str) and code in SAFE_ERROR_CODES else fallback


def safe_request_id(value):
    """An identifier only: never preserve provider URLs, query strings or response bodies."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value) else None


def step_output(step):
    value = json.loads(step.output) if step.output else {}
    return value if isinstance(value, dict) else {}


def provider_progress(output, *, stage, started=False, accepted=False, polled=False, status=None,
                      request_id=None):
    """Update only normalized facts; submitted_at means a known successful response."""
    old = output.get("provider_job")
    old = old if isinstance(old, dict) else {}
    submission = output.get("submission")
    submission = submission if isinstance(submission, dict) else {}
    now = datetime.now(timezone.utc).isoformat()

    def timestamp(key):
        value = old.get(key)
        if isinstance(value, str) and len(value) <= 40:
            try:
                return datetime.fromisoformat(value).isoformat()
            except ValueError:
                pass
        return None

    progress = {
        "stage": stage,
        "submission_started_at": now if started else timestamp("submission_started_at"),
        "submitted_at": (timestamp("submitted_at") or now) if accepted else timestamp("submitted_at"),
        "last_polled_at": now if polled else timestamp("last_polled_at"),
        "last_provider_status": status if status in PROVIDER_STATES else (
            old.get("last_provider_status") if old.get("last_provider_status") in PROVIDER_STATES else "unknown"),
        "submission_succeeded": accepted or old.get("submission_succeeded") is True or bool(submission),
        "remote_request_id": safe_request_id(request_id) or safe_request_id(submission.get("request_id"))
                             or safe_request_id(old.get("remote_request_id")),
    }
    output["provider_job"] = progress
    return output
