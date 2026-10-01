"""Centralized system configuration and manual VietQR payments (Phase 20).

* ``system_config``: one row per admin-managed setting (``app/system_config.py``).
  Plain settings keep JSON in ``value``; secrets keep Fernet ciphertext (master key,
  ``app/secret_box.py``) in ``ciphertext``. Exactly one of the two is set. A setting
  without a row falls back to its legacy environment variable, then its default, so
  installations configured with ``.env.runtime`` keep working unchanged.
* ``system_config_audit``: who changed or tested which settings, by name only.
* ``payment_orders.transfer_reported_at``: when the buyer reported a manual VietQR
  transfer (null for every other order).
* ``payment_order_events``: a manual order's reported / confirmed / rejected history,
  with who and the amount.

No existing row changes: users, workspaces, assets, orders, credits, payment
gateway configurations and OAuth tokens are untouched. Downgrading drops the new
tables and column.

Revision ID: 0019_system_configuration
Revises: 0018_admin_payment_config
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_system_configuration"
down_revision = "0018_admin_payment_config"
branch_labels = None
depends_on = None


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("ciphertext", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("(value IS NULL) <> (ciphertext IS NULL)", name="ck_system_config_one_value"),
    )
    op.create_table(
        "system_config_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("section", sa.String(24), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("admin_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.CheckConstraint(_in("action", ("updated", "tested")), name="ck_system_config_audit_action"),
    )
    op.create_index("ix_system_config_audit_section", "system_config_audit", ["section", "created_at"])
    op.add_column("payment_orders", sa.Column("transfer_reported_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "payment_order_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("payment_orders.id"), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount_vnd", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("action", ("transfer_reported", "confirmed", "rejected")),
                           name="ck_payment_order_events_action"),
    )
    op.create_index("ix_payment_order_events_order", "payment_order_events", ["order_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_order_events_order", table_name="payment_order_events")
    op.drop_table("payment_order_events")
    with op.batch_alter_table("payment_orders") as batch:
        batch.drop_column("transfer_reported_at")
    op.drop_index("ix_system_config_audit_section", table_name="system_config_audit")
    op.drop_table("system_config_audit")
    op.drop_table("system_config")
