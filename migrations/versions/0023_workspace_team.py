"""Teams (Phase 23): workspace invitations, an explicit active workspace, when members joined.

* ``workspace_invites``: an invitation by email with a role (admin, editor, viewer). Only
  the SHA-256 of its token is stored; it expires and is accepted at most once.
* ``login_sessions.active_workspace_id``: the workspace a session works in. Until a user
  switches, the API picks one deterministically (the remembered one, else the oldest
  membership). It is checked against the memberships on every request.
* ``users.last_workspace_id``: the workspace a new session starts in.
* ``memberships.created_at``: when a member joined (null for members from before).

Roles stay strings: ``owner`` (every existing membership), ``admin``, ``editor`` and
``viewer`` (``app/permissions.py``). Downgrading drops the table and columns.

Revision ID: 0023_workspace_team
Revises: 0022_account_security
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_workspace_team"
down_revision = "0022_account_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="ck_workspace_invites_role"),
    )
    op.create_index("ix_workspace_invites_workspace", "workspace_invites", ["workspace_id", "email"])
    op.add_column("login_sessions", sa.Column("active_workspace_id", sa.String(36), nullable=True))
    op.add_column("users", sa.Column("last_workspace_id", sa.String(36), nullable=True))
    op.add_column("memberships", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("memberships", "created_at")
    op.drop_column("users", "last_workspace_id")
    op.drop_column("login_sessions", "active_workspace_id")
    op.drop_index("ix_workspace_invites_workspace", table_name="workspace_invites")
    op.drop_table("workspace_invites")
