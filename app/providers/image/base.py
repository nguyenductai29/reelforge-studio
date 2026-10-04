"""Provider-neutral image generation: capabilities, requests, normalized results and errors.

Image APIs are asynchronous: a request is submitted, polled, and its result
names one or more image URLs. Workers persist only ``ImageSubmission`` (the
provider, model and remote task ID) and copy each image into ReelForge media
storage; a provider URL is never kept as an asset. Adapters translate one
vendor API; nothing outside ``app/providers/image/`` sees a vendor response.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from typing import Literal, Mapping

from app.providers.errors import ProviderError

ASPECT_RATIOS = ("1:1", "16:9", "9:16")
QUALITIES = ("standard", "high")


class ImageProviderError(ProviderError):
    """A local or provider error with a stable code and category (app/providers/errors.py)."""


@dataclass(frozen=True)
class ImageModel:
    """What one model accepts; the node settings and readiness check against it."""

    model_id: str
    aspect_ratios: tuple[str, ...]
    qualities: tuple[str, ...] = ("standard",)
    max_prompt_chars: int = 1000
    supports_seed: bool = False
    supports_negative_prompt: bool = False
    images_per_request: int = 1


@dataclass(frozen=True)
class ImageRequest:
    model: str
    prompt: str
    aspect_ratio: str = "1:1"
    quality: str = "standard"
    negative_prompt: str | None = None
    seed: int | None = None
    reference_image_url: str | None = None


@dataclass(frozen=True)
class ImageSubmission:
    """What a worker stores between polls: identifiers only, never a URL."""

    provider: str
    model: str
    request_id: str


@dataclass(frozen=True)
class ImageFailure:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ImageStatus:
    state: Literal["queued", "running", "completed", "failed"]
    error: ImageFailure | None = None


@dataclass(frozen=True)
class GeneratedImage:
    """One output file. The URL may be signed: download it, never store or log it."""

    url: str
    content_type: str | None = None


@dataclass(frozen=True)
class ImageResult:
    provider: str
    model: str
    remote_request_id: str | None
    images: tuple[GeneratedImage, ...]
    # Small, JSON-safe facts such as {"images": 1}; vendor responses stay inside the adapter.
    usage: Mapping[str, int] = field(default_factory=dict)


def validate_image_request(model: ImageModel, request: ImageRequest) -> ImageModel:
    """Raise ``invalid_request`` unless the model accepts the request; needs no key or network."""
    if request.model != model.model_id:
        raise ImageProviderError("unsupported_model", "Image model does not match the request")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ImageProviderError("invalid_request", "Image prompt is required")
    try:
        prompt_length = len(request.prompt.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ImageProviderError("invalid_request", "Image prompt has invalid Unicode") from exc
    if prompt_length > model.max_prompt_chars:
        raise ImageProviderError("invalid_request", f"Image prompt exceeds {model.max_prompt_chars} characters")
    if request.aspect_ratio not in model.aspect_ratios:
        raise ImageProviderError("invalid_request", "Unsupported aspect ratio for this image model")
    if request.quality not in model.qualities:
        raise ImageProviderError("invalid_request", "Unsupported quality for this image model")
    if request.seed is not None and (not model.supports_seed or isinstance(request.seed, bool)
                                     or not isinstance(request.seed, int) or not 0 <= request.seed <= 4294967295):
        raise ImageProviderError("invalid_request", "Unsupported seed for this image model")
    if request.negative_prompt and not model.supports_negative_prompt:
        raise ImageProviderError("invalid_request", "This image model does not accept a negative prompt")
    if request.reference_image_url:
        raise ImageProviderError("invalid_request", "Reference images are not supported yet")
    return model


class ImageGenerationProvider(ABC):
    """One vendor's image API. The key comes from the server environment and never leaves this object."""

    name: str = ""
    models: Mapping[str, ImageModel] = {}

    def capabilities(self, model: str) -> ImageModel:
        capability = self.models.get(model)
        if capability is None:
            raise ImageProviderError("unsupported_model", f"{self.name} image model is not supported")
        return capability

    def validate(self, request: ImageRequest) -> ImageModel:
        """Check a request against the model's capabilities before any network call."""
        return validate_image_request(self.capabilities(request.model), request)

    @abstractmethod
    def submit(self, request: ImageRequest) -> ImageSubmission:
        """Start one generation; raise with code ``submission_unknown`` when acceptance is unknown."""

    @abstractmethod
    def status(self, submission: ImageSubmission) -> ImageStatus:
        """The remote task's state."""

    @abstractmethod
    def result(self, submission: ImageSubmission) -> ImageResult:
        """The finished task's images, each URL checked with ``validate_media_url``."""

    @abstractmethod
    def validate_media_url(self, url: str) -> None:
        """Raise ``unsafe_url`` unless the URL points at the provider's allowed media host (SSRF guard)."""

    def close(self) -> None:
        """Release the HTTP client."""

    def __enter__(self) -> "ImageGenerationProvider":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def generate(self, request: ImageRequest, *, poll_seconds: float = 2.0, timeout_seconds: float = 300.0,
                 sleep=time.sleep, clock=time.monotonic) -> ImageResult:
        """Submit, poll and return the result in one call; for tools such as the smoke test, not the worker."""
        submission = self.submit(request)
        started = clock()
        while True:
            state = self.status(submission)
            if state.state == "completed":
                return self.result(submission)
            if state.state == "failed":
                error = state.error or ImageFailure("provider_failed", "Image generation failed")
                raise ImageProviderError(error.code, error.message, retryable=error.retryable)
            if clock() - started > timeout_seconds:
                raise ImageProviderError("timeout", f"Image task {submission.request_id} did not finish in time",
                                         retryable=True)
            sleep(poll_seconds)
