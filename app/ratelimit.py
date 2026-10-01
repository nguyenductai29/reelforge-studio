"""Durable rate limits (Phase 24): fixed-window counters in the database, shared by every API process.

``hit(scope, subject)`` counts one attempt and answers whether it is within the scope's
limit. Counters live in ``rate_limit_buckets``: one row per scope and subject (a client
address, an email, a user or workspace id), stored only as a SHA-256 digest. Each call
runs in its own short transaction, so an attempt counts even when the request then fails.

The upsert is atomic on PostgreSQL and SQLite (``INSERT … ON CONFLICT DO UPDATE …
RETURNING``): concurrent requests never lose a count, and a window that ended restarts
in the same statement. Expired rows are pruned in small batches.

Login keeps its own failure throttle as well (``app/auth_security.py``: five failures
block an email and address pair for 15 minutes); these limits add per-address and
per-account ceilings on every sensitive endpoint.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import random

from sqlalchemy import case, delete, inspect, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import RateLimitBucket

# scope: (attempts, window in seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "login_ip": (30, 900),
    "login_account": (10, 900),
    "setup_ip": (10, 3600),
    "register_ip": (10, 3600),
    "forgot_ip": (10, 3600),
    "forgot_account": (3, 3600),
    "reset_ip": (20, 3600),
    "verify_ip": (30, 3600),
    "verify_resend": (3, 3600),
    "two_factor_ip": (30, 900),
    "two_factor_account": (10, 900),
    "account_change": (10, 900),
    "support_ticket": (10, 3600),
    "support_message": (60, 3600),
    "checkout_user": (20, 3600),
    "checkout_workspace": (10, 3600),
    "invite_workspace": (30, 86400),
    "invite_lookup_ip": (60, 3600),
    "email_test": (10, 3600),
    "account_export": (10, 3600),
}
PRUNE_PROBABILITY = 0.05
PRUNE_BATCH = 200


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int):
        super().__init__(f"rate limited: {scope}")
        self.scope = scope
        self.retry_after = max(1, int(retry_after))


def _key(scope: str, subject: str) -> str:
    return hashlib.sha256(f"{scope}|{str(subject).strip().lower()}".encode("utf-8")).hexdigest()


def _insert(db):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return postgres_insert
    if dialect == "sqlite":
        return sqlite_insert
    raise RuntimeError(f"Unsupported rate limit database: {dialect}")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def count(db, scope: str, subject: str, *, window: int, now: datetime) -> tuple[int, datetime]:
    """Add one attempt in the caller's transaction; returns (attempts in this window, window end)."""
    table = RateLimitBucket.__table__
    ends = now + timedelta(seconds=window)
    expired = table.c.expires_at <= now
    statement = _insert(db)(RateLimitBucket).values(
        key_hash=_key(scope, subject), scope=scope[:32], window_started_at=now, count=1, expires_at=ends)
    statement = statement.on_conflict_do_update(index_elements=["key_hash"], set_={
        "count": case((expired, 1), else_=table.c.count + 1),
        "window_started_at": case((expired, now), else_=table.c.window_started_at),
        "expires_at": case((expired, ends), else_=table.c.expires_at),
    }).returning(table.c.count, table.c.expires_at)
    attempts, expires_at = db.execute(statement).one()
    return int(attempts), _aware(expires_at)


def prune(db, now: datetime) -> int:
    keys = list(db.scalars(select(RateLimitBucket.key_hash).where(RateLimitBucket.expires_at < now)
                           .limit(PRUNE_BATCH)))
    if not keys:
        return 0
    return db.execute(delete(RateLimitBucket).where(RateLimitBucket.key_hash.in_(keys),
                                                    RateLimitBucket.expires_at < now)).rowcount


_ready = {"value": False}


def ready(db) -> bool:
    if not _ready["value"]:
        _ready["value"] = inspect(db.connection()).has_table("rate_limit_buckets")
    return _ready["value"]


def hit(scope: str, subject: str, *, limit: int | None = None, window: int | None = None,
        now: datetime | None = None, session_factory=None) -> None:
    """Count one attempt; raise ``RateLimited`` when it is over the limit. Never fails open silently:
    a database error propagates like any other."""
    default_limit, default_window = LIMITS[scope]
    limit, window = limit or default_limit, window or default_window
    moment = now or datetime.now(timezone.utc)
    if session_factory is None:
        from app.db import Session as session_factory
    with session_factory.begin() as db:
        if not ready(db):
            return  # migration 0022 not applied yet: the login throttle (auth_login_attempts) still applies
        attempts, expires_at = count(db, scope, subject, window=window, now=moment)
        if random.random() < PRUNE_PROBABILITY:
            prune(db, moment)
    if attempts > limit:
        raise RateLimited(scope, (expires_at - moment).total_seconds())


def remaining(db, scope: str, subject: str, *, now: datetime | None = None) -> int:
    """How many attempts are left in the current window (for tests and diagnostics)."""
    limit, _ = LIMITS[scope]
    moment = now or datetime.now(timezone.utc)
    row = db.get(RateLimitBucket, _key(scope, subject))
    if row is None or _aware(row.expires_at) <= moment:
        return limit
    return max(0, limit - row.count)
