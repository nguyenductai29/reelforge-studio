"""VND prices and immutable checkout orders.

Revision ID: 0003_billing_orders
Revises: 0002_plans_users
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_billing_orders"
down_revision = "0002_plans_users"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plans", sa.Column("price_vnd", sa.Integer(), nullable=True))
    op.create_table("payment_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("plan_code", sa.String(20), sa.ForeignKey("plans.code"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("order_code", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("amount_vnd", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("provider_reference", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_payment_orders_workspace_id", "payment_orders", ["workspace_id"])


def downgrade():
    op.drop_index("ix_payment_orders_workspace_id", table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_column("plans", "price_vnd")
