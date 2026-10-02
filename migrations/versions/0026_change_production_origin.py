"""The production domain moves to ``https://reelforge.mul-service.com``; data only, no schema change.

Migration 0021 made ``https://studio.imokome-cloud.com`` the production origin (from the old
localhost defaults). The production domain is now ``https://reelforge.mul-service.com``, the
default a new installation stores. An installation still on the **exact** old production origin
moves to the new one:

* ``frontend_origin`` exactly ``"https://studio.imokome-cloud.com"`` becomes
  ``"https://reelforge.mul-service.com"``;
* ``secure_cookies``, only in that case, becomes ``true`` when it is ``false``: both origins are
  HTTPS. It is never turned off, and a missing row is not invented (the API's default is true);
* once the origin is the new one, an OAuth redirect override (Admin → System settings → Social
  OAuth) that is exactly an old-origin callback (``…/youtube/callback``,
  ``…/channels/callback/tiktok``, ``…/channels/callback/facebook``) follows to the new origin:
  the old one is refused by the same-origin check, so it could only break sign-in with Google,
  TikTok or Facebook. Any other override is the admin's choice and stays.

Everything else stays: another domain, a development origin such as ``http://localhost:3000``,
the new origin already set (secure cookies included), every other setting. An override kept in a
legacy environment file is outside the database: Admin → Verification and the release pre-flight
warn when a redirect does not match the public origin.

Downgrading reverses exactly what the upgrade can produce: the exact new origin becomes the old
one again, and so do the overrides that are exactly new-origin callbacks. ``secure_cookies`` stays
as it is (a downgrade never turns it off).

Revision ID: 0026_change_production_origin
Revises: 0025_verification_status
"""
from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa

revision = "0026_change_production_origin"
down_revision = "0025_verification_status"
branch_labels = None
depends_on = None

OLD_ORIGIN = "https://studio.imokome-cloud.com"
NEW_ORIGIN = "https://reelforge.mul-service.com"
# The OAuth callbacks the frontend serves (app/system_config.py REDIRECT_PATHS), by override setting.
CALLBACKS = {"social.youtube.redirect_uri": "/youtube/callback",
             "social.tiktok.redirect_uri": "/channels/callback/tiktok",
             "social.facebook.redirect_uri": "/channels/callback/facebook"}
settings = sa.table("system_settings", sa.column("key", sa.String), sa.column("value", sa.Text))
config = sa.table("system_config", sa.column("key", sa.String), sa.column("value", sa.Text),
                  sa.column("updated_at", sa.DateTime(timezone=True)), sa.column("updated_by_user_id", sa.String))


def _decoded(value):
    try:
        return json.loads(value) if value is not None else None
    except ValueError:
        return None


def _stored(conn, key):
    return _decoded(conn.execute(sa.select(settings.c.value).where(settings.c.key == key)).scalar())


def _store(conn, key, value) -> None:
    conn.execute(settings.update().where(settings.c.key == key).values(value=json.dumps(value)))


def _move_callbacks(conn, source: str, target: str) -> None:
    """Overrides that are exactly ``source``'s callbacks become ``target``'s; any other value stays."""
    for key, path in CALLBACKS.items():
        if _decoded(conn.execute(sa.select(config.c.value).where(config.c.key == key)).scalar()) == source + path:
            # Changed by this migration, not by an administrator.
            conn.execute(config.update().where(config.c.key == key).values(
                value=json.dumps(target + path), updated_at=datetime.now(timezone.utc), updated_by_user_id=None))


def upgrade() -> None:
    conn = op.get_bind()
    if _stored(conn, "frontend_origin") == OLD_ORIGIN:
        if _stored(conn, "secure_cookies") is False:
            _store(conn, "secure_cookies", True)
        _store(conn, "frontend_origin", NEW_ORIGIN)
    if _stored(conn, "frontend_origin") == NEW_ORIGIN:
        _move_callbacks(conn, OLD_ORIGIN, NEW_ORIGIN)


def downgrade() -> None:
    conn = op.get_bind()
    if _stored(conn, "frontend_origin") != NEW_ORIGIN:
        return  # an origin an administrator chose: the previous code reads it as it is
    _move_callbacks(conn, NEW_ORIGIN, OLD_ORIGIN)
    _store(conn, "frontend_origin", OLD_ORIGIN)
