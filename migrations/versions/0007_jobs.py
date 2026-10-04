"""Durable workflow jobs and generated asset lineage.

Revision ID: 0007_jobs
Revises: 0006_workflow_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_jobs"
down_revision = "0006_workflow_runs"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", name="fk_assets_project_id"), nullable=True))
        batch.add_column(sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id", name="fk_assets_run_id"), nullable=True))
        batch.add_column(sa.Column("step_id", sa.String(36), sa.ForeignKey("workflow_run_steps.id", name="fk_assets_step_id"), nullable=True))
        batch.add_column(sa.Column("provider", sa.String(60), nullable=True))
        batch.add_column(sa.Column("model", sa.String(100), nullable=True))
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_index("ix_assets_run_id", "assets", ["run_id"])
    op.create_index("ix_assets_step_id", "assets", ["step_id"])

    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("workflow_run_steps.id"), nullable=False),
        sa.Column("logical_key", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("state IN ('queued', 'leased', 'succeeded', 'failed')", name="ck_workflow_jobs_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_workflow_jobs_attempt_count"),
        sa.UniqueConstraint("logical_key", name="uq_workflow_jobs_logical_key"),
    )
    op.create_index("ix_workflow_jobs_workspace_id", "workflow_jobs", ["workspace_id"])
    op.create_index("ix_workflow_jobs_run_id", "workflow_jobs", ["run_id"])
    op.create_index("ix_workflow_jobs_step_id", "workflow_jobs", ["step_id"])
    op.create_index("ix_workflow_jobs_claim", "workflow_jobs", ["state", "available_at", "lease_expires_at"])


def downgrade():
    op.drop_index("ix_workflow_jobs_claim", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_step_id", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_run_id", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_workspace_id", table_name="workflow_jobs")
    op.drop_table("workflow_jobs")
    op.drop_index("ix_assets_step_id", table_name="assets")
    op.drop_index("ix_assets_run_id", table_name="assets")
    op.drop_index("ix_assets_project_id", table_name="assets")
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("model")
        batch.drop_column("provider")
        batch.drop_column("step_id")
        batch.drop_column("run_id")
        batch.drop_column("project_id")
