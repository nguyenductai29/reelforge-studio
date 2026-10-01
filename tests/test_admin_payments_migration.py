"""Migration 0015: user display names and the admin/payment-history indexes, from 0014 and back.

Runs on a disposable SQLite database; set ``REELFORGE_TEST_DATABASE_URL`` to an isolated
PostgreSQL test database (its name must contain "test") to run the same program there.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
config = Config("alembic.ini")
url = json.load(open("instance/bootstrap.json"))["database_url"]
engine = create_engine(url)
sqlite = engine.dialect.name == "sqlite"
if not sqlite:
    command.downgrade(config, "base")  # the PostgreSQL test database may hold an earlier run
command.upgrade(config, "0014_channels_scheduling_ops")
NOW = "2026-09-01 00:00:00"
with engine.begin() as connection:
    connection.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) "
                            "VALUES ('u1', 'owner@example.com', 'x', false, true)"))
    connection.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) "
                            "VALUES ('w1', 'Studio', 'u1', 'trial', :now)"), {"now": NOW})
    for code, provider, status in ((1001, "payos", "paid"), (1002, "payos", "pending")):
        connection.execute(text(
            "INSERT INTO payment_orders (id, workspace_id, plan_code, provider, order_code, amount_vnd, status, "
            "checkout_url, provider_reference, created_at, paid_at, credits_award) VALUES "
            "(:id, 'w1', 'standard', :provider, :code, 199000, :status, NULL, NULL, :now, NULL, 0)"),
            {"id": f"o{code}", "provider": provider, "code": code, "status": status, "now": NOW})

def rows(table, key):
    with engine.connect() as connection:
        return sorted((dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {table}"))),
                      key=lambda row: row[key])

before = {"users": rows("users", "id"), "workspaces": rows("workspaces", "id"),
          "payment_orders": rows("payment_orders", "id")}
command.upgrade(config, "head")
inspector = inspect(engine)
assert inspector.has_table("user_profiles")
assert {c["name"] for c in inspector.get_columns("user_profiles")} == {"user_id", "display_name", "updated_at"}
assert "ix_workspaces_owner_id" in {i["name"] for i in inspector.get_indexes("workspaces")}
assert {"ix_payment_orders_created_at", "ix_payment_orders_provider_status"} <= {
    i["name"] for i in inspector.get_indexes("payment_orders")}
for table, old in before.items():
    # Columns later migrations add (e.g. 0019's transfer_reported_at) are not part of this comparison.
    now = [{key: row[key] for key in old_row} for row, old_row in zip(rows(table, "id"), old)]
    assert now == old, (table, old, now)
# A card order needs no schema change: it is a payment_orders row whose provider is onepay.
with engine.begin() as connection:
    connection.execute(text(
        "INSERT INTO payment_orders (id, workspace_id, plan_code, provider, order_code, amount_vnd, status, "
        "checkout_url, provider_reference, created_at, paid_at, credits_award) VALUES "
        "('o1003', 'w1', 'standard', 'onepay', 1003, 199000, 'pending', NULL, NULL, :now, NULL, 0)"), {"now": NOW})
    connection.execute(text("INSERT INTO user_profiles (user_id, display_name, updated_at) VALUES ('u1', 'Owner', :now)"),
                       {"now": NOW})
try:
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO payment_orders (id, workspace_id, plan_code, provider, order_code, amount_vnd, status, "
            "created_at, credits_award) VALUES ('o9', 'w1', 'standard', 'onepay', 1003, 1, 'pending', :now, 0)"),
            {"now": NOW})
    raise AssertionError("a duplicate order code was accepted")
except AssertionError:
    raise
except Exception as exc:
    assert "unique" in str(exc).lower() or "duplicate" in str(exc).lower(), exc

# Downgrading drops only the new table and indexes; every order (including the card one) stays.
command.downgrade(config, "0014_channels_scheduling_ops")
inspector = inspect(engine)
assert not inspector.has_table("user_profiles")
assert "ix_workspaces_owner_id" not in {i["name"] for i in inspector.get_indexes("workspaces")}
assert [row["id"] for row in rows("payment_orders", "id")] == ["o1001", "o1002", "o1003"]
assert rows("users", "id") == before["users"]
command.upgrade(config, "head")
print("ok")
'''


def run(database_url: str, directory: str):
    target = Path(directory)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "instance").mkdir()
    (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": database_url}))
    return subprocess.run([sys.executable, "-c", PROGRAM], cwd=target, env={**os.environ, "PYTHONPATH": str(target)},
                          capture_output=True, text=True)


class AdminPaymentsMigrationTest(unittest.TestCase):
    def test_sqlite_upgrade_from_0014_and_downgrade_keep_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn("ok", result.stdout)

    def test_postgresql_upgrade_from_0014_and_downgrade_keep_existing_rows(self):
        url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
            self.skipTest("REELFORGE_TEST_DATABASE_URL must name a PostgreSQL database whose name contains 'test'")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        with tempfile.TemporaryDirectory() as directory:
            result = run(url, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
