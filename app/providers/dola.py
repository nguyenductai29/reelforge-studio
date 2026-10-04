"""HTTP adapter for the locally operated Dola render gateway.

The gateway owns its browser profiles. ReelForge only submits and polls jobs.
Set DOLA_BASE_URL and DOLA_MEDIA_BASE_URL to operator-controlled origins.
"""

from dataclasses import dataclass
import math
import os
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

import httpx

from app.providers.errors import (NETWORK_ERROR, PROVIDER_UNAVAILABLE, TIMEOUT,
                                 ProviderError as BaseProviderError, response_detail, status_error)


class ProviderError(BaseProviderError):
    """A local or Dola error with a stable code and category (app/providers/errors.py)."""


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    aspect_ratios: frozenset[str]
    durations: frozenset[str]


_RATIOS = frozenset({"9:16", "16:9", "1:1", "4:3", "3:4"})
_DURATIONS = frozenset({"10s", "15s", "30s"})
VIDEO_MODELS: Mapping[str, ModelCapabilities] = MappingProxyType({
    model: ModelCapabilities(model, _RATIOS, _DURATIONS)
    for model in ("seedance-2.0", "seedance-2.5")
})


@dataclass(frozen=True)
class VideoRequest:
    model_id: str
    prompt: str
    aspect_ratio: str = "9:16"
    duration: str = "10s"
    resolution: str = "auto"
    generate_audio: None = None


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
    content_type: str = "video/mp4"


_TASK_ID = re.compile(r"video_[a-f0-9]{32}\Z")
_VIDEO_PATH = re.compile(r"/videos/[A-Za-z0-9][A-Za-z0-9._-]{0,254}\.mp4\Z")


def _origin(value: str | None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProviderError("invalid_config", "Dola gateway URL is required")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port  # Force validation of malformed ports.
    except ValueError as exc:
        raise ProviderError("invalid_config", "Invalid Dola gateway URL") from exc
    if (
        not host or not parsed.netloc or parsed.path not in ("", "/")
        or parsed.query or parsed.fragment or parsed.username or parsed.password
        or parsed.scheme not in {"http", "https"}
        or (parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ProviderError("invalid_config", "Dola gateway URL must be an HTTPS origin or local HTTP origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_media_url(url: str, *, media_base_url: str | None = None) -> None:
    """Only a configured Dola origin's MP4 route may enter private storage."""
    base = _origin(media_base_url or os.environ.get("DOLA_MEDIA_BASE_URL") or os.environ.get("DOLA_BASE_URL"))
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ProviderError("unsafe_url", "Invalid Dola media URL") from exc
    if (
        not isinstance(url, str) or f"{parsed.scheme}://{parsed.netloc}" != base
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or not _VIDEO_PATH.fullmatch(parsed.path)
    ):
        raise ProviderError("unsafe_url", "Invalid Dola media URL")


def validate_video_request(request: VideoRequest) -> None:
    if not isinstance(request.model_id, str) or request.model_id not in VIDEO_MODELS:
        raise ProviderError("unsupported_model", "This Dola model is not enabled")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ProviderError("invalid_request", "Prompt is required")
    if not isinstance(request.aspect_ratio, str) or request.aspect_ratio not in _RATIOS:
        raise ProviderError("invalid_request", "Unsupported Dola aspect ratio")
    if not isinstance(request.duration, str) or request.duration not in _DURATIONS:
        raise ProviderError("invalid_request", "Unsupported Dola duration")
    if request.resolution != "auto":
        raise ProviderError("invalid_request", "Dola does not offer resolution control")
    if request.generate_audio is not None:
        raise ProviderError("invalid_request", "Dola does not offer audio control")


class DolaClient:
    def __init__(self, api_key: str, *, base_url: str | None = None, media_base_url: str | None = None,
                 http_client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ProviderError("invalid_config", "Dola API key is required")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProviderError("invalid_config", "HTTP timeout must be positive")
        self.base_url = _origin(base_url or os.environ.get("DOLA_BASE_URL"))
        self.media_base_url = _origin(media_base_url or os.environ.get("DOLA_MEDIA_BASE_URL") or self.base_url)
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client()

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "DolaClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request_json(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.http_client.request(
                method, url, headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload, timeout=self.timeout_seconds, follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "Dola submission timed out; its outcome is unknown",
                                    category=TIMEOUT) from exc
            raise ProviderError("timeout", "Dola did not answer in time", retryable=True) from exc
        except httpx.RequestError as exc:
            if method == "POST":
                raise ProviderError("submission_unknown", "Dola submission outcome is unknown",
                                    category=NETWORK_ERROR) from exc
            raise ProviderError("network_error", "Could not contact Dola", retryable=True) from exc
        status = response.status_code
        if not 200 <= status < 300:
            code, retryable = status_error(status)
            category = None
            if method == "POST" and status >= 500:
                code, retryable, category = "submission_unknown", False, PROVIDER_UNAVAILABLE
            elif status == 429 and method == "POST":
                retryable = False
            raise ProviderError(code, f"Dola returned HTTP {status}", retryable=retryable,
                                http_status=status, category=category, provider_detail=response_detail(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_response", "Dola returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "Dola returned an invalid response")
        return data

    def submit(self, request: VideoRequest) -> Submission:
        validate_video_request(request)
        data = self._request_json("POST", f"{self.base_url}/v1/videos/generations", payload={
            "model": request.model_id, "prompt": request.prompt,
            "ratio": request.aspect_ratio, "duration": int(request.duration[:-1]),
            "reference_images": [],
        })
        request_id = data.get("id")
        if (
            not isinstance(request_id, str) or not _TASK_ID.fullmatch(request_id)
            or data.get("status") != "queued" or data.get("model") != request.model_id
        ):
            raise ProviderError("invalid_response", "Dola did not acknowledge a valid task")
        url = f"{self.base_url}/v1/videos/{request_id}"
        return Submission(request.model_id, request_id, url, url)

    def _poll(self, submission: Submission) -> dict[str, Any]:
        if (
            not isinstance(submission.model_id, str) or submission.model_id not in VIDEO_MODELS
            or not isinstance(submission.request_id, str) or not _TASK_ID.fullmatch(submission.request_id)
        ):
            raise ProviderError("unsafe_url", "Invalid Dola task reference")
        expected = f"{self.base_url}/v1/videos/{submission.request_id}"
        if submission.status_url != expected or submission.response_url != expected:
            raise ProviderError("unsafe_url", "Invalid Dola task URL")
        data = self._request_json("GET", expected)
        if data.get("id") != submission.request_id or data.get("model") != submission.model_id:
            raise ProviderError("invalid_response", "Dola returned a different task")
        return data

    def status(self, submission: Submission) -> JobStatus:
        data = self._poll(submission)
        state = data.get("status")
        if state == "queued":
            return JobStatus("queued")
        if state == "processing":
            return JobStatus("running")
        if state == "completed":
            return JobStatus("completed")
        if state == "failed":
            return JobStatus("failed", error=JobFailure("provider_failed", "Dola generation failed", False))
        raise ProviderError("invalid_response", "Unknown Dola task state")

    def result(self, submission: Submission) -> VideoResult:
        data = self._poll(submission)
        if data.get("status") != "completed":
            raise ProviderError("not_ready", "Dola video is not ready", retryable=True)
        url = data.get("video_url")
        validate_media_url(url, media_base_url=self.media_base_url)
        return VideoResult(url)
