"""Runway Dev Gen-4.5 text-to-video adapter.

Contract: https://github.com/runwayml/openapi/blob/main/openapi.json
          https://docs.dev.runwayml.com/assets/outputs/

Runway output URLs are ephemeral and their delivery host is not part of the
public API contract. Operators must explicitly allowlist the exact hostnames
they receive from Runway via RUNWAY_OUTPUT_HOSTS before submitting jobs.
"""

from dataclasses import dataclass
import ipaddress
import math
import os
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit
import uuid

import httpx

from app.providers.errors import (NETWORK_ERROR, PROVIDER_UNAVAILABLE, TIMEOUT,
                                 ProviderError as BaseProviderError, response_detail, status_error)


API_BASE = "https://api.dev.runwayml.com/v1"
API_VERSION = "2024-11-06"
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_DURATION = re.compile(r"([0-9]{1,2})s\Z")


class ProviderError(BaseProviderError):
    """A local or Runway error with a stable code and category (app/providers/errors.py)."""


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    aspect_ratios: frozenset[str]
    min_duration: int
    max_duration: int
    resolution: str
    has_generated_audio: bool


VIDEO_MODELS: Mapping[str, ModelCapabilities] = MappingProxyType({
    "gen4.5": ModelCapabilities("gen4.5", frozenset({"16:9", "9:16"}), 2, 10, "720p", False),
})


@dataclass(frozen=True)
class VideoRequest:
    model_id: str
    prompt: str
    aspect_ratio: str = "16:9"
    duration: str = "8s"
    resolution: str = "720p"
    generate_audio: bool = False


@dataclass(frozen=True)
class Submission:
    model_id: str
    request_id: str
    status_url: str
    response_url: str


@dataclass(frozen=True)
class JobFailure:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class JobStatus:
    state: Literal["queued", "running", "completed", "failed"]
    queue_position: int | None = None
    error: JobFailure | None = None


@dataclass(frozen=True)
class VideoResult:
    video_url: str
    content_type: str | None = "video/mp4"


def validate_output_hosts(value: str | None) -> frozenset[str]:
    """Parse exact public DNS hosts configured for Runway's signed output URLs."""
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("invalid_config", "RUNWAY_OUTPUT_HOSTS must list exact media hostnames")
    hosts = []
    for part in value.split(","):
        host = part.strip().lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ProviderError("invalid_config", "Runway output hosts must be DNS names")
        labels = host.split(".")
        if (not host or len(host) > 253 or len(labels) < 2 or len(labels[-1]) < 2
                or not labels[-1].isalpha() or any(not _HOST_LABEL.fullmatch(label) for label in labels)
                or host.endswith((".local", ".internal", ".localhost"))):
            raise ProviderError("invalid_config", "Invalid RUNWAY_OUTPUT_HOSTS entry")
        hosts.append(host)
    return frozenset(hosts)


def validate_media_url(url: str) -> None:
    """Allow only an exact configured HTTPS host and an MP4 path, preserving signed query."""
    hosts = validate_output_hosts(os.environ.get("RUNWAY_OUTPUT_HOSTS"))
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid Runway media URL") from exc
    host = parsed.hostname
    if (not isinstance(url, str) or len(url) > 8192 or any(c in url for c in "\r\n\\")
            or parsed.scheme != "https" or not host or host not in hosts
            or parsed.netloc != host or not parsed.path.startswith("/")
            or not parsed.path.lower().endswith(".mp4") or parsed.fragment):
        raise ProviderError("unsafe_url", "Invalid Runway media URL")


def validate_video_request(request: VideoRequest) -> int:
    if not isinstance(request.model_id, str) or request.model_id not in VIDEO_MODELS:
        raise ProviderError("unsupported_model", "This Runway model is not enabled")
    model = VIDEO_MODELS[request.model_id]
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ProviderError("invalid_request", "Runway prompt must contain 1–1000 UTF-16 characters")
    try:
        prompt_length = len(request.prompt.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ProviderError("invalid_request", "Runway prompt has invalid Unicode") from exc
    if prompt_length > 1000:
        raise ProviderError("invalid_request", "Runway prompt must contain 1–1000 UTF-16 characters")
    if not isinstance(request.aspect_ratio, str) or request.aspect_ratio not in model.aspect_ratios:
        raise ProviderError("invalid_request", "Unsupported Runway aspect ratio")
    if not isinstance(request.duration, str) or not (match := _DURATION.fullmatch(request.duration)):
        raise ProviderError("invalid_request", "Runway duration must be whole seconds")
    duration = int(match.group(1))
    if not model.min_duration <= duration <= model.max_duration:
        raise ProviderError("invalid_request", "Unsupported Runway duration")
    if request.resolution != model.resolution:
        raise ProviderError("invalid_request", "Gen-4.5 text-to-video supports 720p only")
    if request.generate_audio is not False:
        raise ProviderError("invalid_request", "Gen-4.5 does not generate audio")
    return duration


def _task_url(task_id: str) -> str:
    try:
        parsed = uuid.UUID(task_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid Runway task ID") from exc
    if parsed.version != 4 or str(parsed) != task_id:
        raise ProviderError("unsafe_url", "Invalid Runway task ID")
    return f"{API_BASE}/tasks/{task_id}"


def _validate_submission(submission: Submission) -> None:
    if not isinstance(submission.model_id, str) or submission.model_id not in VIDEO_MODELS:
        raise ProviderError("invalid_request", "Invalid Runway model reference")
    expected = _task_url(submission.request_id)
    if submission.status_url != expected or submission.response_url != expected:
        raise ProviderError("unsafe_url", "Invalid Runway task URL")


class RunwayClient:
    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ProviderError("invalid_config", "Runway API secret is required")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderError("invalid_config", "HTTP timeout must be positive")
        self.api_key = api_key
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client()
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "RunwayClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request_json(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.http_client.request(
                method, url,
                headers={"Authorization": f"Bearer {self.api_key}", "X-Runway-Version": API_VERSION,
                         "Content-Type": "application/json"},
                json=payload, timeout=self.timeout_seconds, follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "Runway submission timed out; its outcome is unknown",
                                    category=TIMEOUT) from exc
            raise ProviderError("timeout", "Runway did not answer in time", retryable=True) from exc
        except httpx.RequestError as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "Runway submission outcome is unknown",
                                    category=NETWORK_ERROR) from exc
            raise ProviderError("network_error", "Could not contact Runway", retryable=True) from exc
        status = response.status_code
        if not 200 <= status < 300:
            code, retryable = status_error(status)
            category = None
            if method == "POST" and status >= 500:
                code, retryable, category = "submission_unknown", False, PROVIDER_UNAVAILABLE
            elif status == 429 and method == "POST":
                retryable = False
            raise ProviderError(code, f"Runway returned HTTP {status}", retryable=retryable,
                                http_status=status, category=category, provider_detail=response_detail(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_response", "Runway returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "Runway returned an invalid response")
        return data

    def submit(self, request: VideoRequest) -> Submission:
        duration = validate_video_request(request)
        ratio = {"16:9": "1280:720", "9:16": "720:1280"}[request.aspect_ratio]
        data = self._request_json("POST", f"{API_BASE}/text_to_video", payload={
            "model": request.model_id, "promptText": request.prompt,
            "ratio": ratio, "duration": duration,
        })
        task_id = data.get("id")
        task_url = _task_url(task_id)
        return Submission(request.model_id, task_id, task_url, task_url)

    def _poll(self, submission: Submission) -> dict[str, Any]:
        _validate_submission(submission)
        data = self._request_json("GET", submission.status_url)
        if data.get("id") != submission.request_id:
            raise ProviderError("invalid_response", "Runway returned a different task ID")
        return data

    def status(self, submission: Submission) -> JobStatus:
        data = self._poll(submission)
        state = data.get("status")
        if state in ("PENDING", "THROTTLED"):
            return JobStatus("queued")
        if state == "RUNNING":
            return JobStatus("running")
        if state == "SUCCEEDED":
            return JobStatus("completed")
        if state in ("FAILED", "CANCELLED"):
            failure_code = data.get("failureCode")
            code = failure_code[:100] if isinstance(failure_code, str) and failure_code else (
                "provider_canceled" if state == "CANCELLED" else "provider_failed")
            message = data.get("failure")
            if not isinstance(message, str) or not message:
                message = f"Runway task {state.lower()}"
            retryable = code in {"INTERNAL", "INPUT_PREPROCESSING.INTERNAL", "THIRD_PARTY.UNAVAILABLE"}
            return JobStatus("failed", error=JobFailure(code, message[:1000], retryable))
        raise ProviderError("invalid_response", "Unknown Runway task state")

    def result(self, submission: Submission) -> VideoResult:
        data = self._poll(submission)
        if data.get("status") != "SUCCEEDED":
            raise ProviderError("not_ready", "Runway video is not ready", retryable=True)
        output = data.get("output")
        if not isinstance(output, list) or len(output) != 1 or not isinstance(output[0], str):
            raise ProviderError("invalid_response", "Runway returned no single video URL")
        validate_media_url(output[0])
        return VideoResult(video_url=output[0])
