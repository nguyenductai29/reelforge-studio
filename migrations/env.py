"""Online migrations using the same connection bootstrap as the application."""
from alembic import context
from app.db import engine
from app.models import Base
from app import auth_security  # noqa: F401 -- register security tables in metadata
from app.publishers import google_oauth  # noqa: F401 -- register encrypted connection tables
from app import publications  # noqa: F401 -- register publication records in metadata

config = context.config
target_metadata = Base.metadata

if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is disabled; run migrations against a database")

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
