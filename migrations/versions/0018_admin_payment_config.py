"""Admin-managed payment gateway configuration (Phase 19).

* ``payment_provider_configs``: one row per provider (``payos``, ``onepay``). ``enabled``
  is the system admin's switch for new checkouts; ``mode`` is OnePAY's sandbox,
  production or custom endpoints. Every credential is inside ``config_ciphertext``
  (Fernet, see ``app/secret_box.py``); no secret has a column of its own. A row
  without ciphertext only records the switch for credentials that still come from
  the legacy bootstrap file or environment.
* ``payment_config_audit``: who created, changed, enabled, disabled or tested a
  provider's configuration, and when. ``metadata_json`` names changed fields; it
  never holds a value.

No existing row changes: payment orders, subscriptions, the credit ledger and the
``payment_activity`` setting are untouched, and providers configured in the
bootstrap file or environment keep working. Downgrading drops both tables.

Revision ID: 0018_admin_payment_config
Revises: 0017_notify_support_verify
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_admin_payment_config"
down_revision = "0017_notify_support_verify"
branch_labels = None
depends_on = None

PROVIDERS = ("payos", "onepay")
MODES = ("sandbox", "production", "custom")
ACTIONS = ("created", "updated", "enabled", "disabled", "tested")


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "payment_provider_configs",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=True),
        sa.Column("config_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint(_in("provider", PROVIDERS), name="ck_payment_provider_configs_provider"),
        sa.CheckConstraint(f"mode IS NULL OR {_in('mode', MODES)}", name="ck_payment_provider_configs_mode"),
    )
    op.create_table(
        "payment_config_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("admin_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.CheckConstraint(_in("provider", PROVIDERS), name="ck_payment_config_audit_provider"),
        sa.CheckConstraint(_in("action", ACTIONS), name="ck_payment_config_audit_action"),
    )
    op.create_index("ix_payment_config_audit_provider", "payment_config_audit", ["provider", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_config_audit_provider", table_name="payment_config_audit")
    op.drop_table("payment_config_audit")
    op.drop_table("payment_provider_configs")
