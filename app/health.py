"""Liveness and readiness (Phase 24): ``GET /health/live`` and ``GET /health/ready``.

* **live**: the process answers. systemd and a load balancer restart it otherwise.
* **ready**: it can serve: the database answers, its schema is at the newest migration
  (``alembic_version`` equals the head of ``migrations/``), and the master key is usable
  (stored secrets can be decrypted). Paid providers, SMTP and payment gateways are never
  contacted: their trouble shows in Admin → Verification, not here.

``deploy.sh`` waits for ``ready`` after a restart. Neither endpoint is under ``/api/``, so
the Next.js proxy does not expose them; the API listens on 127.0.0.1.
"""
from functools import lru_cache

from sqlalchemy import text

from app import master_key


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db import ROOT

    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()


def readiness(engine=None) -> dict:
    if engine is None:
        from app.db import engine
    checks: dict[str, dict] = {}
    current = None
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        checks["database"] = {"status": "error", "error": type(exc).__name__}
    head = migration_head()
    if current is None:
        checks["migrations"] = {"status": "error", "current": None, "head": head}
    else:
        checks["migrations"] = {"status": "ok" if current == head else "behind", "current": current, "head": head}
    key = master_key.status()
    checks["master_key"] = {"status": "ok" if not key["problem"] else "error", "source": key["source"],
                            "problem": key["problem"]}
    ok = all(check["status"] == "ok" for check in checks.values())
    return {"status": "ok" if ok else "unavailable", "checks": checks}
