"""Live provider smoke tests: one small, real, paid request per modality.

    python -m app.smoke_test text  --live [--provider openai] [--model gpt-4.1-mini] [--max-tokens 256]
    python -m app.smoke_test video --live [--provider runware] [--model …] [--aspect 16:9] [--timeout 900]
    python -m app.smoke_test image --live [--provider runway] [--model gen4_image] [--aspect 1:1] [--timeout 300]
    python -m app.smoke_test run-report <run_id>

``text``, ``video`` and ``image`` call the provider directly, with the cheapest request
the adapter accepts, and never touch the database or workspace media. They run
only with ``--live`` or ``REELFORGE_LIVE_TESTS=1`` set in the shell; the runtime
file cannot turn them on. Video and image files are saved under
``instance/smoke-tests/`` (git-ignored), never in workspace media.

``run-report`` makes no provider call: it reads a workflow run from the
database and checks that each queued job carries the settings frozen in the
run's snapshot (model, language, tone, platform, aspect ratio, duration,
prompt override).

Output never contains a key, and generated text is shown as a short preview.
See docs/LIVE_PROVIDER_SMOKE_TEST.md.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from app.logs import scrub
from app.image_files import download_image, inspect_image
from app.provider_check import (check_image, check_text, check_video, describe, safe_console, smoke_image_settings,
                                smoke_video_settings)
from app.providers.image import ImageRequest, create_image_provider
from app.providers.catalog import PROVIDER_ERRORS, VIDEO_PROVIDERS
from app.providers.errors import ProviderError
from app.providers.text import create_text_provider
from app.runtime_env import ROOT, load_runtime_env
from app.video_files import download_video, mp4_duration_seconds, valid_mp4

TEXT_PROMPT = "Write one short sentence explaining why consistent posting helps a social media channel."
TEXT_SYSTEM_PROMPT = "Answer in one plain sentence."
VIDEO_PROMPT = "A cinematic sunrise over a quiet mountain lake, slow camera movement."
IMAGE_PROMPT = "A small red paper boat on a calm blue lake, soft morning light."
OUTPUT_DIR = ROOT / "instance" / "smoke-tests"
PREVIEW_CHARS = 160
# What to try first for each error category.
HINTS = {
    "authentication_error": "Check the API key in .env.runtime (or the process environment) and that it belongs to this provider.",
    "billing_error": "The provider account has no credit or billing is not set up.",
    "rate_limited": "Wait a minute and retry once; check the account's rate limits and quota.",
    "invalid_request": "Check the model name and that this account can use it.",
    "content_rejected": "The provider's safety system declined the prompt; try the default prompt.",
    "provider_unavailable": "The provider is having problems; retry later.",
    "timeout": "The provider did not answer in time; retry once, then check its status page.",
    "network_error": "Could not reach the provider; check DNS, proxy and firewall.",
    "empty_output": "No text came back; raise --max-tokens (reasoning models spend tokens before answering).",
    "invalid_response": "The provider answered in an unexpected shape; the adapter may need an update.",
    "generation_failed": "The provider accepted the job and then failed it; retry with the default prompt.",
    "configuration_error": "Run python -m app.provider_check and fix what it reports.",
}


class LiveTestRefused(RuntimeError):
    pass


def live_intent(flag: bool) -> bool:
    return flag or os.environ.get("REELFORGE_LIVE_TESTS") == "1"


def _one_line(text: str, limit: int = PREVIEW_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _report_error(exc: Exception, out) -> None:
    if isinstance(exc, ProviderError):
        out(f"FAILED: {exc.category} (code {exc.code}, retryable {exc.retryable}"
            + (f", HTTP {exc.http_status}" if exc.http_status else "") + f"): {exc}")
        if exc.provider_detail:
            out(f"Provider said: {scrub(exc.provider_detail)}")
        out(f"Hint: {HINTS.get(exc.category, 'See docs/LIVE_PROVIDER_SMOKE_TEST.md.')}")
    else:
        out(f"FAILED: {type(exc).__name__}: {exc}")


def smoke_text(*, provider=None, model=None, max_tokens=256, provider_factory=create_text_provider, out=print) -> int:
    check = check_text(provider, model)
    for line in describe(check):
        out(line)
    if not check.ready:
        out("Not ready; nothing was sent.")
        return 2
    started = time.monotonic()
    try:
        client = provider_factory(check.provider)
        try:
            result = client.generate(model=check.model, prompt=TEXT_PROMPT, system_prompt=TEXT_SYSTEM_PROMPT,
                                     max_tokens=max_tokens)
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 - every failure is reported, none is retried
        out(f"Latency: {round((time.monotonic() - started) * 1000)} ms")
        _report_error(exc, out)
        return 1
    usage = result.usage
    out(f"Response model: {result.model}")
    out(f"Latency: {round((time.monotonic() - started) * 1000)} ms")
    out(f"Tokens: input {usage.input_tokens}, output {usage.output_tokens}, total {usage.total_tokens}")
    out(f"Finish reason: {result.raw_metadata.get('finish_reason')}")
    out(f"Preview: {_one_line(result.text)}")
    out("PASSED: text smoke test.")
    return 0


def smoke_video(*, provider=None, model=None, prompt=VIDEO_PROMPT, aspect="16:9", timeout_seconds=900,
                poll_seconds=10, output_dir: Path = OUTPUT_DIR, client_factory=None, download=download_video,
                sleep=time.sleep, out=print) -> int:
    check = check_video(provider, model)
    for line in describe(check):
        out(line)
    if not check.ready:
        out("Not ready; nothing was sent.")
        return 2
    spec = VIDEO_PROVIDERS[check.provider]
    settings = {**smoke_video_settings(check.provider, check.model), "aspect_ratio": aspect}
    request = spec.module.VideoRequest(model_id=check.model, prompt=prompt, **settings)
    started = time.monotonic()
    try:
        spec.module.validate_video_request(request)
        client = (client_factory or spec.client_type)(os.environ[spec.key_env])
    except PROVIDER_ERRORS as exc:
        _report_error(exc, out)
        return 2
    try:
        try:
            submission = client.submit(request)
        except Exception as exc:  # noqa: BLE001
            _report_error(exc, out)
            if getattr(exc, "code", None) == "submission_unknown":
                out("The provider may have accepted the job anyway; check its dashboard before retrying.")
            return 1
        job_id = getattr(submission, "request_id", None)
        out(f"Submitted: job {job_id}")
        last_state = None
        while True:
            try:
                state = client.status(submission)
            except Exception as exc:  # noqa: BLE001
                _report_error(exc, out)
                out(f"Job {job_id} may still be running at the provider.")
                return 1
            if state.state != last_state:
                out(f"State: {state.state} after {round(time.monotonic() - started)} s")
                last_state = state.state
            if state.state == "failed":
                code = state.error.code if state.error else "unknown"
                out(f"FAILED: the provider reported {code}")
                if state.error and state.error.message:
                    out(f"Provider said: {scrub(state.error.message[:200])}")
                return 1
            if state.state == "completed":
                break
            if time.monotonic() - started > timeout_seconds:
                out(f"FAILED: still {state.state} after {timeout_seconds} s; job {job_id} was not cancelled.")
                return 1
            sleep(poll_seconds)
        try:
            result = client.result(submission)
        except Exception as exc:  # noqa: BLE001
            _report_error(exc, out)
            return 1
    finally:
        client.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"{check.provider}-{stamp}.mp4"
    partial = target.with_suffix(".part")
    try:
        size = download(result.video_url, partial)
        if not valid_mp4(partial):
            raise ValueError("the file is not a complete MP4")
        partial.replace(target)
    except Exception as exc:  # noqa: BLE001
        partial.unlink(missing_ok=True)
        out(f"FAILED: download or validation: {type(exc).__name__}: {exc}")
        return 1
    duration = mp4_duration_seconds(target)
    out(f"Provider: {check.provider}")
    out(f"Model: {check.model}")
    out(f"Job ID: {job_id}")
    out(f"Requested: {settings['duration']}, {settings['resolution']}, {settings['aspect_ratio']}")
    out(f"Duration: {duration if duration is not None else 'unknown'} s")
    out(f"File size: {size} bytes")
    out(f"Elapsed: {round(time.monotonic() - started)} s")
    out(f"Saved: {target}")
    out("PASSED: video smoke test.")
    return 0


def smoke_image(*, provider=None, model=None, prompt=IMAGE_PROMPT, aspect=None, timeout_seconds=300,
                poll_seconds=3, output_dir: Path = OUTPUT_DIR, provider_factory=None, download=download_image,
                sleep=time.sleep, out=print) -> int:
    check = check_image(provider, model)
    for line in describe(check):
        out(line)
    if not check.ready:
        out("Not ready; nothing was sent.")
        return 2
    settings = smoke_image_settings(check.provider, check.model)
    request = ImageRequest(check.model, prompt, aspect or settings["aspect_ratio"], settings["quality"])
    started = time.monotonic()
    try:
        client = (provider_factory or create_image_provider)(check.provider)
    except ProviderError as exc:
        _report_error(exc, out)
        return 2
    try:
        try:
            result = client.generate(request, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds, sleep=sleep)
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none is retried
            _report_error(exc, out)
            return 1
        out(f"Task ID: {result.remote_request_id}")
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        saved = []
        for number, image in enumerate(result.images, 1):
            partial = output_dir / f"{check.provider}-{stamp}-{number}.part"
            try:
                client.validate_media_url(image.url)
                size = download(image.url, partial)
                info = inspect_image(partial)
                target = partial.with_suffix(f".{info.extension}")
                partial.replace(target)
            except Exception as exc:  # noqa: BLE001
                partial.unlink(missing_ok=True)
                out(f"FAILED: download or validation: {type(exc).__name__}: {exc}")
                return 1
            saved.append((target, size, info))
    finally:
        client.close()
    out(f"Provider: {check.provider}")
    out(f"Model: {check.model}")
    out(f"Requested: {request.aspect_ratio}, {request.quality} quality")
    for target, size, info in saved:
        out(f"Image: {info.width}x{info.height} {info.content_type}, {size} bytes, saved {target}")
    out(f"Latency: {round((time.monotonic() - started) * 1000)} ms")
    out("PASSED: image smoke test.")
    return 0


# Run report ------------------------------------------------------------------

def compare_settings(node_type: str, config, payload) -> list[str]:
    """Settings from a run snapshot that the queued request does not reflect; empty when consistent."""
    from app.workflow.nodes.text import PLATFORMS, SUMMARY_LENGTHS, TITLE_STYLES, TONES

    config = config if isinstance(config, dict) else {}
    problems = []

    def expect(label, wanted, actual):
        if wanted != actual:
            problems.append(f"{label}: setting {wanted!r}, request {actual!r}")

    if config.get("tool_id"):
        expect("tool_id", config["tool_id"], payload.get("tool_id"))
    if payload.get("kind") == "text.generate":
        language_key = "target_language" if node_type == "translate" else "language"
        if config.get(language_key) not in (None, "auto"):
            expect(language_key, config[language_key], payload.get("language"))
        for key in ("max_tokens", "temperature"):
            if config.get(key) is not None:
                expect(key, config[key], payload.get(key))
        prompt = payload.get("prompt") or ""
        phrases = {"tone": TONES, "platform": PLATFORMS}
        for key, words in phrases.items():
            phrase = words.get(config.get(key) or "")
            if phrase and phrase not in prompt:
                problems.append(f"{key}: {config[key]!r} is not in the prompt")
        if node_type == "summarize" and config.get("length") and SUMMARY_LENGTHS[config["length"]] not in prompt:
            problems.append(f"length: {config['length']!r} is not in the prompt")
        if node_type == "title" and config.get("style") and TITLE_STYLES[config["style"]] not in prompt:
            problems.append(f"style: {config['style']!r} is not in the prompt")
        if config.get("duration") and f"about {config['duration']} seconds" not in prompt:
            problems.append(f"duration: {config['duration']!r} is not in the prompt")
    elif payload.get("kind") in ("video.generate", "image.generate"):
        keys = ("aspect_ratio", "duration") if payload["kind"] == "video.generate" else ("aspect_ratio", "quality", "seed")
        for key in keys:
            if config.get(key) not in (None, "auto"):
                expect(key, config[key], payload.get(key))
        if isinstance(config.get("prompt"), str) and config["prompt"].strip():
            expect("prompt override", config["prompt"].strip(), payload.get("prompt"))
    return problems


def run_report(run_id: str, *, out=print) -> int:
    from sqlalchemy import select

    from app.db import Session
    from app.logs import payload_summary
    from app.models import WorkflowJob, WorkflowRun, WorkflowRunStep

    with Session() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            out(f"Run {run_id} not found.")
            return 2
        nodes = {node["id"]: node for node in json.loads(run.graph_snapshot)["nodes"]}
        steps = db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id)
                           .order_by(WorkflowRunStep.position)).all()
        # Image and multi-scene video steps have one job per image or scene.
        jobs_by_step: dict[str, list] = {}
        for job in db.scalars(select(WorkflowJob).where(WorkflowJob.run_id == run.id)
                              .order_by(WorkflowJob.created_at, WorkflowJob.id)):
            jobs_by_step.setdefault(job.step_id, []).append(job)
        out(f"Run {run.id}: {run.status} (workflow {run.workflow_id}, retry of {run.retry_of_id or '-'})")
        mismatches = 0
        for step in steps:
            node = nodes.get(step.node_id, {})
            out("")
            out(f"{step.node_id} [{step.node_type}]: {step.status}")
            output = json.loads(step.output) if step.output else {}
            if isinstance(output, dict) and output.get("error"):
                out(f"  error: {output['error']}")
            if node.get("config"):
                out(f"  settings: {json.dumps(node['config'], ensure_ascii=False)[:300]}")
            for job in jobs_by_step.get(step.id, []):
                scene = job.payload.get("scene_index")
                label = f" (scene {scene})" if isinstance(scene, int) else ""
                out(f"  job {job.id}{label}: {job.state}, attempts {job.attempt_count}")
                out(f"  request: {json.dumps(payload_summary(job.payload), ensure_ascii=False)}")
                problems = compare_settings(step.node_type, node.get("config"), job.payload)
                mismatches += len(problems)
                out("  settings -> request: " + ("consistent" if not problems else "MISMATCH"))
                for problem in problems:
                    out(f"    {problem}")
    out("")
    out("Every queued request matches its snapshot settings." if not mismatches else
        f"{mismatches} setting(s) did not reach the request.")
    return 0 if not mismatches else 1


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description="Live provider smoke tests (paid) and run reports")
    commands = parser.add_subparsers(dest="command", required=True)
    text = commands.add_parser("text", help="one small real text generation")
    text.add_argument("--provider")
    text.add_argument("--model")
    text.add_argument("--max-tokens", type=int, default=256)
    text.add_argument("--live", action="store_true", help="confirm that a paid request may be sent")
    video = commands.add_parser("video", help="one short real video generation")
    video.add_argument("--provider")
    video.add_argument("--model")
    video.add_argument("--prompt", default=VIDEO_PROMPT)
    video.add_argument("--aspect", choices=("16:9", "9:16"), default="16:9")
    video.add_argument("--timeout", type=int, default=900, help="seconds to wait for the provider (default 900)")
    video.add_argument("--poll", type=int, default=10, help="seconds between status checks (default 10)")
    video.add_argument("--output", type=Path, default=OUTPUT_DIR)
    video.add_argument("--live", action="store_true", help="confirm that a paid request may be sent")
    image = commands.add_parser("image", help="one small real image generation")
    image.add_argument("--provider")
    image.add_argument("--model")
    image.add_argument("--prompt", default=IMAGE_PROMPT)
    image.add_argument("--aspect", choices=("1:1", "16:9", "9:16"))
    image.add_argument("--timeout", type=int, default=300, help="seconds to wait for the provider (default 300)")
    image.add_argument("--poll", type=int, default=3, help="seconds between status checks (default 3)")
    image.add_argument("--output", type=Path, default=OUTPUT_DIR)
    image.add_argument("--live", action="store_true", help="confirm that a paid request may be sent")
    report = commands.add_parser("run-report", help="check a workflow run's requests against its snapshot")
    report.add_argument("run_id")
    args = parser.parse_args(argv)

    if args.command in ("text", "video", "image") and not live_intent(args.live):
        # Checked before the runtime file loads, so the file cannot turn live tests on.
        print("Refusing to call a paid provider API: pass --live or set REELFORGE_LIVE_TESTS=1 in this shell.")
        return 3
    env_file, _ = load_runtime_env()
    print(f"Runtime environment: {env_file or 'no runtime file; using this process environment only'}")
    if args.command == "text":
        return smoke_text(provider=args.provider, model=args.model, max_tokens=args.max_tokens)
    if args.command == "video":
        return smoke_video(provider=args.provider, model=args.model, prompt=args.prompt, aspect=args.aspect,
                           timeout_seconds=args.timeout, poll_seconds=args.poll, output_dir=args.output)
    if args.command == "image":
        return smoke_image(provider=args.provider, model=args.model, prompt=args.prompt, aspect=args.aspect,
                           timeout_seconds=args.timeout, poll_seconds=args.poll, output_dir=args.output)
    return run_report(args.run_id)


if __name__ == "__main__":
    sys.exit(main())
