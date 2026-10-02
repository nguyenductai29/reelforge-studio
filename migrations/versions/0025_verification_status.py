"""Release verification (V1.0 release closure): a status on each manual verification check.

``verification_checks`` could only say "verified" (``verified_at`` set) or nothing. A release
gate also needs "failed" (checked, it does not work) and "not applicable" (a provider the
installation does not use). ``status`` is ``passed``, ``failed``, ``not_applicable`` or NULL
(not checked); ``verified_at`` / ``verified_by_user_id`` now say when and by whom the current
status was recorded, whatever it is. Rows already verified become ``passed``. Nothing is ever
set by the server itself: an administrator records every status.

Downgrading keeps only what the old schema can say: a ``passed`` row stays verified, any other
status loses its date and author (the old code would read them as verified), then the column goes.

Revision ID: 0025_verification_status
Revises: 0024_operations
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_verification_status"
down_revision = "0024_operations"
branch_labels = None
depends_on = None

STATUSES = "status IN ('passed', 'failed', 'not_applicable')"


def upgrade() -> None:
    with op.batch_alter_table("verification_checks") as batch:
        batch.add_column(sa.Column("status", sa.String(16), nullable=True))
        batch.create_check_constraint("ck_verification_checks_status", STATUSES)
    op.execute("UPDATE verification_checks SET status = 'passed' WHERE verified_at IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE verification_checks SET verified_at = NULL, verified_by_user_id = NULL "
               "WHERE status IS NULL OR status <> 'passed'")
    with op.batch_alter_table("verification_checks") as batch:
        batch.drop_constraint("ck_verification_checks_status", type_="check")
        batch.drop_column("status")
