"""Image generation providers, chosen by a workspace's AI tools (task ``image``); keys stay in the environment.

The first provider is Runway ``gen4_image``: it reuses the Runway video
adapter's authentication, task polling and output-host allowlist, uses the same
``RUNWAYML_API_SECRET`` already verified live for video, and returns a remote
task ID that reconciliation can show. Other providers can be added to
``IMAGE_PROVIDERS`` without touching the workflow code.
"""
from dataclasses import dataclass
import os

# Aliased: importing the image adapter below binds this package's own ``runway`` attribute.
from app.providers import runway as runway_video
from app.providers.image.base import (ASPECT_RATIOS, QUALITIES, GeneratedImage, ImageFailure,
                                      ImageGenerationProvider, ImageModel, ImageProviderError, ImageRequest,
                                      ImageResult, ImageStatus, ImageSubmission, validate_image_request)
from app.providers.image.runway import RunwayImageProvider

IMAGE_TASK = "image"


@dataclass(frozen=True)
class ImageProviderSpec:
    provider_type: type[ImageGenerationProvider]
    key_env: str

    @property
    def models(self):
        return self.provider_type.models


IMAGE_PROVIDERS = {
    "runway": ImageProviderSpec(RunwayImageProvider, "RUNWAYML_API_SECRET"),
}


def image_model(provider: str | None, model: str | None) -> ImageModel | None:
    spec = IMAGE_PROVIDERS.get(provider or "")
    return spec.models.get(model or "") if spec else None


def image_provider_config_issue(provider_name: str) -> tuple[str, str] | None:
    """Operator configuration problems, checked without any request."""
    spec = IMAGE_PROVIDERS.get(provider_name)
    if spec is None:
        return "unsupported_provider", "Provider ảnh này chưa được hỗ trợ."
    if not os.environ.get(spec.key_env, "").strip():
        return "missing_key", f"Server cần {spec.key_env}."
    if provider_name == "runway":
        try:
            runway_video.validate_output_hosts(os.environ.get("RUNWAY_OUTPUT_HOSTS"))
        except runway_video.ProviderError:
            return "invalid_config", "Server cần RUNWAY_OUTPUT_HOSTS gồm các hostname media Runway được phép tải."
    return None


def create_image_provider(provider_name: str, **kwargs) -> ImageGenerationProvider:
    spec = IMAGE_PROVIDERS.get(provider_name)
    if spec is None:
        raise ImageProviderError("unsupported_provider", f"Unsupported image provider {provider_name!r}")
    key = os.environ.get(spec.key_env, "")
    if not key.strip():
        raise ImageProviderError("missing_key", f"{spec.key_env} is not set")
    return spec.provider_type(key, **kwargs)


def image_credit_cost() -> int:
    """Credits held for, and charged by, one generated image (``IMAGE_CREDITS_PER_GENERATION``, default 2)."""
    try:
        amount = int(os.environ.get("IMAGE_CREDITS_PER_GENERATION", "2"))
    except ValueError as exc:
        raise RuntimeError("IMAGE_CREDITS_PER_GENERATION must be a positive integer") from exc
    if not 1 <= amount <= 100000:
        raise RuntimeError("IMAGE_CREDITS_PER_GENERATION must be between 1 and 100000")
    return amount


__all__ = ["ASPECT_RATIOS", "IMAGE_PROVIDERS", "IMAGE_TASK", "QUALITIES", "GeneratedImage", "ImageFailure",
           "ImageGenerationProvider", "ImageModel", "ImageProviderError", "ImageProviderSpec", "ImageRequest",
           "ImageResult", "ImageStatus", "ImageSubmission", "RunwayImageProvider", "create_image_provider",
           "image_credit_cost", "image_model", "image_provider_config_issue", "validate_image_request"]
