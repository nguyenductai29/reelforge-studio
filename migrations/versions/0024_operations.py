"""Operations (Phases 24–25): database backup history and system alert state.

* ``backup_runs``: each run of ``python -m app.backup run`` (deploy/backup.sh, the
  reelforge-backup timer): when, the outcome, the file name and size. Never a password.
* ``system_alerts``: one row per alert condition (a stale worker, a full disk, an overdue
  backup...). It remembers when the condition started, when admins were last notified
  and when it cleared, so a condition notifies once, then again only after a cooldown.

Downgrading drops both tables.

Revision ID: 0024_operations
Revises: 0023_workspace_team
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_operations"
down_revision = "0023_workspace_team"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("host", sa.String(120), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_backup_runs_status"),
    )
    op.create_index("ix_backup_runs_started", "backup_runs", ["kind", "started_at"])
    op.create_table(
        "system_alerts",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("level", sa.String(12), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_alerts")
    op.drop_index("ix_backup_runs_started", table_name="backup_runs")
    op.drop_table("backup_runs")
