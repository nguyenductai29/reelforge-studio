"""The legacy runtime environment file, and process start-up for the API, workers and CLI tools.

Since Phase 20 the application is configured in Admin → System settings and read
from PostgreSQL (``app/system_config.py``); production needs only the database URL
and the master key file (``app/master_key.py``). This file remains an optional
fallback: a setting nobody saved in the admin UI still reads its environment
variable, so an installation configured before Phase 20 keeps working. Logging
(``REELFORGE_LOG_*``) and the live smoke-test choices stay environment-only.

Where the file is still used, every process loads the same one:

* development: ``.env.runtime`` in the repository root (git-ignored), used by
  ``python -m app.run …`` and by ``app.provider_check`` / ``app.smoke_test``;
* production: the same file as systemd ``EnvironmentFile=`` for every unit.

``REELFORGE_ENV_FILE`` names another file. The format is systemd's: ``KEY=value``
lines, ``#`` comments, optional matching quotes around the value. A variable
already set in the process wins over the file, so an explicit export or a
systemd ``Environment=`` line can override one value.

Nothing here runs on import: tests and library code never read the file.
"""
import hashlib
import logging
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / ".env.runtime"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

# Variables the providers and workers read, by purpose; the example file lists the same names.
TEXT_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
VIDEO_KEYS = ("FAL_KEY", "RUNWARE_API_KEY", "REPLICATE_API_TOKEN", "RUNWAYML_API_SECRET", "DOLA_API_KEY")
PROVIDER_KEYS = TEXT_KEYS + VIDEO_KEYS


class RuntimeEnvError(ValueError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from a file; a malformed line raises with its line number, never its value."""
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not _NAME.fullmatch(name):
            raise RuntimeEnvError(f"{path.name} line {number} is not KEY=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def env_file_path() -> Path | None:
    """The file to load: ``REELFORGE_ENV_FILE`` if set (it must exist), else ``.env.runtime`` if present."""
    named = os.environ.get("REELFORGE_ENV_FILE", "").strip()
    if named:
        path = Path(named)
        if not path.is_file():
            raise RuntimeEnvError(f"REELFORGE_ENV_FILE does not exist: {path}")
        return path
    return DEFAULT_FILE if DEFAULT_FILE.is_file() else None


def key_fingerprint(value: str) -> str:
    """8 hex characters of SHA-256: enough to tell two keys apart across processes, useless to recover one."""
    return hashlib.sha256(value.strip().encode()).hexdigest()[:8]


def provider_key_summary() -> dict[str, str | None]:
    """Each provider key variable: its fingerprint when set, else ``None``."""
    return {name: key_fingerprint(os.environ[name]) if os.environ.get(name, "").strip() else None
            for name in PROVIDER_KEYS}


def start_process(name: str) -> None:
    """Entry-point setup for the API, workers and tools.

    Loads the optional legacy runtime file, turns on database-backed settings
    (``system_config.activate``) and configures logging. Logs which file was loaded
    and which provider keys the environment sets (fingerprints only), so a process
    started with a different environment stands out in the logs.
    """
    from app import system_config
    from app.logs import configure_logging, log_event

    path, loaded = load_runtime_env()
    system_config.activate()
    configure_logging()
    log_event(logging.getLogger("app.runtime"), "process_started", process=name, pid=os.getpid(),
              env_file=str(path) if path else None, env_file_values=len(loaded),
              provider_keys=provider_key_summary())


def load_runtime_env(path: Path | None = None) -> tuple[Path | None, list[str]]:
    """Load the runtime file into ``os.environ`` without overriding set variables.

    Returns the file used (or ``None``) and the names it set. Empty values are
    skipped, so the example file's blank names change nothing.
    """
    path = path or env_file_path()
    if path is None:
        return None, []
    loaded = []
    for name, value in parse_env_file(path).items():
        if value and not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return path, loaded
