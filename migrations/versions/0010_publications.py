"""Idempotent publication records with encrypted resumable upload sessions.

Revision ID: 0010_publications
Revises: 0009_youtube_connections
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_publications"
down_revision = "0009_youtube_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("workflow_jobs.id"), nullable=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("remote_id", sa.String(255), nullable=True),
        sa.Column("upload_session_ciphertext", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'uploading', 'succeeded', 'failed', 'needs_attention')",
            name="ck_publications_state",
        ),
        sa.UniqueConstraint("workspace_id", "run_id", "channel", name="uq_publications_run_channel"),
        sa.UniqueConstraint("job_id", name="uq_publications_job_id"),
    )
    op.create_index("ix_publications_workspace_id", "publications", ["workspace_id"])
    op.create_index("ix_publications_run_id", "publications", ["run_id"])
    op.create_index("ix_publications_asset_id", "publications", ["asset_id"])
    op.create_index("ix_publications_workspace_state", "publications", ["workspace_id", "state", "updated_at"])


def downgrade():
    op.drop_index("ix_publications_workspace_state", table_name="publications")
    op.drop_index("ix_publications_asset_id", table_name="publications")
    op.drop_index("ix_publications_run_id", table_name="publications")
    op.drop_index("ix_publications_workspace_id", table_name="publications")
    op.drop_table("publications")
