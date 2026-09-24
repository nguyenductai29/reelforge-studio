"""Runware REST adapter for allowlisted text-to-video models.

API contracts: https://runware.ai/docs/platform/authentication
               https://runware.ai/docs/platform/task-polling
               https://runware.ai/docs/models/bytedance-seedance-2-5
"""

from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit
import uuid

import httpx


API_URL = "https://api.runware.ai/v1"


class ProviderError(Exception):
    """A local or Runware error with a stable code for the worker."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    dimensions: Mapping[str, Mapping[str, tuple[int, int]]]
    min_duration: int
    max_duration: int


# The explicit dimensions and duration range come from the model API reference.
_SEEDANCE_DIMENSIONS = MappingProxyType({
    "480p": MappingProxyType({"16:9": (854, 480), "9:16": (480, 854)}),
    "720p": MappingProxyType({"16:9": (1280, 720), "9:16": (720, 1280)}),
    "1080p": MappingProxyType({"16:9": (1920, 1080), "9:16": (1080, 1920)}),
})
VIDEO_MODELS: Mapping[str, ModelCapabilities] = MappingProxyType({
    "bytedance:seedance@2.5": ModelCapabilities(
        model_id="bytedance:seedance@2.5",
        dimensions=_SEEDANCE_DIMENSIONS,
        min_duration=4,
        max_duration=30,
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
    status_url: str = API_URL
    response_url: str = API_URL


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


_DURATION = re.compile(r"([0-9]+)s\Z")
_TRANSIENT_CODES = frozenset({"timeoutProvider", "providerRateLimitExceeded"})


def validate_media_url(url: str) -> None:
    """Accept only documented Runware video media URLs for worker downloads."""
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid Runware media URL") from exc
    if (parsed.scheme != "https" or parsed.netloc != "vm.runware.ai"
            or not parsed.path.startswith("/video/") or not parsed.path.lower().endswith(".mp4")
            or parsed.query or parsed.fragment):
        raise ProviderError("unsafe_url", "Invalid Runware media URL")


def validate_video_request(request: VideoRequest) -> tuple[int, int, int]:
    if not isinstance(request.model_id, str) or request.model_id not in VIDEO_MODELS:
        raise ProviderError("unsupported_model", "This Runware model is not enabled")
    model = VIDEO_MODELS[request.model_id]
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ProviderError("invalid_request", "Prompt is required")
    if not isinstance(request.resolution, str) or request.resolution not in model.dimensions:
        raise ProviderError("invalid_request", "Unsupported resolution for model")
    dimensions = model.dimensions[request.resolution]
    if not isinstance(request.aspect_ratio, str) or request.aspect_ratio not in dimensions:
        raise ProviderError("invalid_request", "Unsupported aspect ratio for model")
    if not isinstance(request.duration, str) or not (match := _DURATION.fullmatch(request.duration)):
        raise ProviderError("invalid_request", "Duration must be whole seconds")
    duration = int(match.group(1))
    if not model.min_duration <= duration <= model.max_duration:
        raise ProviderError("invalid_request", "Unsupported duration for model")
    if not isinstance(request.generate_audio, bool):
        raise ProviderError("invalid_request", "generate_audio must be a boolean")
    width, height = dimensions[request.aspect_ratio]
    return width, height, duration


def _validate_submission(submission: Submission) -> None:
    try:
        task_uuid = uuid.UUID(submission.request_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderError("invalid_request", "Invalid Runware task UUID") from exc
    if (not isinstance(submission.model_id, str) or submission.model_id not in VIDEO_MODELS
            or task_uuid.version != 4 or str(task_uuid) != submission.request_id
            or submission.status_url != API_URL or submission.response_url != API_URL):
        raise ProviderError("invalid_request", "Invalid Runware submission reference")


class RunwareClient:
    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ProviderError("invalid_config", "Runware API key is required")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderError("invalid_config", "HTTP timeout must be positive")
        self.api_key = api_key
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client()
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "RunwareClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request_json(self, payload: list[dict[str, Any]], *, submitting: bool) -> dict[str, Any]:
        try:
            response = self.http_client.request(
                "POST", API_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload, timeout=self.timeout_seconds, follow_redirects=False,
            )
        except httpx.RequestError as exc:
            if submitting:
                raise ProviderError("submission_unknown", "Runware submission outcome is unknown") from exc
            raise ProviderError("transport_error", "Could not contact Runware", retryable=True) from exc

        status = response.status_code
        if not 200 <= status < 300:
            if submitting and status >= 500:
                code, retryable = "submission_unknown", False
            elif status in (400, 422):
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
                code, retryable = "provider_unavailable", True
            else:
                code, retryable = "provider_response", False
            raise ProviderError(code, f"Runware returned HTTP {status}", retryable=retryable, http_status=status)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("provider_response", "Runware returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("provider_response", "Runware returned an invalid response")
        return data

    @staticmethod
    def _find_response(data: dict[str, Any], request_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        results = data.get("data", [])
        errors = data.get("errors", [])
        if not isinstance(results, list) or not isinstance(errors, list):
            raise ProviderError("provider_response", "Runware returned an invalid response")
        matching_results = [entry for entry in results if isinstance(entry, dict)
                            and entry.get("taskUUID") == request_id and entry.get("taskType") == "videoInference"]
        matching_errors = [entry for entry in errors if isinstance(entry, dict)
                           and entry.get("taskUUID") == request_id]
        if len(matching_results) + len(matching_errors) != 1:
            raise ProviderError("provider_response", "Runware returned a missing or ambiguous task response")
        return (matching_results[0] if matching_results else None,
                matching_errors[0] if matching_errors else None)

    def submit(self, request: VideoRequest) -> Submission:
        width, height, duration = validate_video_request(request)
        request_id = str(uuid.uuid4())
        data = self._request_json([{
            "taskType": "videoInference", "taskUUID": request_id,
            "model": request.model_id, "positivePrompt": request.prompt,
            "width": width, "height": height, "duration": duration,
            "settings": {"audio": request.generate_audio},
            "deliveryMethod": "async", "outputFormat": "MP4",
        }], submitting=True)
        result, error = self._find_response(data, request_id)
        if error is not None:
            raise ProviderError("provider_failed", str(error.get("message", "Runware rejected task"))[:1000])
        if result is None or result.get("status") == "error":
            raise ProviderError("provider_response", "Runware did not acknowledge task")
        return Submission(model_id=request.model_id, request_id=request_id)

    def _poll(self, submission: Submission) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        _validate_submission(submission)
        data = self._request_json([{"taskType": "getResponse", "taskUUID": submission.request_id}], submitting=False)
        return self._find_response(data, submission.request_id)

    def status(self, submission: Submission) -> JobStatus:
        result, error = self._poll(submission)
        if error is not None:
            code = str(error.get("code") or "provider_failed")
            return JobStatus("failed", error=JobFailure(
                code=code, message=str(error.get("message") or "Runware task failed")[:1000],
                retryable=code in _TRANSIENT_CODES,
            ))
        if result is None:
            raise ProviderError("provider_response", "Runware returned no task status")
        state = result.get("status")
        if state == "processing":
            return JobStatus("running")
        if state == "success" or (state is None and isinstance(result.get("videoURL"), str)):
            return JobStatus("completed")
        if state == "error":
            return JobStatus("failed", error=JobFailure("provider_failed", "Runware task failed", False))
        raise ProviderError("provider_response", "Unknown Runware task state")

    def result(self, submission: Submission) -> VideoResult:
        result, error = self._poll(submission)
        if error is not None:
            raise ProviderError("provider_failed", str(error.get("message") or "Runware task failed")[:1000])
        if result is None or result.get("status") not in ("success", None):
            raise ProviderError("not_ready", "Runware video is not ready", retryable=True)
        video_url = result.get("videoURL")
        if not isinstance(video_url, str):
            raise ProviderError("provider_response", "Runware returned no video URL")
        validate_media_url(video_url)
        return VideoResult(video_url=video_url)
