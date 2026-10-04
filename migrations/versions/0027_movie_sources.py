"""Movie sources: temporary source movies for the movie-processing workflows (app/movie_sources.py).

* ``movie_sources``: one source movie of a workspace, imported from a file in the operator's import folder, a
  direct HTTPS media URL or the operator's Google Drive, kept in Google Drive while it is needed, then deleted.
  The row stays (status ``deleted``) for the history; the work lease columns let one movie worker at a time
  import, upload or delete it.
* ``movie_source_uses``: which workflow runs use a source. A source with a run still running or waiting for
  review is never deleted.
* ``assets.movie_source_id``: the source an extracted clip or the working audio came from.

Downgrading drops both tables and the column (Drive files are not touched by a migration).

Revision ID: 0027_movie_sources
Revises: 0026_change_production_origin
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_movie_sources"
down_revision = "0026_change_production_origin"
branch_labels = None
depends_on = None

STATUSES = ("status IN ('created', 'importing', 'uploading', 'ready', 'processing', 'completed', "
            "'delete_scheduled', 'deleting', 'deleted', 'failed')")


def upgrade() -> None:
    op.create_table(
        "movie_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", name="fk_movie_sources_project_id"),
                  nullable=True),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("original_url", sa.String(500), nullable=True),
        sa.Column("url_ciphertext", sa.Text(), nullable=True),
        sa.Column("local_path", sa.String(1000), nullable=True),
        sa.Column("drive_import_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("failure_stage", sa.String(16), nullable=True),
        sa.Column("failure_code", sa.String(48), nullable=True),
        sa.Column("failure_message_safe", sa.String(300), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("container", sa.String(32), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("video_codec", sa.String(32), nullable=True),
        sa.Column("audio_codec", sa.String(32), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("checksum_md5", sa.String(32), nullable=True),
        sa.Column("drive_file_id", sa.String(128), nullable=True),
        sa.Column("drive_folder_id", sa.String(128), nullable=True),
        sa.Column("upload_session_ciphertext", sa.Text(), nullable=True),
        sa.Column("progress_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after_success", sa.Boolean(), nullable=False),
        sa.Column("delete_grace_hours", sa.Integer(), nullable=False),
        sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(STATUSES, name="ck_movie_sources_status"),
        sa.CheckConstraint("source_type IN ('local', 'url', 'drive')", name="ck_movie_sources_source_type"),
    )
    op.create_index("ix_movie_sources_workspace", "movie_sources", ["workspace_id", "created_at"])
    op.create_index("ix_movie_sources_work", "movie_sources", ["status", "next_attempt_at"])
    op.create_index("ix_movie_sources_expires", "movie_sources", ["expires_at"])
    op.create_index("ix_movie_sources_drive_file", "movie_sources", ["drive_file_id"])
    op.create_index("ix_movie_sources_project", "movie_sources", ["project_id"])
    op.create_table(
        "movie_source_uses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("movie_source_id", sa.String(36), sa.ForeignKey("movie_sources.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("movie_source_id", "run_id", name="uq_movie_source_uses_run"),
    )
    op.create_index("ix_movie_source_uses_run", "movie_source_uses", ["run_id"])
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("movie_source_id", sa.String(36), nullable=True))
        batch.create_index("ix_assets_movie_source_id", ["movie_source_id"])


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.drop_index("ix_assets_movie_source_id")
        batch.drop_column("movie_source_id")
    op.drop_index("ix_movie_source_uses_run", table_name="movie_source_uses")
    op.drop_table("movie_source_uses")
    for name in ("ix_movie_sources_project", "ix_movie_sources_drive_file", "ix_movie_sources_expires",
                 "ix_movie_sources_work", "ix_movie_sources_workspace"):
        op.drop_index(name, table_name="movie_sources")
    op.drop_table("movie_sources")
