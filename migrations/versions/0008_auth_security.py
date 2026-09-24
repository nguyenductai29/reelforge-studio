"""Persistent login throttling.

Revision ID: 0008_auth_security
Revises: 0007_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_auth_security"
down_revision = "0007_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_login_attempts",
        sa.Column("identifier_hash", sa.String(64), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("failure_count >= 0", name="ck_auth_login_attempts_failure_count"),
    )
    op.create_index("ix_auth_login_attempts_updated_at", "auth_login_attempts", ["updated_at"])


def downgrade():
    op.drop_index("ix_auth_login_attempts_updated_at", table_name="auth_login_attempts")
    op.drop_table("auth_login_attempts")
