"""TikTok and Facebook connections, scheduled publications, worker heartbeats and clip lineage.

* ``channel_connections`` / ``channel_oauth_states``: OAuth grants for TikTok and
  Facebook, encrypted like the YouTube connection (``youtube_connections`` is unchanged).
* ``publications``: ``scheduled_for`` and ``published_at`` (UTC), and two more states,
  ``scheduled`` (waiting for the scheduler) and ``cancelled``.
* ``worker_heartbeats``: the last time each worker process reported in.
* ``assets.source_asset_id``: the source video a clip was cut from (Movie Recap).

Existing rows are unchanged. Downgrading turns ``scheduled`` and ``cancelled``
publications into ``failed`` ones (the older schema has neither state).

Revision ID: 0014_channels_scheduling_ops
Revises: 0013_publication_metadata
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_channels_scheduling_ops"
down_revision = "0013_publication_metadata"
branch_labels = None
depends_on = None

OLD_STATES = "state IN ('queued', 'uploading', 'succeeded', 'failed', 'needs_attention')"
NEW_STATES = ("state IN ('scheduled', 'queued', 'uploading', 'succeeded', 'failed', 'needs_attention', "
              "'cancelled')")


def upgrade() -> None:
    op.create_table(
        "channel_connections",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("channel", sa.String(32), primary_key=True),
        sa.Column("account_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.String(255), nullable=True),
        sa.Column("access_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("refresh_token_ciphertext", sa.Text(), nullable=True),
        sa.Column("accounts_ciphertext", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("channel IN ('tiktok', 'facebook')", name="ck_channel_connections_channel"),
    )
    op.create_table(
        "channel_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_channel_oauth_states_workspace_id", "channel_oauth_states", ["workspace_id"])
    op.create_index("ix_channel_oauth_states_expires_at", "channel_oauth_states", ["expires_at"])
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker", sa.String(64), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("publications") as batch:
        batch.add_column(sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint("ck_publications_state", type_="check")
        batch.create_check_constraint("ck_publications_state", NEW_STATES)
    op.create_index("ix_publications_due", "publications", ["state", "scheduled_for"])
    op.add_column("assets", sa.Column("source_asset_id", sa.String(36), nullable=True))
    op.create_index("ix_assets_source_asset_id", "assets", ["source_asset_id"])


def downgrade() -> None:
    op.drop_index("ix_assets_source_asset_id", table_name="assets")
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("source_asset_id")
    op.drop_index("ix_publications_due", table_name="publications")
    op.execute("UPDATE publications SET state = 'failed', last_error = 'downgraded_' || state "
               "WHERE state IN ('scheduled', 'cancelled')")
    with op.batch_alter_table("publications") as batch:
        batch.drop_constraint("ck_publications_state", type_="check")
        batch.create_check_constraint("ck_publications_state", OLD_STATES)
        batch.drop_column("published_at")
        batch.drop_column("scheduled_for")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_channel_oauth_states_expires_at", table_name="channel_oauth_states")
    op.drop_index("ix_channel_oauth_states_workspace_id", table_name="channel_oauth_states")
    op.drop_table("channel_oauth_states")
    op.drop_table("channel_connections")
