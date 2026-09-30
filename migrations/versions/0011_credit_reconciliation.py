"""Append-only decisions for uncertain paid workflow steps.

Revision ID: 0011_credit_reconciliation
Revises: 0010_publications
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_credit_reconciliation"
down_revision = "0010_publications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credit_reconciliations",
        sa.Column("step_id", sa.String(36), sa.ForeignKey("workflow_run_steps.id"), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("workflow_jobs.id"), nullable=False, unique=True),
        sa.Column("reservation_id", sa.String(36), sa.ForeignKey("credit_ledger.id"), nullable=False, unique=True),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("reconciled_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.CheckConstraint("decision IN ('confirmed_charge', 'refunded')", name="ck_reconciliation_decision"),
        sa.CheckConstraint("credits > 0", name="ck_reconciliation_credits"),
    )
    op.create_index("ix_credit_reconciliations_reconciled_at", "credit_reconciliations", ["reconciled_at"])


def downgrade():
    op.drop_index("ix_credit_reconciliations_reconciled_at", table_name="credit_reconciliations")
    op.drop_table("credit_reconciliations")
