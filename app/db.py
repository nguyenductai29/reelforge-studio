"""Minimal connection bootstrap shared by the API, the workers and Alembic.

The database URL is one of the two things ReelForge keeps outside PostgreSQL (the
other is the master key file, ``app/master_key.py``). It comes from
``instance/bootstrap.json`` (``{"database_url": "postgresql+psycopg://…"}``), or, when
that file has none, from ``REELFORGE_DATABASE_URL`` (for container-style
deployments). The file wins, so a stray variable can never redirect an installation
that has one.
"""
import json
import os
from pathlib import Path
import re
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "instance" / "bootstrap.json"
LEGACY_CONFIG_FILE = ROOT / "instance" / "config.json"
source_file = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
config = json.loads(source_file.read_text()) if source_file.exists() else {}


DATABASE_URL_ENV = "REELFORGE_DATABASE_URL"


def database_url() -> URL:
    raw = config.get("database_url") or os.environ.get(DATABASE_URL_ENV, "").strip()
    if not raw:
        raise RuntimeError("Create instance/bootstrap.json with a PostgreSQL database_url (or set "
                           f"{DATABASE_URL_ENV}) before migrating or starting ReelForge Studio")
    url = make_url(raw)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    # The supplied hosted-Postgres URL contains a compatibility switch for other
    # clients. libpq/psycopg rejects it; sslmode=require remains intact.
    compat = url.query.get("uselibpqcompat")
    if compat is not None:
        if compat != "true":
            raise ValueError("Only uselibpqcompat=true is supported")
        url = url.difference_update_query(["uselibpqcompat"])
    if url.drivername == "sqlite" and url.database and url.database.startswith("instance/"):
        url = url.set(database=str(ROOT / url.database))
    return url


# A development machine (or a test) can serve the frontend somewhere other than the installation's public
# origin, e.g. http://localhost:3000, possibly against a shared database. instance/bootstrap.json may then say so:
#   {"database_url": "…", "frontend_origin": "http://localhost:3000", "secure_cookies": false}
# These keys apply to the processes on this machine only. They never reach the database, where System
# Settings keep the public origin (https://reelforge.mul-service.com by default) for every other machine.
LOCAL_SETTINGS = ("frontend_origin", "secure_cookies")


def local_settings() -> dict:
    """This machine's frontend origin override, validated like the admin form; ``{}`` when there is none.

    Only ``instance/bootstrap.json`` can hold one. The legacy ``instance/config.json`` keeps its old meaning:
    its values are copied into System Settings once."""
    if source_file != CONFIG_FILE or not any(key in config for key in LOCAL_SETTINGS):
        return {}
    origin = config.get("frontend_origin")
    if not isinstance(origin, str) or not re.fullmatch(r"https?://[^/\s]+", origin.rstrip("/")):
        raise RuntimeError("instance/bootstrap.json: frontend_origin must be an origin such as "
                           "http://localhost:3000 (scheme and host only)")
    origin = origin.rstrip("/")
    secure = config.get("secure_cookies", origin.startswith("https://"))
    if not isinstance(secure, bool):
        raise RuntimeError("instance/bootstrap.json: secure_cookies must be true or false")
    if secure and not origin.startswith("https://"):
        raise RuntimeError("instance/bootstrap.json: secure_cookies require an https frontend_origin")
    return {"frontend_origin": origin, "secure_cookies": secure}


url = database_url()
engine = create_engine(url, connect_args={"check_same_thread": False} if url.drivername == "sqlite" else {}, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
