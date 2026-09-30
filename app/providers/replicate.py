"""Replicate REST prediction adapter for an allowlisted text-to-video model.

Contracts: https://replicate.com/docs/reference/http
           https://replicate.com/google/veo-3.1-fast/readme
           https://replicate.com/google/veo-3.1-fast/examples
           https://replicate.com/docs/topics/predictions/output-files
"""

from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

import httpx

from app.providers.errors import (NETWORK_ERROR, PROVIDER_UNAVAILABLE, TIMEOUT,
                                 ProviderError as BaseProviderError, response_detail, status_error)


API_BASE = "https://api.replicate.com/v1"
_PREDICTION_ID = re.compile(r"[a-z0-9]{10,64}\Z")


class ProviderError(BaseProviderError):
    """A local or Replicate error with a stable code and category (app/providers/errors.py)."""


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    aspect_ratios: frozenset[str]
    durations: frozenset[str]
    resolutions: frozenset[str]


VIDEO_MODELS: Mapping[str, ModelCapabilities] = MappingProxyType({
    "google/veo-3.1-fast": ModelCapabilities(
        model_id="google/veo-3.1-fast",
        aspect_ratios=frozenset({"16:9", "9:16"}),
        durations=frozenset({"4s", "6s", "8s"}),
        resolutions=frozenset({"720p", "1080p"}),
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
    content_type: str | None = "video/mp4"


def validate_video_request(request: VideoRequest) -> None:
    if not isinstance(request.model_id, str) or request.model_id not in VIDEO_MODELS:
        raise ProviderError("unsupported_model", "This Replicate model is not enabled")
    model = VIDEO_MODELS[request.model_id]
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ProviderError("invalid_request", "Prompt is required")
    if not isinstance(request.aspect_ratio, str) or request.aspect_ratio not in model.aspect_ratios:
        raise ProviderError("invalid_request", "Unsupported aspect ratio for model")
    if not isinstance(request.duration, str) or request.duration not in model.durations:
        raise ProviderError("invalid_request", "Unsupported duration for model")
    if not isinstance(request.resolution, str) or request.resolution not in model.resolutions:
        raise ProviderError("invalid_request", "Unsupported resolution for model")
    if not isinstance(request.generate_audio, bool):
        raise ProviderError("invalid_request", "generate_audio must be a boolean")


def validate_media_url(url: str) -> None:
    """Accept only HTTPS MP4 output on Replicate's documented delivery hosts."""
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid Replicate media URL") from exc
    host = parsed.hostname
    if (parsed.scheme != "https" or not host or parsed.netloc != host
            or (host != "replicate.delivery" and not host.endswith(".replicate.delivery"))
            or not parsed.path.startswith("/") or not parsed.path.lower().endswith(".mp4")
            or parsed.query or parsed.fragment):
        raise ProviderError("unsafe_url", "Invalid Replicate media URL")


def _prediction_url(request_id: str) -> str:
    if not isinstance(request_id, str) or not _PREDICTION_ID.fullmatch(request_id):
        raise ProviderError("invalid_request", "Invalid Replicate prediction ID")
    return f"{API_BASE}/predictions/{request_id}"


def _validate_submission(submission: Submission) -> None:
    if not isinstance(submission.model_id, str) or submission.model_id not in VIDEO_MODELS:
        raise ProviderError("invalid_request", "Invalid Replicate model reference")
    expected_url = _prediction_url(submission.request_id)
    if submission.status_url != expected_url or submission.response_url != expected_url:
        raise ProviderError("unsafe_url", "Invalid Replicate prediction URL")


class ReplicateClient:
    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ProviderError("invalid_config", "Replicate API token is required")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderError("invalid_config", "HTTP timeout must be positive")
        self.api_key = api_key
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client()
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "ReplicateClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request_json(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.http_client.request(
                method, url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload, timeout=self.timeout_seconds, follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "Replicate submission timed out; its outcome is unknown",
                                    category=TIMEOUT) from exc
            raise ProviderError("timeout", "Replicate did not answer in time", retryable=True) from exc
        except httpx.RequestError as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "Replicate submission outcome is unknown",
                                    category=NETWORK_ERROR) from exc
            raise ProviderError("network_error", "Could not contact Replicate", retryable=True) from exc
        status = response.status_code
        if not 200 <= status < 300:
            code, retryable = status_error(status)
            category = None
            if method == "POST" and status >= 500:
                code, retryable, category = "submission_unknown", False, PROVIDER_UNAVAILABLE
            raise ProviderError(code, f"Replicate returned HTTP {status}", retryable=retryable,
                                http_status=status, category=category, provider_detail=response_detail(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_response", "Replicate returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "Replicate returned an invalid prediction")
        return data

    @staticmethod
    def _validate_prediction(data: dict[str, Any], submission: Submission) -> None:
        if data.get("id") != submission.request_id:
            raise ProviderError("invalid_response", "Replicate returned a different prediction ID")
        if "model" in data and data["model"] != submission.model_id:
            raise ProviderError("invalid_response", "Replicate returned a different model")

    def submit(self, request: VideoRequest) -> Submission:
        validate_video_request(request)
        data = self._request_json(
            "POST", f"{API_BASE}/models/{request.model_id}/predictions",
            payload={"input": {
                "prompt": request.prompt, "aspect_ratio": request.aspect_ratio,
                "duration": int(request.duration[:-1]), "resolution": request.resolution,
                "generate_audio": request.generate_audio,
            }},
        )
        request_id = data.get("id")
        expected_url = _prediction_url(request_id)
        urls = data.get("urls")
        if not isinstance(urls, dict) or urls.get("get") != expected_url:
            raise ProviderError("unsafe_url", "Replicate returned an invalid prediction URL")
        result = Submission(model_id=request.model_id, request_id=request_id,
                            status_url=expected_url, response_url=expected_url)
        self._validate_prediction(data, result)
        return result

    def _poll(self, submission: Submission) -> dict[str, Any]:
        _validate_submission(submission)
        data = self._request_json("GET", submission.status_url)
        self._validate_prediction(data, submission)
        return data

    def status(self, submission: Submission) -> JobStatus:
        data = self._poll(submission)
        state = data.get("status")
        if state == "starting":
            return JobStatus("queued")
        if state == "processing":
            return JobStatus("running")
        if state == "succeeded":
            return JobStatus("completed")
        if state in ("failed", "canceled", "aborted"):
            code = "provider_failed" if state == "failed" else "provider_canceled"
            return JobStatus("failed", error=JobFailure(
                code=code, message=str(data.get("error") or f"Prediction {state}")[:1000], retryable=False,
            ))
        raise ProviderError("invalid_response", "Unknown Replicate prediction state")

    def result(self, submission: Submission) -> VideoResult:
        data = self._poll(submission)
        if data.get("status") != "succeeded":
            raise ProviderError("not_ready", "Replicate video is not ready", retryable=True)
        if data.get("data_removed") is True:
            raise ProviderError("result_expired", "Replicate video has expired")
        output = data.get("output")
        if not isinstance(output, str):
            raise ProviderError("invalid_response", "Replicate returned no video URL")
        validate_media_url(output)
        return VideoResult(video_url=output)
