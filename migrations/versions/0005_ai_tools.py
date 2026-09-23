"""Workspace-scoped AI tool catalog.

Revision ID: 0005_ai_tools
Revises: 0004_credits_usage
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_ai_tools"
down_revision = "0004_credits_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_tools",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("task", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_ai_tools_workspace_id", "ai_tools", ["workspace_id"])


def downgrade():
    op.drop_index("ix_ai_tools_workspace_id", table_name="ai_tools")
    op.drop_table("ai_tools")
