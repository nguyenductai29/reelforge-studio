"""Checks the system admin runs from Admin → System settings (Phase 20).

**AI providers.** "Test connection" sends one authenticated request to a free
metadata endpoint (a model or account listing): it proves the key without
generating anything or spending credits. FAL and Runware have no such endpoint,
so only the local check runs for them. Responses are reduced to a status; the
body is never returned or logged.

**Storage root.** A new media root must be an absolute path to an existing,
writable directory that is not itself a link or junction (the storage code never
follows links). Files are never moved when the root changes: the admin is warned
and moves them, or runs a migration, explicitly.
"""
import os
from pathlib import Path
import uuid

import httpx

from app import system_config

TIMEOUT = httpx.Timeout(15.0, connect=10.0)
# provider → (method, URL, headers builder). All are read-only listings.
ENDPOINTS = {
    "openai": ("https://api.openai.com/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
    "anthropic": ("https://api.anthropic.com/v1/models?limit=1",
                  lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
               lambda key: {"x-goog-api-key": key}),
    "replicate": ("https://api.replicate.com/v1/account", lambda key: {"Authorization": f"Bearer {key}"}),
    "runway": ("https://api.dev.runwayml.com/v1/organization",
               lambda key: {"Authorization": f"Bearer {key}", "X-Runway-Version": "2024-11-06"}),
}
KEYS = {"openai": "ai.openai.api_key", "anthropic": "ai.anthropic.api_key", "gemini": "ai.gemini.api_key",
        "runway": "ai.runway.api_secret", "fal": "ai.fal.api_key", "runware": "ai.runware.api_key",
        "replicate": "ai.replicate.api_token"}


def http_client() -> httpx.Client:
    # A factory so tests answer with httpx.MockTransport; no test reaches a provider.
    return httpx.Client(follow_redirects=False)


def test_ai_provider(provider: str, *, client: httpx.Client | None = None) -> dict:
    """``{local, remote}``: whether a key is configured, then whether the provider accepts it."""
    if provider not in KEYS:
        raise ValueError("unknown provider")
    if system_config.source(KEYS[provider]) == "error":
        return {"provider": provider, "local": {"status": "error", "code": "cannot_decrypt"},
                "remote": {"status": "skipped"}}
    if not system_config.get(f"ai.{provider}.enabled"):
        return {"provider": provider, "local": {"status": "warning", "code": "disabled"},
                "remote": {"status": "skipped"}}
    key = system_config.get(KEYS[provider])
    if not key:
        return {"provider": provider, "local": {"status": "error", "code": "key_missing"},
                "remote": {"status": "skipped"}}
    local = {"status": "ok"}
    if provider == "runway":
        from app.providers.runway import validate_output_hosts

        try:
            validate_output_hosts(system_config.get("ai.runway.output_hosts"))
        except Exception:  # noqa: BLE001 - the message may name the hosts; only the code leaves
            local = {"status": "error", "code": "output_hosts_invalid"}
    if provider not in ENDPOINTS:
        return {"provider": provider, "local": local, "remote": {"status": "unsupported"}}
    url, headers = ENDPOINTS[provider]
    try:
        with (client or http_client()) as session:
            response = session.get(url, headers=headers(key), timeout=TIMEOUT)
    except httpx.RequestError:
        return {"provider": provider, "local": local, "remote": {"status": "error", "code": "unavailable"}}
    if response.status_code == 200:
        remote = {"status": "ok"}
    elif response.status_code in (401, 403):
        remote = {"status": "error", "code": "unauthorized"}
    elif response.status_code == 429:
        remote = {"status": "warning", "code": "rate_limited"}
    else:
        remote = {"status": "error", "code": "unavailable"}
    return {"provider": provider, "local": local, "remote": remote}


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def storage_root(raw: str) -> dict:
    """``{path, ok, problem}`` for a proposed media root; ``problem`` is a stable code."""
    value = (raw or "").strip()
    path = Path(value).expanduser() if value else None
    if path is None or not path.is_absolute():
        return {"path": value, "ok": False, "problem": "not_absolute"}
    if _is_link(path):
        return {"path": str(path), "ok": False, "problem": "is_link"}
    if not path.exists():
        return {"path": str(path), "ok": False, "problem": "missing"}
    if not path.is_dir():
        return {"path": str(path), "ok": False, "problem": "not_directory"}
    probe = path / f".reelforge-write-test-{uuid.uuid4().hex}"
    try:
        with open(probe, "xb") as handle:
            handle.write(b"ok")
        os.remove(probe)
    except OSError:
        return {"path": str(path), "ok": False, "problem": "not_writable"}
    return {"path": str(path), "ok": True, "problem": None}
