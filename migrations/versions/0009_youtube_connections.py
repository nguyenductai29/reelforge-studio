"""Encrypted YouTube OAuth connections and one-use callback state.

Revision ID: 0009_youtube_connections
Revises: 0008_auth_security
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_youtube_connections"
down_revision = "0008_auth_security"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "youtube_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_youtube_oauth_states_workspace_id", "youtube_oauth_states", ["workspace_id"])
    op.create_index("ix_youtube_oauth_states_user_id", "youtube_oauth_states", ["user_id"])
    op.create_index("ix_youtube_oauth_states_expires_at", "youtube_oauth_states", ["expires_at"])
    op.create_table(
        "youtube_connections",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=False),
        sa.Column("refresh_token_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("youtube_connections")
    op.drop_index("ix_youtube_oauth_states_expires_at", table_name="youtube_oauth_states")
    op.drop_index("ix_youtube_oauth_states_user_id", table_name="youtube_oauth_states")
    op.drop_index("ix_youtube_oauth_states_workspace_id", table_name="youtube_oauth_states")
    op.drop_table("youtube_oauth_states")
