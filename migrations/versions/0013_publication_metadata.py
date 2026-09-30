"""YouTube publication privacy, tags and the upload result YouTube reports.

Adds to ``publications``:

* ``privacy_status``: the requested visibility, ``private`` (the only value before
  this migration, so existing rows keep it), ``unlisted`` or ``public``;
* ``tags``: a JSON array of tags, ``[]`` for existing rows;
* ``remote_status`` and ``remote_privacy``: YouTube's ``uploadStatus`` and
  ``privacyStatus`` after a successful upload (YouTube may keep a video private
  when the Google project is not verified).

Existing rows and their meaning are unchanged.

Revision ID: 0013_publication_metadata
Revises: 0012_job_reconciliation
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_publication_metadata"
down_revision = "0012_job_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.add_column(sa.Column("privacy_status", sa.String(16), nullable=False, server_default="private"))
        batch.add_column(sa.Column("tags", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("remote_status", sa.String(32), nullable=True))
        batch.add_column(sa.Column("remote_privacy", sa.String(16), nullable=True))
        batch.create_check_constraint("ck_publications_privacy", "privacy_status IN ('private', 'unlisted', 'public')")


def downgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.drop_constraint("ck_publications_privacy", type_="check")
        batch.drop_column("remote_privacy")
        batch.drop_column("remote_status")
        batch.drop_column("tags")
        batch.drop_column("privacy_status")
