"""The production frontend origin becomes the default; data only, no schema change.

Until now a new installation stored ``frontend_origin = "http://localhost:3000"`` and
``secure_cookies = false`` (System Settings, seeded when the API first starts). The
defaults are now ``https://reelforge.mul-service.com`` with Secure cookies.

An installation still on the **exact** old defaults moves to the new ones:

* ``frontend_origin`` exactly ``"http://localhost:3000"`` becomes
  ``"https://reelforge.mul-service.com"``;
* ``secure_cookies`` becomes ``true`` only when it is ``false`` **and** the origin was
  that old default. With any other origin it is the admin's choice and stays.

Anything else an admin saved is kept: another domain, another port or path form
(``http://localhost:3000/`` included), ``secure_cookies`` already true. A development machine that keeps using
``http://localhost:3000`` says so in its own ``instance/bootstrap.json``
(``frontend_origin``, ``secure_cookies``); that never touches this table.

Downgrading changes nothing. The previous code reads these values as they are, and
the new values cannot be told apart from an admin who typed them (the deployment
guide asked for exactly these), so reverting them could undo a deliberate setting.

Revision ID: 0021_default_production_origin
Revises: 0020_manual_payment_statuses
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0021_default_production_origin"
down_revision = "0020_manual_payment_statuses"
branch_labels = None
depends_on = None

OLD_ORIGIN = "http://localhost:3000"
NEW_ORIGIN = "https://reelforge.mul-service.com"
settings = sa.table("system_settings", sa.column("key", sa.String), sa.column("value", sa.Text))


def _stored(conn, key):
    value = conn.execute(sa.select(settings.c.value).where(settings.c.key == key)).scalar()
    try:
        return json.loads(value) if value is not None else None
    except ValueError:
        return None


def upgrade() -> None:
    conn = op.get_bind()
    if _stored(conn, "frontend_origin") != OLD_ORIGIN:
        return  # a new installation (seeded with the new defaults later) or an origin an admin chose
    if _stored(conn, "secure_cookies") is False:
        conn.execute(settings.update().where(settings.c.key == "secure_cookies").values(value=json.dumps(True)))
    conn.execute(settings.update().where(settings.c.key == "frontend_origin").values(value=json.dumps(NEW_ORIGIN)))


def downgrade() -> None:
    pass
