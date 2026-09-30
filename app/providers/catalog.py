"""Video provider catalog shared by the API, the video node handler and the worker."""
from dataclasses import dataclass
import os
from types import ModuleType

from app.providers import dola, fal, replicate, runware, runway


@dataclass(frozen=True)
class VideoProvider:
    module: ModuleType
    client_type: type
    key_env: str


VIDEO_PROVIDERS = {
    "fal": VideoProvider(fal, fal.FalQueueClient, "FAL_KEY"),
    "runware": VideoProvider(runware, runware.RunwareClient, "RUNWARE_API_KEY"),
    "replicate": VideoProvider(replicate, replicate.ReplicateClient, "REPLICATE_API_TOKEN"),
    "dola": VideoProvider(dola, dola.DolaClient, "DOLA_API_KEY"),
    "runway": VideoProvider(runway, runway.RunwayClient, "RUNWAYML_API_SECRET"),
}
PROVIDER_ERRORS = (dola.ProviderError, fal.ProviderError, runware.ProviderError,
                   replicate.ProviderError, runway.ProviderError)
# Square output is not offered by any supported model yet.
ORIENTATION_ASPECT = {"vertical": "9:16", "horizontal": "16:9"}


def video_provider_config_issue(provider_name: str) -> tuple[str, str] | None:
    """Check operator credentials and the experimental gateway opt-in."""
    if provider_name == "dola" and os.environ.get("DOLA_EXPERIMENTAL_ENABLED") != "1":
        return "experimental_disabled", "Dola là provider thử nghiệm; server chưa bật DOLA_EXPERIMENTAL_ENABLED=1."
    key_name = VIDEO_PROVIDERS[provider_name].key_env
    if not os.environ.get(key_name):
        return "missing_key", f"Server cần {key_name}."
    if provider_name == "runway":
        try:
            runway.validate_output_hosts(os.environ.get("RUNWAY_OUTPUT_HOSTS"))
        except runway.ProviderError:
            return "invalid_config", "Server cần RUNWAY_OUTPUT_HOSTS gồm các hostname media Runway được phép tải."
    if provider_name == "dola":
        if not os.environ.get("DOLA_BASE_URL"):
            return "missing_config", "Server cần DOLA_BASE_URL."
        try:
            dola_max_job_age_seconds()
            with dola.DolaClient(os.environ[key_name]):
                pass
        except dola.ProviderError:
            return "invalid_config", "Cấu hình URL hoặc khóa Dola chưa hợp lệ."
    return None


def video_request_defaults(provider_name: str | None) -> tuple[str, str, bool | None]:
    if provider_name == "dola":
        return "10s", "auto", None
    if provider_name == "runway":
        return "8s", "720p", False
    return "8s", "720p", True


def dola_max_job_age_seconds() -> int:
    try:
        max_age = int(os.environ.get("DOLA_MAX_JOB_AGE_SECONDS", "7200"))
    except ValueError as exc:
        raise dola.ProviderError("invalid_config", "Invalid Dola max job age") from exc
    if not 60 <= max_age <= 86400:
        raise dola.ProviderError("invalid_config", "Dola max job age must be 60 to 86400 seconds")
    return max_age


def video_credit_cost() -> int:
    try:
        amount = int(os.environ.get("VIDEO_CREDITS_PER_CLIP", "10"))
    except ValueError as exc:
        raise RuntimeError("VIDEO_CREDITS_PER_CLIP must be a positive integer") from exc
    if not 1 <= amount <= 100000:
        raise RuntimeError("VIDEO_CREDITS_PER_CLIP must be between 1 and 100000")
    return amount
