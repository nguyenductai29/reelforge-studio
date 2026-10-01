"""Liveness and readiness (Phase 24): ``GET /health/live`` and ``GET /health/ready``.

* **live**: the process answers. systemd and a load balancer restart it otherwise.
* **ready**: it can serve: the database answers, its schema is at the newest migration
  (``alembic_version`` equals the head of ``migrations/``), and the master key is usable
  (a valid key file, and a sample of the stored secrets decrypts with it). Paid providers,
  SMTP and payment gateways are never contacted: their trouble shows in Admin →
  Verification, not here.
* **warnings** (never a failure): ``setup_open`` while no account exists yet. The first
  administrator is then created on the server itself (``npm run create-admin``);
  ``deploy.sh`` prints the reminder.

``deploy.sh`` waits for ``ready`` after a restart. Neither endpoint is under ``/api/``, so
the Next.js proxy does not expose them; the API listens on 127.0.0.1.
"""
from functools import lru_cache

from sqlalchemy import inspect, select, text

from app import master_key


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db import ROOT

    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()


def undecryptable_secrets(connection, sample: int = 3) -> int:
    """How many of a few stored admin secrets fail to decrypt with the key in use (0 when there are none)."""
    from app import secret_box
    from app.models import SystemConfig

    if not secret_box.available() or not inspect(connection).has_table("system_config"):
        return 0
    rows = connection.execute(select(SystemConfig.key, SystemConfig.ciphertext)
                              .where(SystemConfig.ciphertext.is_not(None)).limit(sample)).all()
    unreadable = 0
    for name, ciphertext in rows:
        try:
            secret_box.decrypt_json(f"system-config:{name}", ciphertext)
        except secret_box.SecretBoxError:
            unreadable += 1
    return unreadable


def readiness(engine=None) -> dict:
    if engine is None:
        from app.db import engine
    checks: dict[str, dict] = {}
    warnings: list[str] = []
    current, unreadable = None, 0
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if not connection.execute(text("SELECT count(*) FROM users")).scalar():
                warnings.append("setup_open")
            unreadable = undecryptable_secrets(connection)
        checks["database"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        checks["database"] = {"status": "error", "error": type(exc).__name__}
    head = migration_head()
    if current is None:
        checks["migrations"] = {"status": "error", "current": None, "head": head}
    else:
        checks["migrations"] = {"status": "ok" if current == head else "behind", "current": current, "head": head}
    key = master_key.status()
    problem = key["problem"] or ("cannot_decrypt" if unreadable else None)
    checks["master_key"] = {"status": "ok" if not problem else "error", "source": key["source"], "problem": problem}
    ok = all(check["status"] == "ok" for check in checks.values())
    return {"status": "ok" if ok else "unavailable", "checks": checks, "warnings": warnings}
