"""Validate a database backup without touching production (Phase 25): ``deploy/restore-check.sh``.

``python -m app.restore_check --dump FILE`` reads the archive (``pg_restore --list``) and
checks it holds the tables that matter (users, workspaces, payment orders, the migration
version). Nothing is restored.

With ``--scratch-url`` it also rehearses a restore: into a **separate** PostgreSQL
database, whose name must contain ``restore``, ``rehearsal``, ``scratch`` or ``test`` and
must not be the production database (``instance/bootstrap.json``). The scratch database's
``public`` schema is emptied first. Then it counts users, workspaces, projects, payment
orders and assets, and compares the migration version with this code's head (a restore of
an older dump is followed by ``alembic upgrade head``).

With ``--master-key FILE`` (a *copy* of the backed-up key) it also decrypts every stored
secret of the restored data: admin settings, payment gateways, OAuth tokens and upload
sessions. It reports how many decrypt, never a value.

Exit status 0 when every check passes, 1 otherwise. The report is JSON on stdout.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app import backup

REQUIRED_TABLES = ("users", "workspaces", "payment_orders", "alembic_version")
SCRATCH_WORDS = ("restore", "rehearsal", "scratch", "test")
COUNTED = ("users", "workspaces", "projects", "payment_orders", "assets")


def archive_tables(dump: Path, *, pg_restore: str, url) -> tuple[int, set[str]]:
    result = backup._run([pg_restore, "--list", str(dump)], {}, url, timeout=600)
    entries = [line for line in result.stdout.splitlines() if line.strip() and not line.startswith(";")]
    tables = set()
    for line in entries:
        parts = line.split()
        if "TABLE" in parts and "DATA" in parts:
            # "123; 0 16390 TABLE DATA public users postgres"
            index = parts.index("DATA")
            if index + 2 < len(parts):
                tables.add(parts[index + 2])
    return len(entries), tables


def _normalized(url):
    url = make_url(url) if isinstance(url, str) else url
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


def scratch_problem(scratch, production) -> str | None:
    if not scratch.drivername.startswith("postgresql"):
        return "the scratch database must be PostgreSQL"
    name = (scratch.database or "").lower()
    if not any(word in name for word in SCRATCH_WORDS):
        return f"the scratch database name must contain one of: {', '.join(SCRATCH_WORDS)}"
    if production is not None and production.drivername.startswith("postgresql") and \
            (scratch.host or "", scratch.port or 5432, scratch.database) == \
            (production.host or "", production.port or 5432, production.database):
        return "the scratch database is the production database"
    return None


def restore(dump: Path, scratch, *, pg_restore: str) -> None:
    engine = create_engine(scratch)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    args, env = backup.libpq(scratch)
    backup._run([pg_restore, "--no-owner", "--no-privileges", "--exit-on-error", "--dbname", scratch.database, *args,
                 str(dump)], env, scratch)


def counts(scratch) -> dict:
    engine = create_engine(scratch)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            found = {name: int(connection.execute(text(f"SELECT count(*) FROM {name}")).scalar() or 0)
                     for name in COUNTED if name in tables}
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar() \
                if "alembic_version" in tables else None
        return {"counts": found, "migration": version}
    finally:
        engine.dispose()


def decrypt(scratch, key_file: Path) -> dict:
    """How many stored secrets of the restored data decrypt with this key (purpose-derived and raw Fernet)."""
    from cryptography.fernet import Fernet, InvalidToken

    from app import master_key, secret_box

    os.environ[master_key.FILE_ENV] = str(key_file)
    resolved = master_key.resolve()
    if resolved["problem"]:
        return {"ok": 0, "failed": 0, "error": f"key file {resolved['problem']}"}
    raw = Fernet(resolved["key"].encode("ascii"))
    checks = (("system_config", "key", "ciphertext", lambda key: f"system-config:{key}"),
              ("payment_provider_configs", "provider", "config_ciphertext", lambda key: f"payment-config:{key}"),
              ("youtube_connections", None, "refresh_token_ciphertext", None),
              ("youtube_connections", None, "access_token_ciphertext", None),
              ("channel_connections", None, "access_token_ciphertext", None),
              ("channel_connections", None, "refresh_token_ciphertext", None),
              ("publications", None, "upload_session_ciphertext", None))
    ok = failed = 0
    by_table: dict[str, dict] = {}
    engine = create_engine(scratch)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            for table, key_column, column, purpose in checks:
                if table not in tables:
                    continue
                columns = {item["name"] for item in inspect(connection).get_columns(table)}
                if column not in columns:
                    continue
                select_key = key_column if key_column else "NULL"
                rows = connection.execute(text(f"SELECT {select_key}, {column} FROM {table} "
                                               f"WHERE {column} IS NOT NULL")).all()
                stats = by_table.setdefault(f"{table}.{column}", {"ok": 0, "failed": 0})
                for key, ciphertext in rows:
                    try:
                        if purpose:
                            secret_box.decrypt_json(purpose(key), ciphertext)
                        else:
                            raw.decrypt(ciphertext.encode("ascii"))
                        stats["ok"] += 1
                        ok += 1
                    except (secret_box.SecretBoxError, InvalidToken, UnicodeError, ValueError):
                        stats["failed"] += 1
                        failed += 1
    finally:
        engine.dispose()
    return {"ok": ok, "failed": failed, "by_column": by_table, "error": None}


def check(dump: Path, *, scratch_url: str | None = None, key_file: Path | None = None,
          pg_restore: str = "pg_restore", production=None) -> dict:
    report: dict = {"dump": str(dump), "ok": True, "steps": {}}
    if not dump.is_file():
        return {**report, "ok": False, "error": "dump file not found"}
    placeholder = make_url("postgresql+psycopg://localhost/unused")
    try:
        entries, tables = archive_tables(dump, pg_restore=pg_restore, url=placeholder)
    except backup.BackupError as exc:
        return {**report, "ok": False, "error": f"archive unreadable: {exc}"}
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    report["steps"]["archive"] = {"entries": entries, "tables": len(tables), "missing": missing}
    report["ok"] = not missing
    if not scratch_url:
        return report
    scratch = _normalized(scratch_url)
    if production is None:
        try:
            from app.db import url as production
        except Exception:  # noqa: BLE001 - no bootstrap on this machine: nothing to protect
            production = None
    problem = scratch_problem(scratch, production)
    if problem:
        return {**report, "ok": False, "error": problem}
    try:
        restore(dump, scratch, pg_restore=pg_restore)
    except backup.BackupError as exc:
        return {**report, "ok": False, "error": f"restore failed: {exc}"}
    restored = counts(scratch)
    from app import health

    head = health.migration_head()
    restored["head"] = head
    restored["migrate_forward"] = restored["migration"] != head
    report["steps"]["restore"] = restored
    report["ok"] = report["ok"] and restored["migration"] is not None
    if key_file is not None:
        result = decrypt(scratch, key_file)
        report["steps"]["decrypt"] = result
        report["ok"] = report["ok"] and not result.get("error") and result["failed"] == 0
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a ReelForge database backup without touching production")
    parser.add_argument("--dump", required=True, help="A reelforge-*.dump file")
    parser.add_argument("--scratch-url", help="A separate PostgreSQL database to rehearse the restore in")
    parser.add_argument("--master-key", help="A copy of the backed-up master key file, to test decryption")
    parser.add_argument("--pg-restore", default="pg_restore")
    args = parser.parse_args(argv)
    report = check(Path(args.dump), scratch_url=args.scratch_url,
                   key_file=Path(args.master_key) if args.master_key else None, pg_restore=args.pg_restore)
    json.dump(report, sys.stdout, indent=2, default=str)
    print()
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
