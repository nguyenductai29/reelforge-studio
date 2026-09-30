"""Minimal structured logging for the API, the workers and the CLI tools.

``configure_logging()`` (called once per process: at API startup, and in each
worker's and tool's ``main``) sends the ``app.*`` loggers to stderr, one JSON
object per line::

    {"ts": "...", "level": "INFO", "logger": "app.text_worker", "event": "job_claimed", "job_id": "...", ...}

``REELFORGE_LOG_FORMAT=text`` prints ``event key=value …`` instead, and
``REELFORGE_LOG_LEVEL`` sets the level (default ``INFO``).

Log events with ``log_event(logger, "job_claimed", job_id=..., provider=...)``.
Secrets never reach the output: a field named like a secret (``api_key``,
``authorization``, ``token``…) is replaced, and the values of secret
environment variables are scrubbed from every formatted line. Prompts are
logged by length only.
"""
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
from typing import Any

REDACTED = "[redacted]"
# Provider keys and other credentials the runtime environment may hold.
SECRET_ENV_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "FAL_KEY", "RUNWARE_API_KEY",
                    "REPLICATE_API_TOKEN", "RUNWAYML_API_SECRET", "DOLA_API_KEY", "GOOGLE_OAUTH_CLIENT_SECRET",
                    "REELFORGE_TOKEN_ENCRYPTION_KEY")
_SECRET_ENV = re.compile(r"(_KEY|_TOKEN|_SECRET|PASSWORD)$")
# Field names whose values are never logged; counts such as "max_tokens" do not match.
_SECRET_FIELD = re.compile(r"(^|_)(api_?key|key|secret|password|authorization|cookie|token)$", re.IGNORECASE)
_MIN_SECRET_CHARS = 6
_HANDLER_NAME = "reelforge-structured"


def secret_values() -> list[str]:
    """Current values of secret environment variables, longest first."""
    values = {os.environ.get(name, "") for name in SECRET_ENV_NAMES}
    values |= {value for name, value in os.environ.items() if _SECRET_ENV.search(name)}
    return sorted((value.strip() for value in values if len(value.strip()) >= _MIN_SECRET_CHARS),
                  key=len, reverse=True)


def scrub(text: str) -> str:
    for value in secret_values():
        text = text.replace(value, REDACTED)
    return text


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: REDACTED if _SECRET_FIELD.search(key) else value for key, value in fields.items()}


class StructuredFormatter(logging.Formatter):
    def __init__(self, style: str = "json"):
        super().__init__()
        self.style = style

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        event = getattr(record, "event", None) or message
        entry: dict[str, Any] = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                                 "level": record.levelname, "logger": record.name, "event": event}
        if message != event:
            entry["message"] = message
        entry.update(safe_fields(getattr(record, "fields", None) or {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if self.style == "text":
            extras = " ".join(f"{key}={value}" for key, value in entry.items() if key not in ("ts", "level", "logger", "event"))
            line = f"{entry['ts']} {record.levelname} {record.name} {event}" + (f" {extras}" if extras else "")
        else:
            line = json.dumps(entry, ensure_ascii=False, default=str)
        return scrub(line)


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Log one named event with identifiers such as run_id, job_id, provider or model."""
    if logger.isEnabledFor(level):
        logger.log(level, event, extra={"event": event, "fields": fields})


# Job payload fields that are safe to log; the prompt is logged by length only.
SAFE_PAYLOAD_FIELDS = ("kind", "node_type", "provider", "model", "model_id", "tool_id", "language", "max_tokens",
                       "temperature", "response_format", "aspect_ratio", "duration", "resolution", "generate_audio",
                       "credits")


def payload_summary(payload) -> dict[str, Any]:
    summary = {key: payload[key] for key in SAFE_PAYLOAD_FIELDS if key in payload}
    for key in ("prompt", "system_prompt"):
        if isinstance(payload.get(key), str):
            summary[f"{key}_chars"] = len(payload[key])
    return summary


def configure_logging(stream=None) -> None:
    """Send ``app.*`` loggers to stderr as structured lines; calling it again changes nothing."""
    logger = logging.getLogger("app")
    if any(handler.get_name() == _HANDLER_NAME for handler in logger.handlers):
        return
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(StructuredFormatter("text" if os.environ.get("REELFORGE_LOG_FORMAT") == "text" else "json"))
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("REELFORGE_LOG_LEVEL", "INFO").upper())
    logger.propagate = False


def env_summary(names) -> dict[str, bool]:
    """Which of these variables are set, without their values."""
    return {name: bool(os.environ.get(name, "").strip()) for name in names}
