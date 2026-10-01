"""Runway Gen-4 Image (text-to-image) adapter.

Contract: https://docs.dev.runwayml.com/api/#tag/Start-generating/paths/~1v1~1text_to_image/post
Tasks:    https://docs.dev.runwayml.com/api/#tag/Task-management

It reuses the Runway video adapter's HTTP handling (authentication, API
version, error mapping, timeouts) and task IDs. Output URLs are signed and
ephemeral; like video, they must come from a host listed in
RUNWAY_OUTPUT_HOSTS, and are downloaded, never stored.
"""
import os
from urllib.parse import urlsplit

import httpx

from app.providers import runway
from app.providers.image.base import (GeneratedImage, ImageFailure, ImageGenerationProvider, ImageModel,
                                      ImageProviderError, ImageRequest, ImageResult, ImageStatus, ImageSubmission)
from app import system_config

# One image per task. "standard" is 720p and "high" 1080p, per Runway's ratio list for gen4_image.
_RATIOS = {
    "standard": {"1:1": "720:720", "16:9": "1280:720", "9:16": "720:1280"},
    "high": {"1:1": "1080:1080", "16:9": "1920:1080", "9:16": "1080:1920"},
}
IMAGE_MODELS = {
    "gen4_image": ImageModel("gen4_image", aspect_ratios=("1:1", "16:9", "9:16"), qualities=("standard", "high"),
                             max_prompt_chars=1000, supports_seed=True),
}
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_RETRYABLE_FAILURES = {"INTERNAL", "INPUT_PREPROCESSING.INTERNAL", "THIRD_PARTY.UNAVAILABLE"}


def _error(exc: runway.ProviderError) -> ImageProviderError:
    """The same error, as an image provider error, keeping code, category and detail."""
    return ImageProviderError(exc.code, str(exc), retryable=exc.retryable, http_status=exc.http_status,
                              category=exc.category, provider_detail=exc.provider_detail)


def validate_media_url(url: str) -> None:
    """HTTPS on an exact RUNWAY_OUTPUT_HOSTS host, with an image path; the signed query is kept."""
    try:
        hosts = runway.validate_output_hosts(system_config.env("RUNWAY_OUTPUT_HOSTS"))
        parsed = urlsplit(url)
    except runway.ProviderError as exc:
        raise _error(exc) from exc
    except (TypeError, ValueError) as exc:
        raise ImageProviderError("unsafe_url", "Invalid Runway image URL") from exc
    host = parsed.hostname
    if (not isinstance(url, str) or len(url) > 8192 or any(c in url for c in "\r\n\\")
            or parsed.scheme != "https" or not host or host not in hosts or parsed.netloc != host
            or not parsed.path.startswith("/") or not parsed.path.lower().endswith(_IMAGE_EXTENSIONS)
            or parsed.fragment):
        raise ImageProviderError("unsafe_url", "Invalid Runway image URL")


class RunwayImageProvider(ImageGenerationProvider):
    name = "runway"
    models = IMAGE_MODELS

    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0):
        try:
            self._client = runway.RunwayClient(api_key, http_client=http_client, timeout_seconds=timeout_seconds)
        except runway.ProviderError as exc:
            raise _error(exc) from exc

    @property
    def timeout_seconds(self) -> float:
        return self._client.timeout_seconds

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, payload=None) -> dict:
        try:
            return self._client._request_json(method, url, payload=payload)
        except runway.ProviderError as exc:
            raise _error(exc) from exc

    @staticmethod
    def _task_url(task_id) -> str:
        try:
            return runway._task_url(task_id)
        except runway.ProviderError as exc:
            raise _error(exc) from exc

    def submit(self, request: ImageRequest) -> ImageSubmission:
        self.validate(request)
        payload = {"model": request.model, "promptText": request.prompt,
                   "ratio": _RATIOS[request.quality][request.aspect_ratio]}
        if request.seed is not None:
            payload["seed"] = request.seed
        data = self._request("POST", f"{runway.API_BASE}/text_to_image", payload)
        task_id = data.get("id")
        self._task_url(task_id)
        return ImageSubmission(self.name, request.model, task_id)

    def _poll(self, submission: ImageSubmission) -> dict:
        if submission.provider != self.name or submission.model not in self.models:
            raise ImageProviderError("invalid_request", "Invalid Runway image task reference")
        data = self._request("GET", self._task_url(submission.request_id))
        if data.get("id") != submission.request_id:
            raise ImageProviderError("invalid_response", "Runway returned a different task ID")
        return data

    def status(self, submission: ImageSubmission) -> ImageStatus:
        data = self._poll(submission)
        state = data.get("status")
        if state in ("PENDING", "THROTTLED"):
            return ImageStatus("queued")
        if state == "RUNNING":
            return ImageStatus("running")
        if state == "SUCCEEDED":
            return ImageStatus("completed")
        if state in ("FAILED", "CANCELLED"):
            failure_code = data.get("failureCode")
            code = failure_code[:100] if isinstance(failure_code, str) and failure_code else (
                "provider_canceled" if state == "CANCELLED" else "provider_failed")
            message = data.get("failure")
            if not isinstance(message, str) or not message:
                message = f"Runway image task {state.lower()}"
            return ImageStatus("failed", ImageFailure(code, message[:1000], code in _RETRYABLE_FAILURES))
        raise ImageProviderError("invalid_response", "Unknown Runway task state")

    def result(self, submission: ImageSubmission) -> ImageResult:
        data = self._poll(submission)
        if data.get("status") != "SUCCEEDED":
            raise ImageProviderError("not_ready", "Runway image is not ready", retryable=True)
        output = data.get("output")
        if not isinstance(output, list) or not output or not all(isinstance(url, str) for url in output):
            raise ImageProviderError("invalid_response", "Runway returned no image URL")
        for url in output:
            validate_media_url(url)
        return ImageResult(self.name, submission.model, submission.request_id,
                           tuple(GeneratedImage(url) for url in output), usage={"images": len(output)})

    def validate_media_url(self, url: str) -> None:
        validate_media_url(url)
