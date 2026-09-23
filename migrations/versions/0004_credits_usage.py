"""Credit balances, immutable ledger, and usage events.

Revision ID: 0004_credits_usage
Revises: 0003_billing_orders
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_credits_usage"
down_revision = "0003_billing_orders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_orders", sa.Column("credits_award", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("credit_accounts",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("credit_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("reference", sa.String(100), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_credit_ledger_workspace_id", "credit_ledger", ["workspace_id"])
    op.create_table("usage_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("tool", sa.String(80), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(100), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_usage_events_workspace_id", "usage_events", ["workspace_id"])
    connection = op.get_bind()
    accounts = sa.table("credit_accounts", sa.column("workspace_id", sa.String), sa.column("balance", sa.Integer))
    workspaces = sa.table("workspaces", sa.column("id", sa.String))
    for (workspace_id,) in connection.execute(sa.select(workspaces.c.id)):
        connection.execute(accounts.insert().values(workspace_id=workspace_id, balance=0))


def downgrade():
    op.drop_index("ix_usage_events_workspace_id", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_credit_ledger_workspace_id", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_table("credit_accounts")
    op.drop_column("payment_orders", "credits_award")
