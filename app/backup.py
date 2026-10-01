"""Database backups (Phase 25): ``python -m app.backup run`` (deploy/backup.sh, the reelforge-backup timer).

A run:

1. dumps PostgreSQL with ``pg_dump --format=custom`` into ``backups.directory``
   (Admin → System settings → Backups; default ``/srv/data/backups/reelforge``) as
   ``reelforge-YYYYMMDD-HHMMSS.dump``. The password goes to pg_dump through ``PGPASSWORD``
   only: never on a command line, in a log line or in the database. The directory is
   ``chmod 700`` and every file ``chmod 600``;
2. writes ``….dump.partial`` first, checks it with ``pg_restore --list``, then renames it:
   a crash never leaves a half file that looks complete;
3. records the run in ``backup_runs`` (Admin → Verification shows the last success, its
   age and the last failure; ``app/alerts.py`` warns when it is overdue or failed);
4. only after a successful dump, removes old dumps beyond the retention: the newest per
   day for ``keep_daily`` days (14), per ISO week for ``keep_weekly`` weeks (8), per month
   for ``keep_monthly`` months (6). The newest dump is never removed, and nothing is removed
   when the run fails.

The master key is **never** copied next to the dumps: a dump plus its key reveals every
secret. Back the key up separately (docs/BACKUP_RECOVERY.md).

On a development SQLite database the run copies the file with SQLite's backup API
(``reelforge-YYYYMMDD-HHMMSS.sqlite3``) so the whole path can be tried locally.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess

from sqlalchemy import select

FILE_PATTERN = re.compile(r"^reelforge-(\d{8})-(\d{6})\.(dump|sqlite3)$")
TIMEOUT_SECONDS = 6 * 3600


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class Retention:
    daily: int = 14
    weekly: int = 8
    monthly: int = 6


def _scrub(text: str, url) -> str:
    text = str(text)
    if getattr(url, "password", None):
        text = text.replace(str(url.password), "[redacted]")
    return text[:500]


def libpq(url) -> tuple[list[str], dict]:
    """Command-line connection arguments and environment for pg_dump/pg_restore; the password only in the env."""
    args = []
    if url.host:
        args += ["--host", url.host]
    if url.port:
        args += ["--port", str(url.port)]
    if url.username:
        args += ["--username", url.username]
    env = {"PGPASSWORD": str(url.password)} if url.password else {}
    sslmode = (url.query or {}).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = sslmode if isinstance(sslmode, str) else sslmode[0]
    env["PGCONNECT_TIMEOUT"] = "15"
    return args, env


def _run(command: list[str], env: dict, url, *, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise BackupError(f"{Path(command[0]).name} not found (install the PostgreSQL client tools)") from None
    except subprocess.TimeoutExpired:
        raise BackupError(f"{Path(command[0]).name} timed out") from None
    if result.returncode != 0:
        raise BackupError(_scrub((result.stderr or result.stdout or "failed").strip().splitlines()[-1], url))
    return result


def dump(url, target: Path, *, pg_dump: str = "pg_dump") -> None:
    if url.drivername.startswith("sqlite"):
        source = sqlite3.connect(url.database)
        try:
            copy = sqlite3.connect(target)
            with copy:
                source.backup(copy)
            copy.close()
        finally:
            source.close()
        return
    args, env = libpq(url)
    _run([pg_dump, "--format=custom", "--file", str(target), *args, url.database], env, url)


def verify(url, target: Path, *, pg_restore: str = "pg_restore") -> int:
    """How many entries the archive lists (pg_restore reads it without restoring anything)."""
    if url.drivername.startswith("sqlite"):
        connection = sqlite3.connect(target)
        try:
            return int(connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0])
        finally:
            connection.close()
    result = _run([pg_restore, "--list", str(target)], {}, url, timeout=600)
    entries = [line for line in result.stdout.splitlines() if line.strip() and not line.startswith(";")]
    if not entries:
        raise BackupError("the archive lists nothing")
    return len(entries)


def _stamp(name: str) -> datetime | None:
    match = FILE_PATTERN.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def backups(directory: Path) -> list[tuple[Path, datetime]]:
    found = [(path, _stamp(path.name)) for path in directory.iterdir() if path.is_file()] if directory.is_dir() else []
    return sorted(((path, stamp) for path, stamp in found if stamp), key=lambda item: item[1], reverse=True)


def keep_set(files: list[tuple[Path, datetime]], retention: Retention) -> set[Path]:
    """The dumps to keep: newest per day, ISO week and month within the retention; always the newest."""
    keep: set[Path] = set()
    if not files:
        return keep
    keep.add(files[0][0])
    for count, period in ((retention.daily, lambda d: d.strftime("%Y-%m-%d")),
                          (retention.weekly, lambda d: "%d-W%02d" % d.isocalendar()[:2]),
                          (retention.monthly, lambda d: d.strftime("%Y-%m"))):
        seen: list[str] = []
        for path, stamp in files:  # newest first: the first file of each period is its newest
            key = period(stamp)
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.append(key)
            keep.add(path)
    return keep


def prune(directory: Path, retention: Retention, *, dry_run: bool = False) -> list[str]:
    files = backups(directory)
    keep = keep_set(files, retention)
    removed = []
    for path, _ in files:
        if path not in keep:
            if not dry_run:
                path.unlink()
            removed.append(path.name)
    return removed


def settings() -> tuple[Path, Retention, int]:
    from app import system_config

    return (Path(str(system_config.get("backups.directory") or "/srv/data/backups/reelforge")),
            Retention(int(system_config.get("backups.keep_daily") or 14),
                      int(system_config.get("backups.keep_weekly") or 0),
                      int(system_config.get("backups.keep_monthly") or 0)),
            int(system_config.get("backups.max_age_hours") or 26))


def _record(session_factory, run_id: int | None = None, **values) -> int | None:
    from app.models import BackupRun

    try:
        with session_factory.begin() as db:
            if run_id is None:
                row = BackupRun(**values)
                db.add(row)
                db.flush()
                return row.id
            row = db.get(BackupRun, run_id)
            for key, value in values.items():
                setattr(row, key, value)
            return run_id
    except Exception:  # noqa: BLE001 - recording must not hide the dump's own outcome
        return run_id


def run(*, directory: Path | None = None, retention: Retention | None = None, url=None, session_factory=None,
        pg_dump: str = "pg_dump", pg_restore: str = "pg_restore", now: datetime | None = None) -> dict:
    """One backup. Returns {ok, file, bytes, entries, removed, error}; never raises for an ordinary failure."""
    if url is None:
        from app.db import url
    if session_factory is None:
        from app.db import Session as session_factory
    configured_dir, configured_retention, _ = settings()
    directory = Path(directory or configured_dir)
    retention = retention or configured_retention
    moment = now or datetime.now(timezone.utc)
    extension = "sqlite3" if url.drivername.startswith("sqlite") else "dump"
    name = f"reelforge-{moment:%Y%m%d-%H%M%S}.{extension}"
    run_id = _record(session_factory, kind="database", status="running", started_at=moment,
                     host=socket.gethostname()[:120])
    partial = directory / f"{name}.partial"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(directory, 0o700)
        dump(url, partial, pg_dump=pg_dump)
        if os.name == "posix":
            os.chmod(partial, 0o600)
        entries = verify(url, partial, pg_restore=pg_restore)
        final = directory / name
        os.replace(partial, final)
        size = final.stat().st_size
    except (BackupError, OSError, sqlite3.Error) as exc:
        partial.unlink(missing_ok=True)
        error = _scrub(exc, url)
        _record(session_factory, run_id, status="failed", finished_at=datetime.now(timezone.utc), error=error)
        return {"ok": False, "file": None, "bytes": 0, "entries": 0, "removed": [], "error": error}
    removed = prune(directory, retention)
    _record(session_factory, run_id, status="succeeded", finished_at=datetime.now(timezone.utc), filename=name,
            bytes=size)
    return {"ok": True, "file": str(final), "bytes": size, "entries": entries, "removed": removed, "error": None}


def status(db, now: datetime | None = None) -> dict:
    """The backup state for Admin → Verification and the alerts."""
    from app.models import BackupRun

    now = now or datetime.now(timezone.utc)

    def aware(value):
        return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)

    last_ok = db.scalars(select(BackupRun).where(BackupRun.status == "succeeded")
                         .order_by(BackupRun.finished_at.desc()).limit(1)).first()
    last_failed = db.scalars(select(BackupRun).where(BackupRun.status == "failed")
                             .order_by(BackupRun.started_at.desc()).limit(1)).first()
    recent = db.scalars(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(10)).all()
    directory, retention, max_age = settings()
    return {
        "directory": str(directory), "max_age_hours": max_age,
        "retention": {"daily": retention.daily, "weekly": retention.weekly, "monthly": retention.monthly},
        "last_success": {"at": aware(last_ok.finished_at).isoformat(), "file": last_ok.filename, "bytes": last_ok.bytes,
                         "age_hours": round((now - aware(last_ok.finished_at)).total_seconds() / 3600, 1)}
        if last_ok else None,
        "last_failure": {"at": aware(last_failed.started_at).isoformat(), "error": last_failed.error}
        if last_failed else None,
        "runs": [{"status": row.status, "started_at": aware(row.started_at).isoformat(),
                  "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
                  "file": row.filename, "bytes": row.bytes, "error": row.error} for row in recent],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ReelForge database backups (never prints a password)")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Dump the database now, then apply the retention")
    run_parser.add_argument("--dir", help="Backup directory (default: Admin → System settings → Backups)")
    run_parser.add_argument("--pg-dump", default="pg_dump")
    run_parser.add_argument("--pg-restore", default="pg_restore")
    prune_parser = commands.add_parser("prune", help="Apply the retention to the backup directory")
    prune_parser.add_argument("--dir")
    prune_parser.add_argument("--dry-run", action="store_true")
    commands.add_parser("status", help="The last success and failure (JSON)")
    args = parser.parse_args(argv)

    from app.runtime_env import start_process

    start_process("backup")
    if args.command == "run":
        result = run(directory=Path(args.dir) if args.dir else None, pg_dump=args.pg_dump, pg_restore=args.pg_restore)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.command == "prune":
        directory, retention, _ = settings()
        removed = prune(Path(args.dir) if args.dir else directory, retention, dry_run=args.dry_run)
        print(json.dumps({"removed": removed, "dry_run": args.dry_run}, indent=2))
        return 0
    from app.db import Session

    with Session() as db:
        print(json.dumps(status(db), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
