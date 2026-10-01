"""Account security (Phase 22): email verification, password reset, TOTP 2FA, session details,
the transactional email outbox, a general audit log and durable rate limits.

* ``users``: when the account was created and its email verified, when the password last
  changed, the last sign-in, the preferred email language, terms acceptance, and the TOTP
  second factor (secret encrypted with the master key, ``app/secret_box.py``).
  **Every existing account is marked verified**: it was created before verification
  existed, and its owner has been signing in with it.
* ``login_sessions``: a public ``id`` (sessions are listed and revoked by it; the token
  hash is never shown), when the session was created and last seen, and the user agent and
  client address it was created from. Existing sessions get an id and keep working.
* ``account_tokens``: one-time tokens for email verification, password reset and the
  second sign-in step. Only the SHA-256 of a token is stored.
* ``recovery_codes``: the 2FA recovery codes, hashed, each usable once.
* ``email_outbox``: transactional emails waiting to be sent. Their parameters (which can
  hold a reset link) are encrypted and erased once the email is sent.
* ``audit_events``: security and administration events: who, what, when, from where.
  Never a password, token or secret.
* ``rate_limit_buckets``: fixed-window counters shared by every API process.

Downgrading drops the new tables and columns; no existing row is lost.

Revision ID: 0022_account_security
Revises: 0021_default_production_origin
"""
from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0022_account_security"
down_revision = "0021_default_production_origin"
branch_labels = None
depends_on = None

USER_COLUMNS = (
    ("created_at", sa.DateTime(timezone=True)),
    ("email_verified_at", sa.DateTime(timezone=True)),
    ("password_changed_at", sa.DateTime(timezone=True)),
    ("last_login_at", sa.DateTime(timezone=True)),
    ("locale", sa.String(8)),
    ("totp_ciphertext", sa.Text()),
    ("totp_pending_ciphertext", sa.Text()),
    ("totp_enabled_at", sa.DateTime(timezone=True)),
    ("totp_last_step", sa.BigInteger()),
    ("terms_version", sa.String(32)),
    ("terms_accepted_at", sa.DateTime(timezone=True)),
)
SESSION_COLUMNS = (
    ("id", sa.String(36)),
    ("created_at", sa.DateTime(timezone=True)),
    ("last_seen_at", sa.DateTime(timezone=True)),
    ("user_agent", sa.String(255)),
    ("ip", sa.String(64)),
)


def upgrade() -> None:
    for name, kind in USER_COLUMNS:
        op.add_column("users", sa.Column(name, kind, nullable=True))
    for name, kind in SESSION_COLUMNS:
        op.add_column("login_sessions", sa.Column(name, kind, nullable=True))

    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    users = sa.table("users", sa.column("email_verified_at", sa.DateTime(timezone=True)))
    conn.execute(users.update().where(users.c.email_verified_at.is_(None)).values(email_verified_at=now))
    sessions = sa.table("login_sessions", sa.column("token_hash", sa.String), sa.column("id", sa.String),
                        sa.column("expires_at", sa.DateTime(timezone=True)),
                        sa.column("created_at", sa.DateTime(timezone=True)),
                        sa.column("last_seen_at", sa.DateTime(timezone=True)))
    for (token_hash,) in conn.execute(sa.select(sessions.c.token_hash)).all():
        conn.execute(sessions.update().where(sessions.c.token_hash == token_hash)
                     .values(id=str(uuid.uuid4()), created_at=now, last_seen_at=now))
    op.create_index("ix_login_sessions_id", "login_sessions", ["id"], unique=True)
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])

    op.create_table(
        "account_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.CheckConstraint("purpose IN ('verify_email', 'password_reset', 'login_challenge')",
                           name="ck_account_tokens_purpose"),
    )
    op.create_index("ix_account_tokens_user", "account_tokens", ["user_id", "purpose"])
    op.create_index("ix_account_tokens_expires", "account_tokens", ["expires_at"])
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_codes_user_code"),
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_address", sa.String(320), nullable=False),
        sa.Column("template", sa.String(40), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("payload_ciphertext", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("dedupe_key", sa.String(160), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'sending', 'sent', 'failed', 'cancelled')",
                           name="ck_email_outbox_status"),
    )
    op.create_index("ix_email_outbox_due", "email_outbox", ["status", "next_attempt_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("workspace_id", sa.String(36), nullable=True),
        sa.Column("target_type", sa.String(24), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="ck_audit_events_outcome"),
    )
    op.create_index("ix_audit_events_created", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_action", "audit_events", ["action", "created_at"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_events_workspace", "audit_events", ["workspace_id", "created_at"])
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_limit_buckets_expires", "rate_limit_buckets", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_buckets_expires", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    for name in ("ix_audit_events_workspace", "ix_audit_events_actor", "ix_audit_events_action",
                 "ix_audit_events_created"):
        op.drop_index(name, table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_email_outbox_due", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_table("recovery_codes")
    op.drop_index("ix_account_tokens_expires", table_name="account_tokens")
    op.drop_index("ix_account_tokens_user", table_name="account_tokens")
    op.drop_table("account_tokens")
    op.drop_index("ix_login_sessions_user_id", table_name="login_sessions")
    op.drop_index("ix_login_sessions_id", table_name="login_sessions")
    # Plain DROP COLUMN (SQLite 3.35+, PostgreSQL): no table is recreated, so foreign keys pointing at users stay
    # valid even with SQLite's foreign key checks on.
    for name, _ in reversed(SESSION_COLUMNS):
        op.drop_column("login_sessions", name)
    for name, _ in reversed(USER_COLUMNS):
        op.drop_column("users", name)
