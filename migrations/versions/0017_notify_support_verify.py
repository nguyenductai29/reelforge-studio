"""Notifications, customer support and the live-verification checklist (Phase 18).

* ``notifications``: durable in-app notifications per user. The integer ``id`` orders
  them and is the Server-Sent Events ``id`` a reconnecting client resumes from.
  ``dedupe_key`` (unique per user) makes machine-generated notifications idempotent.
  ``payload`` holds only safe display parameters (names, counts, IDs), never provider data.
* ``support_tickets`` / ``support_messages``: a ticket per request, with an append-only
  thread. Optional context IDs (run, project, payment order, publication) point at
  records the admin can open through existing views; nothing is copied from them.
* ``verification_checks``: the system admin's manual live-verification checklist, one
  row per check key, with who verified it, when, and a note.

Existing rows are unchanged; downgrading drops the four new tables.

Revision ID: 0017_notify_support_verify
Revises: 0016_storage_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_notify_support_verify"
down_revision = "0016_storage_lifecycle"
branch_labels = None
depends_on = None

TICKET_STATUSES = ("open", "waiting_support", "waiting_user", "resolved", "closed")
CATEGORIES = ("billing", "credits", "generation", "publishing", "account", "storage", "bug", "other")


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=True),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(160), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    op.create_index("ix_notifications_user_read", "notifications", ["user_id", "read_at"])

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(12), nullable=False, server_default="normal"),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("payment_order_id", sa.String(36), nullable=True),
        sa.Column("publication_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("status", TICKET_STATUSES), name="ck_support_tickets_status"),
        sa.CheckConstraint(_in("category", CATEGORIES), name="ck_support_tickets_category"),
        sa.CheckConstraint(_in("priority", ("normal", "high")), name="ck_support_tickets_priority"),
    )
    op.create_index("ix_support_tickets_workspace", "support_tickets", ["workspace_id", "updated_at"])
    op.create_index("ix_support_tickets_creator", "support_tickets", ["created_by_user_id"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status", "updated_at"])

    op.create_table(
        "support_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(36), sa.ForeignKey("support_tickets.id"), nullable=False),
        sa.Column("author_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("author_type", sa.String(8), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("author_type", ("user", "admin")), name="ck_support_messages_author"),
    )
    op.create_index("ix_support_messages_ticket", "support_messages", ["ticket_id", "created_at"])

    op.create_table(
        "verification_checks",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("verification_checks")
    op.drop_index("ix_support_messages_ticket", table_name="support_messages")
    op.drop_table("support_messages")
    for name in ("ix_support_tickets_status", "ix_support_tickets_creator", "ix_support_tickets_workspace"):
        op.drop_index(name, table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
