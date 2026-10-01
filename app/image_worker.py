"""Run image generation jobs outside request transactions and save private image assets.

Run ``python -m app.image_worker`` next to the API (``--once`` processes one due
job). Each job generates one image: one per scene, or each of an Image node's
images. The job lifecycle, credit settlement and step settlement are shared with
multi-scene video (app/media_jobs.py); this module only connects image providers.
"""
import argparse
from dataclasses import asdict
import os
from pathlib import Path
import time

from app import heartbeat
from app import media_jobs
from app.image_files import download_image, inspect_image
from app.providers.image import (ImageRequest, ImageSubmission, create_image_provider, image_provider_config_issue)
from app.providers.errors import ProviderError
from app.runtime_env import start_process


def image_job_max_age_seconds() -> int:
    """How long an image job may take before its credits are held for review (default 1 hour)."""
    try:
        seconds = int(os.environ.get("IMAGE_JOB_MAX_AGE_SECONDS", "").strip() or "3600")
    except ValueError as exc:
        raise RuntimeError("IMAGE_JOB_MAX_AGE_SECONDS must be an integer") from exc
    if not 60 <= seconds <= 86400:
        raise RuntimeError("IMAGE_JOB_MAX_AGE_SECONDS must be between 60 and 86400")
    return seconds


def _submit(client, payload) -> dict:
    return asdict(client.submit(ImageRequest(payload["model"], payload["prompt"], payload["aspect_ratio"],
                                             payload.get("quality") or "standard", seed=payload.get("seed"))))


def _inspect(path: Path, payload) -> tuple[str, str, dict]:
    info = inspect_image(path)
    return info.content_type, info.extension, {"width": info.width, "height": info.height}


IMAGE_KIND = media_jobs.MediaKind(
    name="image",
    output_key="image_assets",
    running_detail="Đang tạo ảnh.",
    completed_detail="Đã lưu ảnh vào kho media riêng.",
    attention_detail="Có ảnh cần đối soát với provider; credit của các ảnh đó đang được giữ.",
    failed_detail="Không phải ảnh nào cũng tạo được; đã hoàn credits cho các ảnh lỗi.",
    max_age_seconds=lambda payload: image_job_max_age_seconds(),
    config_issue=image_provider_config_issue,
    open_client=lambda payload: create_image_provider(payload["provider"]),
    submit=_submit,
    status=lambda client, payload, submission: client.status(ImageSubmission(**submission)),
    result_urls=lambda client, payload, submission: [image.url for image in
                                                     client.result(ImageSubmission(**submission)).images],
    validate_url=lambda client, payload, url: client.validate_media_url(url),
    download=lambda url, target: download_image(url, target),
    inspect=_inspect,
    errors=(ProviderError,),
)


def run_one(*, client=None, download=None, poll_seconds: int = 5, worker_id: str | None = None) -> bool:
    """Claim and advance one image job; returns False when none is due."""
    return media_jobs.run_one(IMAGE_KIND, client=client, download=download, poll_seconds=poll_seconds,
                              worker_id=worker_id)


def main():
    parser = argparse.ArgumentParser(description="Process ReelForge image generation jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    args = parser.parse_args()
    start_process("image_worker")
    while True:
        worked = run_one()
        heartbeat.beat("image_worker")
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
