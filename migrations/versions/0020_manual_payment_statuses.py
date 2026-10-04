"""Explicit statuses for manual VietQR orders (Phase 21); data only, no schema change.

Phase 20 kept a manual order the buyer reported as ``pending`` with a
``transfer_reported_at`` time, and an order an admin rejected as ``failed``. They
now have statuses of their own:

* ``awaiting_confirmation``: the buyer reported the transfer; an admin has to check
  the bank account (``pending`` + ``transfer_reported_at`` before);
* ``rejected``: an admin did not find the money (``failed`` + a ``rejected`` event
  before). It can still be confirmed if the money turns up.

Only ``bank_qr`` orders change; payOS and OnePAY orders keep their statuses.
``payment_orders.status`` has no check constraint, so no column changes.
Downgrading maps both statuses back.

Revision ID: 0020_manual_payment_statuses
Revises: 0019_system_configuration
"""
from alembic import op

revision = "0020_manual_payment_statuses"
down_revision = "0019_system_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE payment_orders SET status = 'awaiting_confirmation' "
               "WHERE provider = 'bank_qr' AND status = 'pending' AND transfer_reported_at IS NOT NULL")
    op.execute("UPDATE payment_orders SET status = 'rejected' "
               "WHERE provider = 'bank_qr' AND status = 'failed' AND id IN "
               "(SELECT order_id FROM payment_order_events WHERE action = 'rejected')")


def downgrade() -> None:
    op.execute("UPDATE payment_orders SET status = 'pending' "
               "WHERE provider = 'bank_qr' AND status = 'awaiting_confirmation'")
    op.execute("UPDATE payment_orders SET status = 'failed' WHERE provider = 'bank_qr' AND status = 'rejected'")
