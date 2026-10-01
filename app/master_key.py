"""The master encryption key: the one secret ReelForge keeps outside PostgreSQL (Phase 20).

Every secret the application stores — OAuth tokens, upload sessions, payment
gateway credentials, AI provider keys, OAuth app secrets — is encrypted with this
Fernet key or a key derived from it (``app/secret_box.py``). A database backup
alone therefore reveals nothing; the key file is backed up separately.

Where the key comes from, first match wins:

1. ``REELFORGE_MASTER_KEY_FILE``: an explicit file. If it is set, it must exist and
   hold a valid key; there is no fallback, so a typo cannot silently switch keys.
2. ``/etc/reelforge/master.key``: the production default.
3. ``instance/master.key``: the development default (``instance/`` is git-ignored).
4. ``REELFORGE_TOKEN_ENCRYPTION_KEY``: the legacy environment variable, so existing
   installations keep working until the key is moved into a file.

A key is never generated implicitly. ``python -m app.master_key init`` writes one
(chmod 600): it copies the legacy key when one is set, generates a new one only
when the database holds no encrypted data, and otherwise refuses.
``python -m app.master_key status`` reports where the key comes from, never the key.
``python -m app.master_key encrypted`` says whether the database already holds
encrypted data (``deploy/ensure-master-key.sh`` uses it before creating a key).
"""
import argparse
import os
from pathlib import Path
import stat
import sys

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
FILE_ENV = "REELFORGE_MASTER_KEY_FILE"
LEGACY_ENV = "REELFORGE_TOKEN_ENCRYPTION_KEY"
PRODUCTION_FILE = Path("/etc/reelforge/master.key")
DEVELOPMENT_FILE = ROOT / "instance" / "master.key"


def _valid(value: str) -> bool:
    try:
        Fernet(value.encode("ascii"))
    except (TypeError, ValueError, UnicodeError):
        return False
    return True


_cache: dict = {}


def _read(path: Path) -> str | None:
    """The file's content; re-read only when the file changes (every encryption and decryption asks)."""
    try:
        info = os.stat(path)
        stamp = (str(path), info.st_mtime_ns, info.st_size)
        if _cache.get("stamp") != stamp:
            _cache.update(stamp=stamp, value=Path(path).read_text(encoding="ascii").strip())
        return _cache["value"]
    except (OSError, UnicodeError):
        _cache.clear()
        return None


def _mode(path: str) -> int | None:
    """Permission bits on POSIX; None where they mean nothing (Windows development machines)."""
    if os.name != "posix":
        return None
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def resolve() -> dict:
    """``{source, path, key, problem}``. ``source`` is file, legacy_env or missing; ``key`` is "" unless usable."""
    explicit = os.environ.get(FILE_ENV, "").strip()
    candidates = [Path(explicit)] if explicit else [PRODUCTION_FILE, DEVELOPMENT_FILE]
    for path in candidates:
        if not path.is_file():
            if explicit:
                return {"source": "file", "path": str(path), "key": "", "problem": "missing"}
            continue
        value = _read(path)
        if value is None:
            return {"source": "file", "path": str(path), "key": "", "problem": "unreadable"}
        if not _valid(value):
            return {"source": "file", "path": str(path), "key": "", "problem": "invalid"}
        return {"source": "file", "path": str(path), "key": value, "problem": None}
    legacy = os.environ.get(LEGACY_ENV, "").strip()
    if legacy:
        if not _valid(legacy):
            return {"source": "legacy_env", "path": None, "key": "", "problem": "invalid"}
        return {"source": "legacy_env", "path": None, "key": legacy, "problem": None}
    return {"source": "missing", "path": None, "key": "", "problem": "missing"}


def load() -> str:
    """The master key, or "" when none is usable (callers report ``key_missing``)."""
    return resolve()["key"]


def status() -> dict:
    """What an admin may see: where the key comes from and what is wrong, never the key itself."""
    found = resolve()
    info = {"source": found["source"], "path": found["path"], "problem": found["problem"],
            "permissions_ok": None, "legacy_env_set": bool(os.environ.get(LEGACY_ENV, "").strip()),
            "legacy_env_matches": None}
    if found["source"] == "file" and found["path"] and found["key"]:
        mode = _mode(found["path"])
        # Readable or writable by anyone but the owner: chmod 600 is expected.
        info["permissions_ok"] = None if mode is None else mode & 0o077 == 0
    if found["source"] == "file" and found["key"] and info["legacy_env_set"]:
        info["legacy_env_matches"] = os.environ.get(LEGACY_ENV, "").strip() == found["key"]
    return info


def _write(path: Path, value: str) -> None:
    """Create the file (never over an existing one) readable by its owner only; a new directory gets 700."""
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700)
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(value + "\n")
    if os.name == "posix":
        os.chmod(path, 0o600)


def encrypted_data_exists() -> bool:
    """Whether the database already holds anything encrypted (then a new key would make it unreadable)."""
    from sqlalchemy import inspect, text

    from app.db import engine

    checks = (("system_config", "ciphertext"), ("payment_provider_configs", "config_ciphertext"),
              ("youtube_connections", "refresh_token_ciphertext"), ("channel_connections", "access_token_ciphertext"),
              ("publications", "upload_session_ciphertext"))
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table, column in checks:
            if not inspector.has_table(table):
                continue
            if column not in {item["name"] for item in inspector.get_columns(table)}:
                continue
            if connection.execute(text(f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL LIMIT 1")).first():
                return True
    return False


def init(path: Path, *, out=print) -> int:
    """Write the key file once. Never overwrites, never prints the key."""
    found = resolve()
    if found["source"] == "file" and found["key"]:
        out(f"A master key already exists at {found['path']}; nothing changed.")
        return 0
    if Path(path).exists():
        out(f"{path} already exists but holds no valid key; it is never overwritten. Fix or move it by hand.")
        return 1
    legacy = os.environ.get(LEGACY_ENV, "").strip()
    if legacy and _valid(legacy):
        _write(Path(path), legacy)
        out(f"Copied {LEGACY_ENV} into {path} (chmod 600). Keep it backed up; the variable can now be removed.")
        return 0
    try:
        found_encrypted = encrypted_data_exists()
    except Exception as exc:  # noqa: BLE001 - unknown means no new key
        out(f"Cannot check the database ({type(exc).__name__}); no key generated. Fix the database connection first.")
        return 2
    if found_encrypted:
        out("The database already holds encrypted data. Restore the original key file (or set "
            f"{LEGACY_ENV}) instead of generating a new key: a new key would make that data unreadable.")
        return 1
    _write(Path(path), Fernet.generate_key().decode("ascii"))
    out(f"Generated a new master key at {path} (chmod 600). Back it up separately from the database.")
    return 0


def report_encrypted(out=print) -> int:
    """Whether PostgreSQL already holds encrypted data: 0 none, 1 yes (a new key would lose it), 2 unknown."""
    try:
        found = encrypted_data_exists()
    except Exception as exc:  # noqa: BLE001 - the database could not be read
        out(f"error: cannot check the database ({type(exc).__name__}); treat it as holding encrypted data.")
        return 2
    out("encrypted data: yes" if found else "encrypted data: none")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ReelForge master encryption key")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init", help="write the key file (copies the legacy key when set)")
    create.add_argument("--path", type=Path, default=None,
                        help=f"default: ${FILE_ENV}, else {PRODUCTION_FILE} on Linux, else {DEVELOPMENT_FILE}")
    commands.add_parser("status", help="where the key comes from (never prints the key)")
    commands.add_parser("encrypted", help="whether the database already holds encrypted data "
                                          "(exit 0: none, 1: yes, 2: cannot tell)")
    args = parser.parse_args(argv)
    if args.command == "encrypted":
        return report_encrypted()
    from app.runtime_env import load_runtime_env

    load_runtime_env()  # a legacy key may still live in the runtime file
    if args.command == "status":
        return print_status()
    path = args.path or Path(os.environ.get(FILE_ENV, "").strip() or
                             (PRODUCTION_FILE if sys.platform.startswith("linux") else DEVELOPMENT_FILE))
    result = init(path)
    print_status()
    return result


def print_status(out=print) -> int:
    """Safe facts only; 0 when a usable key exists, 1 otherwise. Warnings do not fail."""
    info = status()
    for name in ("source", "path", "problem", "permissions_ok", "legacy_env_set", "legacy_env_matches"):
        out(f"{name}: {info[name]}")
    if info["problem"]:
        out("error: no usable master key. Restore the key file from backup, or run "
            "`python -m app.master_key init` on a new installation.")
    if info["permissions_ok"] is False:
        out(f"warning: {info['path']} is readable by other users; run chmod 600 on it.")
    if info["source"] == "legacy_env":
        out(f"warning: the key still comes from {LEGACY_ENV}; run `python -m app.master_key init` to move it "
            "into a file.")
    if info["legacy_env_matches"] is False:
        out(f"warning: {LEGACY_ENV} differs from the key file; secrets encrypted with it cannot be read.")
    if not info["problem"]:
        out("reminder: keep a copy of the key file off this server, separate from the database backups.")
    return 0 if info["problem"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
