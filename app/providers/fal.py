"""fal queue adapter for supported text-to-video models."""

from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

import httpx


class ProviderError(Exception):
    """A local or fal error with a stable code for the worker."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    aspect_ratios: frozenset[str]
    durations: frozenset[str]
    resolutions: frozenset[str]


# fal model schema: https://fal.ai/models/fal-ai/veo3.1/fast/api
VIDEO_MODELS: Mapping[str, ModelCapabilities] = MappingProxyType({
    "fal-ai/veo3.1/fast": ModelCapabilities(
        model_id="fal-ai/veo3.1/fast",
        aspect_ratios=frozenset({"16:9", "9:16"}),
        durations=frozenset({"4s", "6s", "8s"}),
        resolutions=frozenset({"720p", "1080p", "4k"}),
    ),
})


@dataclass(frozen=True)
class VideoRequest:
    model_id: str
    prompt: str
    aspect_ratio: str = "16:9"
    duration: str = "8s"
    resolution: str = "720p"
    generate_audio: bool = True


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
    content_type: str | None = None


_TRANSIENT_ERROR_TYPES = frozenset({
    "request_timeout", "startup_timeout", "runner_scheduling_failure",
    "runner_connection_timeout", "runner_disconnected", "runner_connection_refused",
    "runner_connection_error", "runner_incomplete_response", "runner_server_error",
    "internal_error",
})

_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _validate_queue_url(url: str, expected_path: str) -> None:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid fal queue URL") from exc
    if (
        parsed.scheme != "https" or parsed.netloc != "queue.fal.run"
        or parsed.path != expected_path or parsed.query or parsed.fragment
    ):
        raise ProviderError("unsafe_url", "Invalid fal queue URL")


def _validate_submission(submission: Submission) -> None:
    if (
        not isinstance(submission.model_id, str) or submission.model_id not in VIDEO_MODELS
        or not isinstance(submission.request_id, str)
        or not _REQUEST_ID_PATTERN.fullmatch(submission.request_id)
    ):
        raise ProviderError("unsafe_url", "Invalid fal request reference")
    request_path = f"/{submission.model_id}/requests/{submission.request_id}"
    _validate_queue_url(submission.status_url, f"{request_path}/status")
    if submission.response_url not in (
        f"https://queue.fal.run{request_path}",
        f"https://queue.fal.run{request_path}/response",
    ):
        raise ProviderError("unsafe_url", "Invalid fal result URL")


def validate_media_url(url: str) -> None:
    """Reject media URLs that a private-storage worker must not download."""
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid fal media URL") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https" or not host
        or (host != "fal.media" and not host.endswith(".fal.media"))
        or parsed.netloc != host or not parsed.path.startswith("/files/")
        or parsed.query or parsed.fragment
    ):
        raise ProviderError("unsafe_url", "Invalid fal media URL")


def validate_video_request(request: VideoRequest) -> None:
    if not isinstance(request.model_id, str):
        raise ProviderError("unsupported_model", "This fal model is not enabled")
    capabilities = VIDEO_MODELS.get(request.model_id)
    if capabilities is None:
        raise ProviderError("unsupported_model", "This fal model is not enabled")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ProviderError("invalid_request", "Prompt is required")
    if not isinstance(request.aspect_ratio, str) or request.aspect_ratio not in capabilities.aspect_ratios:
        raise ProviderError("invalid_request", "Unsupported aspect ratio for model")
    if not isinstance(request.duration, str) or request.duration not in capabilities.durations:
        raise ProviderError("invalid_request", "Unsupported duration for model")
    if not isinstance(request.resolution, str) or request.resolution not in capabilities.resolutions:
        raise ProviderError("invalid_request", "Unsupported resolution for model")
    if not isinstance(request.generate_audio, bool):
        raise ProviderError("invalid_request", "generate_audio must be a boolean")


class FalQueueClient:
    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ProviderError("invalid_config", "fal API key is required")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderError("invalid_config", "HTTP timeout must be positive")
        self.api_key = api_key
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client()
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "FalQueueClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request_json(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.http_client.request(
                method,
                url,
                headers={"Authorization": f"Key {self.api_key}"},
                json=payload,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "fal submission outcome is unknown") from exc
            raise ProviderError("transport_error", "Could not contact fal", retryable=True) from exc

        status = response.status_code
        if not 200 <= status < 300:
            if status in (400, 422):
                code, retryable = "invalid_request", False
            elif status in (401, 403):
                code, retryable = "auth_error", False
            elif status == 402:
                code, retryable = "billing_error", False
            elif status == 404:
                code, retryable = "not_found", False
            elif status == 429:
                code, retryable = "rate_limited", True
            elif status >= 500:
                code, retryable = "provider_unavailable", method != "POST"
            else:
                code, retryable = "provider_response", False
            raise ProviderError(code, f"fal returned HTTP {status}", retryable=retryable, http_status=status)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("provider_response", "fal returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("provider_response", "fal returned an invalid response")
        return data

    def submit(self, request: VideoRequest) -> Submission:
        validate_video_request(request)
        data = self._request_json("POST",
            f"https://queue.fal.run/{request.model_id}",
            payload={
                "prompt": request.prompt,
                "aspect_ratio": request.aspect_ratio,
                "duration": request.duration,
                "resolution": request.resolution,
                "generate_audio": request.generate_audio,
            },
        )
        if not all(isinstance(data.get(key), str) for key in ("request_id", "status_url", "response_url")):
            raise ProviderError("provider_response", "fal returned an incomplete submission")
        submission = Submission(
            model_id=request.model_id,
            request_id=data["request_id"],
            status_url=data["status_url"],
            response_url=data["response_url"],
        )
        _validate_submission(submission)
        return submission

    def status(self, submission: Submission) -> JobStatus:
        _validate_submission(submission)
        data = self._request_json("GET", submission.status_url)
        if "request_id" in data and data["request_id"] != submission.request_id:
            raise ProviderError("provider_response", "fal returned a different request ID")
        raw_status = data.get("status")
        if raw_status == "IN_QUEUE":
            return JobStatus("queued", queue_position=data.get("queue_position"))
        if raw_status == "IN_PROGRESS":
            return JobStatus("running")
        if raw_status == "COMPLETED":
            if data.get("error"):
                code = data.get("error_type") or "provider_failed"
                return JobStatus("failed", error=JobFailure(
                    code=code,
                    message=str(data["error"])[:1000],
                    retryable=code in _TRANSIENT_ERROR_TYPES,
                ))
            return JobStatus("completed")
        raise ProviderError("provider_response", "Unknown fal queue state")

    def result(self, submission: Submission) -> VideoResult:
        _validate_submission(submission)
        data = self._request_json("GET", submission.response_url)
        video = data.get("video")
        if not isinstance(video, dict) or not isinstance(video.get("url"), str):
            raise ProviderError("provider_response", "fal returned no video URL")
        validate_media_url(video["url"])
        return VideoResult(video_url=video["url"], content_type=video.get("content_type"))
