"""Minimal connection bootstrap shared by the API and Alembic."""
import json
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "instance" / "bootstrap.json"
LEGACY_CONFIG_FILE = ROOT / "instance" / "config.json"
source_file = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
config = json.loads(source_file.read_text()) if source_file.exists() else {}


def database_url() -> URL:
    raw = config.get("database_url")
    if not raw:
        raise RuntimeError("Create instance/bootstrap.json with a PostgreSQL database_url before migrating or starting ReelForge Studio")
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


url = database_url()
engine = create_engine(url, connect_args={"check_same_thread": False} if url.drivername == "sqlite" else {}, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
