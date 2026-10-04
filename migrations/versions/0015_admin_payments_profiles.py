"""User display names, and indexes for the paginated admin tables and payment history.

* ``user_profiles``: an optional display name per user (Settings → General).
  A separate table keeps ``users`` unchanged for code that reads it.
* ``ix_workspaces_owner_id``: studios joined with their owner (admin search).
* ``ix_payment_orders_created_at`` and ``ix_payment_orders_provider_status``:
  payment history pages, newest first, filtered by provider and status.

Card payments (OnePAY) reuse ``payment_orders`` as they are: ``provider`` is
``onepay``, ``order_code`` is the merchant transaction reference and
``provider_reference`` the OnePAY transaction number. Existing rows are unchanged.

Revision ID: 0015_admin_payments_profiles
Revises: 0014_channels_scheduling_ops
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_admin_payments_profiles"
down_revision = "0014_channels_scheduling_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("display_name", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspaces_owner_id", "workspaces", ["owner_id"])
    op.create_index("ix_payment_orders_created_at", "payment_orders", ["created_at"])
    op.create_index("ix_payment_orders_provider_status", "payment_orders", ["provider", "status"])


def downgrade() -> None:
    op.drop_index("ix_payment_orders_provider_status", table_name="payment_orders")
    op.drop_index("ix_payment_orders_created_at", table_name="payment_orders")
    op.drop_index("ix_workspaces_owner_id", table_name="workspaces")
    op.drop_table("user_profiles")
