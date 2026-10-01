"""Storage lifecycle: per-plan storage limits, asset kinds and expiry (Phase 17).

* ``plans.storage_limit_bytes``: the media a workspace on this plan may store. Trial,
  Standard and Pro start at 1, 10 and 30 GiB; null falls back to
  ``WORKSPACE_MEDIA_QUOTA_BYTES``. Admins change it under Admin → Plans.
* ``assets.kind``: what the asset is. Existing rows are labelled from their lineage:
  an asset without a run is an upload (``source``); a generated one takes the kind of
  the step that produced it (``render`` → ``final_render``, ``video`` → ``scene_video``,
  ``image``, ``voice``, ``subtitle``, ``extract_clips`` → ``extracted_clip``); anything
  else is ``other``, which never expires.
* ``assets.expired_at``, ``expired_reason``, ``expired_bytes``: when an intermediate file
  expired or a user deleted media. The row stays (lineage, run history, publications)
  with ``bytes`` 0, and ``expired_bytes`` keeps the size that was freed.

Existing rows keep every value they had. Downgrading drops the new columns; an asset
that had already expired then shows as a 0-byte file whose download fails.

Revision ID: 0016_storage_lifecycle
Revises: 0015_admin_payments_profiles
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_storage_lifecycle"
down_revision = "0015_admin_payments_profiles"
branch_labels = None
depends_on = None

GIB = 1024 ** 3
STEP_KINDS = (("render", "final_render"), ("video", "scene_video"), ("image", "generated_image"), ("voice", "voice"),
              ("subtitle", "subtitle"), ("extract_clips", "extracted_clip"))


def upgrade() -> None:
    op.add_column("plans", sa.Column("storage_limit_bytes", sa.BigInteger(), nullable=True))
    plans = sa.table("plans", sa.column("code", sa.String), sa.column("storage_limit_bytes", sa.BigInteger))
    for code, gib in (("trial", 1), ("standard", 10), ("pro", 30)):
        op.execute(plans.update().where(plans.c.code == code).values(storage_limit_bytes=gib * GIB))

    op.add_column("assets", sa.Column("kind", sa.String(24), nullable=True))
    op.add_column("assets", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assets", sa.Column("expired_reason", sa.String(24), nullable=True))
    op.add_column("assets", sa.Column("expired_bytes", sa.BigInteger(), nullable=True))
    op.execute("UPDATE assets SET kind = 'source' WHERE run_id IS NULL")
    cases = " ".join(f"WHEN '{node}' THEN '{kind}'" for node, kind in STEP_KINDS)
    op.execute("UPDATE assets SET kind = CASE (SELECT s.node_type FROM workflow_run_steps s WHERE s.id = assets.step_id) "
               f"{cases} ELSE 'other' END WHERE run_id IS NOT NULL")
    op.create_index("ix_assets_kind_created_at", "assets", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_assets_kind_created_at", table_name="assets")
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("expired_bytes")
        batch.drop_column("expired_reason")
        batch.drop_column("expired_at")
        batch.drop_column("kind")
    with op.batch_alter_table("plans") as batch:
        batch.drop_column("storage_limit_bytes")
