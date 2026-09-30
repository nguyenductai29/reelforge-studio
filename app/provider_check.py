"""Pre-flight check before the live smoke tests: is each selected provider ready? No request is made.

    python -m app.provider_check [--text openai] [--text-model gpt-4.1-mini]
                                 [--video runware] [--video-model bytedance:seedance@2.5]
                                 [--image runway] [--image-model gen4_image]
                                 [--voice gemini] [--voice-model gemini-2.5-flash-preview-tts]
                                 [--only text|video|image|voice]

Providers and models come from the options, else ``REELFORGE_SMOKE_TEXT_PROVIDER`` /
``REELFORGE_SMOKE_TEXT_MODEL`` and ``REELFORGE_SMOKE_VIDEO_PROVIDER`` / ``REELFORGE_SMOKE_VIDEO_MODEL``,
else the first provider whose key is set. Images use ``REELFORGE_SMOKE_IMAGE_PROVIDER`` /
``REELFORGE_SMOKE_IMAGE_MODEL`` and are checked with ``--only image``, ``--image`` or
that variable; voice likewise uses ``REELFORGE_SMOKE_VOICE_PROVIDER`` / ``REELFORGE_SMOKE_VOICE_MODEL``,
``--only voice`` or ``--voice``. The runtime file (``.env.runtime``) is
loaded first, as in the API and workers. Keys are never printed: a configured key
shows an 8-character fingerprint, which lets you compare processes.

Exit status: 0 when every checked modality is ready, 1 otherwise.
"""
import argparse
from dataclasses import dataclass, field
import os
import re
import sys

from app.providers.catalog import VIDEO_PROVIDERS, ORIENTATION_ASPECT, video_provider_config_issue
from app.providers.image import IMAGE_PROVIDERS, image_provider_config_issue
from app.providers.text import TEXT_PROVIDERS
from app.providers.voice import VOICE_PROVIDERS, voice_provider_config_issue
from app.runtime_env import key_fingerprint, load_runtime_env

# Small, inexpensive models for the text smoke test; any model name the provider accepts can be passed instead.
DEFAULT_TEXT_MODELS = {"openai": "gpt-4.1-mini", "anthropic": "claude-haiku-4-5-20251001",
                       "gemini": "gemini-2.5-flash"}
# The Models page presets plus the smoke-test defaults; other names are sent as they are ("accepted").
KNOWN_TEXT_MODELS = {"openai": {"gpt-4.1-mini"}, "anthropic": {"claude-opus-5-5", "claude-haiku-4-5-20251001"},
                     "gemini": {"gemini-2.5-flash"}}
# What to fix for each configuration problem video_provider_config_issue() reports.
_PROBLEMS = {
    ("runway", "invalid_config"): "RUNWAY_OUTPUT_HOSTS must list the Runway media hostnames results may come from",
    ("dola", "missing_config"): "DOLA_BASE_URL is missing",
    ("dola", "invalid_config"): "DOLA_BASE_URL, DOLA_API_KEY or DOLA_MAX_JOB_AGE_SECONDS is invalid",
    ("dola", "experimental_disabled"): "Dola is experimental and needs DOLA_EXPERIMENTAL_ENABLED=1",
}


@dataclass
class ProviderCheck:
    modality: str
    provider: str | None
    model: str | None = None
    key_env: str | None = None
    key_state: str = "missing"  # configured / missing / invalid
    fingerprint: str | None = None
    model_state: str | None = None  # recognized / accepted / unsupported
    issues: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.issues


def _key_state(name: str) -> tuple[str, str | None]:
    value = os.environ.get(name, "")
    if not value.strip():
        return "missing", None
    if value != value.strip() or any(character.isspace() for character in value.strip()):
        return "invalid", None
    return "configured", key_fingerprint(value)


def _pick(requested: str | None, env_name: str, providers) -> str | None:
    choice = (requested or os.environ.get(env_name, "")).strip().lower()
    if choice:
        return choice
    return next((name for name, spec in providers.items() if os.environ.get(spec.key_env, "").strip()), None)


def check_text(provider: str | None = None, model: str | None = None) -> ProviderCheck:
    name = _pick(provider, "REELFORGE_SMOKE_TEXT_PROVIDER", TEXT_PROVIDERS)
    check = ProviderCheck("text", name)
    if name is None:
        check.issues.append(f"no text provider selected and no key set ({', '.join(spec.key_env for spec in TEXT_PROVIDERS.values())})")
        return check
    spec = TEXT_PROVIDERS.get(name)
    if spec is None:
        check.issues.append(f"unsupported text provider {name!r} (supported: {', '.join(TEXT_PROVIDERS)})")
        return check
    check.key_env = spec.key_env
    check.key_state, check.fingerprint = _key_state(spec.key_env)
    if check.key_state != "configured":
        check.issues.append(f"{spec.key_env} is {check.key_state}")
    check.model = (model or os.environ.get("REELFORGE_SMOKE_TEXT_MODEL", "").strip() or DEFAULT_TEXT_MODELS[name])
    if not spec.provider_type.model_pattern.fullmatch(check.model):
        check.model_state = "unsupported"
        check.issues.append(f"model name {check.model!r} is not valid for {name}")
    else:
        # Text providers accept any model their API knows; unknown names are sent as they are.
        check.model_state = "recognized" if check.model in KNOWN_TEXT_MODELS[name] else "accepted"
    return check


def check_video(provider: str | None = None, model: str | None = None) -> ProviderCheck:
    name = _pick(provider, "REELFORGE_SMOKE_VIDEO_PROVIDER", VIDEO_PROVIDERS)
    check = ProviderCheck("video", name)
    if name is None:
        check.issues.append(f"no video provider selected and no key set ({', '.join(spec.key_env for spec in VIDEO_PROVIDERS.values())})")
        return check
    spec = VIDEO_PROVIDERS.get(name)
    if spec is None:
        check.issues.append(f"unsupported video provider {name!r} (supported: {', '.join(VIDEO_PROVIDERS)})")
        return check
    check.key_env = spec.key_env
    check.key_state, check.fingerprint = _key_state(spec.key_env)
    models = spec.module.VIDEO_MODELS
    check.model = model or os.environ.get("REELFORGE_SMOKE_VIDEO_MODEL", "").strip() or next(iter(models))
    check.model_state = "recognized" if check.model in models else "unsupported"
    if check.model_state == "unsupported":
        check.issues.append(f"model {check.model!r} is not supported by the {name} adapter (supported: {', '.join(models)})")
    if check.key_state == "invalid":
        check.issues.append(f"{spec.key_env} is invalid")
    elif issue := video_provider_config_issue(name):
        check.issues.append(f"{spec.key_env} is missing" if issue[0] == "missing_key" else
                            _PROBLEMS.get((name, issue[0]), f"{name}: {issue[0]}"))
    return check


def check_image(provider: str | None = None, model: str | None = None) -> ProviderCheck:
    name = _pick(provider, "REELFORGE_SMOKE_IMAGE_PROVIDER", IMAGE_PROVIDERS)
    check = ProviderCheck("image", name)
    if name is None:
        check.issues.append(f"no image provider selected and no key set ({', '.join(spec.key_env for spec in IMAGE_PROVIDERS.values())})")
        return check
    spec = IMAGE_PROVIDERS.get(name)
    if spec is None:
        check.issues.append(f"unsupported image provider {name!r} (supported: {', '.join(IMAGE_PROVIDERS)})")
        return check
    check.key_env = spec.key_env
    check.key_state, check.fingerprint = _key_state(spec.key_env)
    models = spec.models
    check.model = model or os.environ.get("REELFORGE_SMOKE_IMAGE_MODEL", "").strip() or next(iter(models))
    check.model_state = "recognized" if check.model in models else "unsupported"
    if check.model_state == "unsupported":
        check.issues.append(f"model {check.model!r} is not supported by the {name} image adapter (supported: {', '.join(models)})")
    if check.key_state == "invalid":
        check.issues.append(f"{spec.key_env} is invalid")
    elif issue := image_provider_config_issue(name):
        check.issues.append(f"{spec.key_env} is missing" if issue[0] == "missing_key" else
                            _PROBLEMS.get((name, issue[0]), f"{name}: {issue[0]}"))
    return check


def check_voice(provider: str | None = None, model: str | None = None) -> ProviderCheck:
    name = _pick(provider, "REELFORGE_SMOKE_VOICE_PROVIDER", VOICE_PROVIDERS)
    check = ProviderCheck("voice", name)
    if name is None:
        check.issues.append(f"no voice provider selected and no key set ({', '.join(spec.key_env for spec in VOICE_PROVIDERS.values())})")
        return check
    spec = VOICE_PROVIDERS.get(name)
    if spec is None:
        check.issues.append(f"unsupported voice provider {name!r} (supported: {', '.join(VOICE_PROVIDERS)})")
        return check
    check.key_env = spec.key_env
    check.key_state, check.fingerprint = _key_state(spec.key_env)
    check.model = model or os.environ.get("REELFORGE_SMOKE_VOICE_MODEL", "").strip() or next(iter(spec.models))
    check.model_state = "recognized" if check.model in spec.models else "unsupported"
    if check.model_state == "unsupported":
        check.issues.append(f"model {check.model!r} is not supported by the {name} voice adapter (supported: {', '.join(spec.models)})")
    if check.key_state == "invalid":
        check.issues.append(f"{spec.key_env} is invalid")
    elif voice_provider_config_issue(name):
        check.issues.append(f"{spec.key_env} is missing")
    return check


def smoke_image_settings(provider: str, model: str) -> dict:
    """The cheapest image request: square if the model allows it, lowest quality, one image."""
    capabilities = IMAGE_PROVIDERS[provider].models[model]
    aspect = "1:1" if "1:1" in capabilities.aspect_ratios else capabilities.aspect_ratios[0]
    return {"aspect_ratio": aspect, "quality": capabilities.qualities[0]}


def _numbers(text: str) -> int:
    match = re.search(r"\d+", text)
    value = int(match.group()) if match else 0
    return value * 1000 if text.lower().endswith("k") else value


def smoke_video_settings(provider: str, model: str) -> dict:
    """The cheapest request the adapter accepts: shortest duration, lowest resolution, no audio."""
    capabilities = VIDEO_PROVIDERS[provider].module.VIDEO_MODELS[model]
    if hasattr(capabilities, "durations"):
        duration = min(capabilities.durations, key=_numbers)
    else:
        duration = f"{capabilities.min_duration}s"
    if hasattr(capabilities, "resolutions"):
        resolution = min(capabilities.resolutions, key=_numbers)
    elif hasattr(capabilities, "dimensions"):
        resolution = min(capabilities.dimensions, key=_numbers)
    elif hasattr(capabilities, "resolution"):
        resolution = capabilities.resolution
    else:
        resolution = "auto"
    generate_audio = None if provider == "dola" else False
    return {"duration": duration, "resolution": resolution, "generate_audio": generate_audio,
            "aspect_ratio": ORIENTATION_ASPECT["horizontal"]}


def describe(check: ProviderCheck) -> list[str]:
    title = check.modality.capitalize()
    lines = [f"{title} provider: {check.provider or 'not selected'}"]
    if check.model:
        lines.append(f"Model: {check.model} ({check.model_state})")
    if check.key_env:
        state = check.key_state + (f" (fingerprint {check.fingerprint})" if check.fingerprint else "")
        lines.append(f"API key: {check.key_env} {state}")
    if check.modality == "video" and check.model_state == "recognized":
        settings = smoke_video_settings(check.provider, check.model)
        audio = "no audio" if settings["generate_audio"] is False else "provider default audio"
        lines.append(f"Smoke request: {settings['duration']}, {settings['resolution']}, "
                     f"{settings['aspect_ratio']}, {audio}")
    if check.modality == "image" and check.model_state == "recognized":
        settings = smoke_image_settings(check.provider, check.model)
        lines.append(f"Smoke request: one image, {settings['aspect_ratio']}, {settings['quality']} quality")
    if check.modality == "voice" and check.model_state == "recognized":
        lines.append(f"Smoke request: one short sentence, voice {VOICE_PROVIDERS[check.provider].default_voice}, WAV")
    lines += [f"Problem: {issue}" for issue in check.issues]
    return lines


def run_checks(*, text=None, text_model=None, video=None, video_model=None, image=None, image_model=None,
               voice=None, voice_model=None, only=None) -> list[ProviderCheck]:
    checks = []
    if only in (None, "text"):
        checks.append(check_text(text, text_model))
    if only in (None, "video"):
        checks.append(check_video(video, video_model))
    # Images are checked when asked for, so text/video-only setups stay "ready".
    if only == "image" or (only is None and (image or os.environ.get("REELFORGE_SMOKE_IMAGE_PROVIDER", "").strip())):
        checks.append(check_image(image, image_model))
    if only == "voice" or (only is None and (voice or os.environ.get("REELFORGE_SMOKE_VOICE_PROVIDER", "").strip())):
        checks.append(check_voice(voice, voice_model))
    return checks


def report(checks: list[ProviderCheck], env_file, out=print) -> bool:
    out(f"Runtime environment: {env_file or 'no runtime file; using this process environment only'}")
    for check in checks:
        out("")
        for line in describe(check):
            out(line)
    ready = all(check.ready for check in checks)
    out("")
    out("Ready for smoke test." if ready else "Not ready for smoke test.")
    return ready


def safe_console() -> None:
    """Never fail on a character the console cannot show (e.g. cp932 on Windows)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description="Check provider configuration without calling any provider")
    parser.add_argument("--text", help="text provider: openai, anthropic or gemini")
    parser.add_argument("--text-model")
    parser.add_argument("--video", help="video provider: fal, runware, replicate, runway or dola")
    parser.add_argument("--video-model")
    parser.add_argument("--image", help="image provider: runway")
    parser.add_argument("--image-model")
    parser.add_argument("--voice", help="voice provider: gemini")
    parser.add_argument("--voice-model")
    parser.add_argument("--only", choices=("text", "video", "image", "voice"))
    args = parser.parse_args(argv)
    env_file, _ = load_runtime_env()
    checks = run_checks(text=args.text, text_model=args.text_model, video=args.video, video_model=args.video_model,
                        image=args.image, image_model=args.image_model, voice=args.voice,
                        voice_model=args.voice_model, only=args.only)
    return 0 if report(checks, env_file) else 1


if __name__ == "__main__":
    sys.exit(main())
