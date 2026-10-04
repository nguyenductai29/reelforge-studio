"""Database-backed login throttling and first-owner setup serialization.

Both helpers require a transaction owned by the caller. Failed attempts must
be committed before an HTTP error is raised, otherwise the throttle rolls
back with the request. Five failures within 15 minutes block the identifier
for 15 minutes. Identifiers are stored only as SHA-256 digests.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import CheckConstraint, DateTime, Integer, String, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, SystemSetting


FAILURE_LIMIT = 5
FAILURE_WINDOW = timedelta(minutes=15)
BLOCK_DURATION = timedelta(minutes=15)
ATTEMPT_RETENTION = timedelta(days=1)
CLEANUP_BATCH_SIZE = 20


class AuthAttempt(Base):
    __tablename__ = "auth_login_attempts"
    __table_args__ = (
        CheckConstraint("failure_count >= 0", name="ck_auth_login_attempts_failure_count"),
    )

    identifier_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _digest(identifier: str) -> str:
    return hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()


def _locked_attempt(db, identifier: str, now: datetime) -> AuthAttempt:
    """Create a row if needed, then hold its lock until the caller commits.

    PostgreSQL uses a row lock. SQLite serializes writers when the insert is
    attempted, which also protects the absent-row case during first setup.
    """
    digest = _digest(identifier)
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgres_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise RuntimeError(f"Unsupported auth throttle database: {dialect}")
    db.execute(insert(AuthAttempt).values(
        identifier_hash=digest, failure_count=0, window_started_at=now,
        blocked_until=None, updated_at=now,
    ).on_conflict_do_nothing(index_elements=[AuthAttempt.identifier_hash]))
    return db.scalar(select(AuthAttempt).where(AuthAttempt.identifier_hash == digest).with_for_update())


def check_login_allowed(db, identifier: str, now: datetime | None = None) -> bool:
    """Check the current block while locking the identifier's row."""
    moment = _utc(now)
    attempt = _locked_attempt(db, identifier, moment)
    # Keep the active identifier locked before housekeeping. Other concurrent
    # logins skip locked rows on PostgreSQL, avoiding cross-identifier deadlocks.
    prune_expired_login_attempts(db, moment, exclude_identifier_hash=attempt.identifier_hash)
    return attempt.blocked_until is None or _utc(attempt.blocked_until) <= moment


def prune_expired_login_attempts(db, now: datetime | None = None, *, batch_size: int = CLEANUP_BATCH_SIZE,
                                 exclude_identifier_hash: str | None = None) -> int:
    """Delete a bounded batch of stale rows without removing active blocks.

    Each login checks one indexed batch. Rows older than 24 hours are beyond
    both the failure window and lock duration. The predicates are repeated
    at deletion so a row refreshed after selection cannot be removed.
    """
    if not isinstance(batch_size, int) or not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    moment = _utc(now)
    cutoff = moment - ATTEMPT_RETENTION
    expired = [AuthAttempt.updated_at < cutoff,
               or_(AuthAttempt.blocked_until.is_(None), AuthAttempt.blocked_until <= moment)]
    if exclude_identifier_hash is not None:
        expired.append(AuthAttempt.identifier_hash != exclude_identifier_hash)
    hashes = list(db.scalars(select(AuthAttempt.identifier_hash).where(*expired)
        .order_by(AuthAttempt.updated_at, AuthAttempt.identifier_hash)
        .limit(batch_size).with_for_update(skip_locked=True)))
    if not hashes:
        return 0
    result = db.execute(delete(AuthAttempt).where(AuthAttempt.identifier_hash.in_(hashes), *expired))
    return result.rowcount


def record_login_failure(db, identifier: str, now: datetime | None = None) -> int:
    """Atomically record a failed login and return the window's failure count."""
    moment = _utc(now)
    attempt = _locked_attempt(db, identifier, moment)
    if attempt.blocked_until is not None and _utc(attempt.blocked_until) > moment:
        return attempt.failure_count
    if moment - _utc(attempt.window_started_at) >= FAILURE_WINDOW:
        attempt.failure_count = 0
        attempt.window_started_at = moment
        attempt.blocked_until = None
    attempt.failure_count += 1
    attempt.updated_at = moment
    if attempt.failure_count >= FAILURE_LIMIT:
        attempt.blocked_until = moment + BLOCK_DURATION
    return attempt.failure_count


def clear_login_failures(db, identifier: str) -> None:
    """Discard the identifier's failed attempts after a successful login."""
    db.execute(delete(AuthAttempt).where(AuthAttempt.identifier_hash == _digest(identifier)))


def lock_initial_setup(db) -> None:
    """Serialize setup requests before checking whether any owner exists.

    The settings row is seeded at API startup. Updating it to its existing
    value acquires a write lock on SQLite and a row lock on PostgreSQL for
    the remainder of the caller's transaction.
    """
    result = db.execute(update(SystemSetting)
        .where(SystemSetting.key == "registration_enabled")
        .values(value=SystemSetting.value))
    if result.rowcount != 1:
        raise RuntimeError("System settings must be initialized before setup")
