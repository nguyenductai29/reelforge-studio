"""One reconciliation decision per paid job instead of per step.

A multi-scene image or video step queues one paid job per scene, and each
uncertain job needs its own decision. The primary key moves from ``step_id`` to
``job_id`` (already unique and non-null); ``step_id`` stays as an indexed
foreign key. Existing decisions are kept unchanged: every historical step had
exactly one paid job.

Revision ID: 0012_job_reconciliation
Revises: 0011_credit_reconciliation
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_job_reconciliation"
down_revision = "0011_credit_reconciliation"
branch_labels = None
depends_on = None


def _table(primary: str) -> sa.Table:
    """The table as it should look with ``primary`` as its primary key (used to rebuild it on SQLite)."""
    return sa.Table(
        "credit_reconciliations", sa.MetaData(),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("workflow_run_steps.id"), nullable=False,
                  primary_key=primary == "step_id"),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("workflow_jobs.id"), nullable=False, unique=True,
                  primary_key=primary == "job_id"),
        sa.Column("reservation_id", sa.String(36), sa.ForeignKey("credit_ledger.id"), nullable=False, unique=True),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("reconciled_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.CheckConstraint("decision IN ('confirmed_charge', 'refunded')", name="ck_reconciliation_decision"),
        sa.CheckConstraint("credits > 0", name="ck_reconciliation_credits"),
    )


def _move_primary_key(primary: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot alter a primary key: rebuild the table and copy every row.
        with op.batch_alter_table("credit_reconciliations", recreate="always", copy_from=_table(primary)):
            pass
        op.create_index("ix_credit_reconciliations_reconciled_at", "credit_reconciliations", ["reconciled_at"])
    else:
        name = sa.inspect(bind).get_pk_constraint("credit_reconciliations").get("name") or "credit_reconciliations_pkey"
        op.drop_constraint(name, "credit_reconciliations", type_="primary")
        op.create_primary_key(name, "credit_reconciliations", [primary])


def upgrade():
    _move_primary_key("job_id")
    op.create_index("ix_credit_reconciliations_step_id", "credit_reconciliations", ["step_id"])


def downgrade():
    op.drop_index("ix_credit_reconciliations_step_id", table_name="credit_reconciliations")
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("ix_credit_reconciliations_reconciled_at", table_name="credit_reconciliations")
    # Fails if a step has more than one decision, which only multi-job steps can create.
    _move_primary_key("step_id")
