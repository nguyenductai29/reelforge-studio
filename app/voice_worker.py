"""Run text-to-speech jobs outside request transactions and save private narration assets.

Run ``python -m app.voice_worker`` next to the API (``--once`` processes one due
job). Each job reads one narration: a whole script, or one scene's text. The
provider answers with the audio itself, so a job is generate → validate → store;
the job lifecycle, credit settlement and step settlement are shared with images
and multi-scene video (app/media_jobs.py). This module only connects voice providers.
"""
import argparse
import os
from pathlib import Path
import time

from app import heartbeat
from app import media_jobs
from app.audio_files import inspect_audio
from app.providers.errors import ProviderError
from app.providers.voice import VoiceRequest, create_voice_provider, voice_provider_config_issue
from app.runtime_env import start_process


def voice_job_max_age_seconds() -> int:
    """How long a voice job may wait before its credits are held for review (default 30 minutes)."""
    try:
        seconds = int(os.environ.get("VOICE_JOB_MAX_AGE_SECONDS", "1800"))
    except ValueError as exc:
        raise RuntimeError("VOICE_JOB_MAX_AGE_SECONDS must be an integer") from exc
    if not 60 <= seconds <= 86400:
        raise RuntimeError("VOICE_JOB_MAX_AGE_SECONDS must be between 60 and 86400")
    return seconds


def _generate(client, payload) -> tuple[list[bytes], str | None]:
    result = client.generate(VoiceRequest(payload["model"], payload["prompt"], payload["voice"],
                                          payload.get("style") or "neutral", payload.get("format") or "wav"))
    return [result.audio], result.remote_request_id


def _inspect(path: Path, payload) -> tuple[str, str, dict]:
    info = inspect_audio(path)
    return info.content_type, info.extension, {"duration": info.duration}


VOICE_KIND = media_jobs.MediaKind(
    name="voice",
    output_key="audio_assets",
    running_detail="Đang tạo giọng đọc.",
    completed_detail="Đã lưu giọng đọc vào kho media riêng.",
    attention_detail="Có đoạn giọng đọc cần đối soát với provider; credit của các đoạn đó đang được giữ.",
    failed_detail="Không phải đoạn nào cũng tạo được giọng đọc; đã hoàn credits cho các đoạn lỗi.",
    max_age_seconds=lambda payload: voice_job_max_age_seconds(),
    config_issue=voice_provider_config_issue,
    open_client=lambda payload: create_voice_provider(payload["provider"]),
    inspect=_inspect,
    generate=_generate,
    errors=(ProviderError,),
)


def run_one(*, client=None, poll_seconds: int = 5, worker_id: str | None = None) -> bool:
    """Claim and advance one voice job; returns False when none is due."""
    return media_jobs.run_one(VOICE_KIND, client=client, poll_seconds=poll_seconds, worker_id=worker_id)


def main():
    parser = argparse.ArgumentParser(description="Process ReelForge voice (text-to-speech) jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    args = parser.parse_args()
    start_process("voice_worker")
    while True:
        worked = run_one()
        heartbeat.beat("voice_worker")
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
