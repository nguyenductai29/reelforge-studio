"""Migration 0019: central system configuration and manual VietQR order history, from 0018 and back.

Orders, gateway configurations, the credit ledger and settings must survive both directions. Runs on a disposable SQLite database; set ``REELFORGE_TEST_DATABASE_URL`` to an
isolated PostgreSQL test database (its name must contain "test") to run the same program there.
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
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")  # the PostgreSQL test database may hold an earlier run
command.upgrade(config, "0018_admin_payment_config")
NOW = "2026-09-01 00:00:00"
with engine.begin() as connection:
    connection.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) "
                            "VALUES ('u1', 'owner@example.com', 'x', true, true)"))
    connection.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) "
                            "VALUES ('w1', 'Studio', 'u1', 'standard', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO payment_orders (id, workspace_id, plan_code, provider, order_code, amount_vnd, "
                            "credits_award, status, provider_reference, created_at, paid_at) VALUES "
                            "('o1', 'w1', 'standard', 'payos', 1234567890123, 30000, 5, 'paid', 'REF', :now, :now)"),
                       {"now": NOW})
    connection.execute(text("INSERT INTO payment_provider_configs (provider, enabled, mode, config_ciphertext, "
                            "created_at, updated_at) VALUES ('onepay', true, 'sandbox', 'gAAAA-cipher', :now, :now)"),
                       {"now": NOW})
    connection.execute(text("INSERT INTO credit_ledger (id, workspace_id, delta, reason, reference, created_at) "
                            "VALUES ('l1', 'w1', 5, 'subscription', 'payment:o1', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO system_settings (key, value) VALUES ('storage_dir', '\"instance/media\"')"))

def rows(table, key):
    with engine.connect() as connection:
        return sorted((dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {table}"))),
                      key=lambda row: str(row[key]))

KEPT = (("payment_orders", "id"), ("payment_provider_configs", "provider"), ("credit_ledger", "id"),
        ("system_settings", "key"), ("users", "id"), ("workspaces", "id"))
before = {table: rows(table, key) for table, key in KEPT}
command.upgrade(config, "head")
inspector = inspect(engine)
for table in ("system_config", "system_config_audit", "payment_order_events"):
    assert inspector.has_table(table), table
assert "transfer_reported_at" in {c["name"] for c in inspector.get_columns("payment_orders")}
for table, key in KEPT:
    kept = [{name: row[name] for name in old} for row, old in zip(rows(table, key), before[table])]
    assert kept == before[table], table
assert all(row["transfer_reported_at"] is None for row in rows("payment_orders", "id"))
with engine.begin() as connection:
    connection.execute(text("INSERT INTO system_config (key, value, updated_at) VALUES ('credits.video_per_clip', '12', :now)"),
                       {"now": NOW})
    connection.execute(text("INSERT INTO system_config (key, ciphertext, updated_at) VALUES ('ai.gemini.api_key', 'gAAAA', :now)"),
                       {"now": NOW})
    connection.execute(text("INSERT INTO payment_order_events (order_id, action, user_id, amount_vnd, created_at) "
                            "VALUES ('o1', 'confirmed', 'u1', 30000, :now)"), {"now": NOW})
for bad in ("INSERT INTO system_config (key, value, ciphertext, updated_at) VALUES ('a', '1', 'x', '2026-09-01')",
            "INSERT INTO system_config (key, updated_at) VALUES ('b', '2026-09-01')",
            "INSERT INTO system_config_audit (section, action, created_at, metadata_json) VALUES ('ai', 'deleted', '2026-09-01', '{}')",
            "INSERT INTO payment_order_events (order_id, action, created_at) VALUES ('o1', 'refunded', '2026-09-01')"):
    try:
        with engine.begin() as connection:
            connection.execute(text(bad))
        raise AssertionError(f"the database accepted: {bad}")
    except AssertionError:
        raise
    except Exception as exc:
        assert any(word in str(exc).lower() for word in ("check", "constraint")), exc

command.downgrade(config, "0018_admin_payment_config")
inspector = inspect(engine)
for table in ("system_config", "system_config_audit", "payment_order_events"):
    assert not inspector.has_table(table), table
assert "transfer_reported_at" not in {c["name"] for c in inspector.get_columns("payment_orders")}
for table, key in KEPT:
    assert rows(table, key) == before[table], table
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


class Phase20MigrationTest(unittest.TestCase):
    def test_sqlite_upgrade_from_0018_and_downgrade_keep_data(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn("ok", result.stdout)

    def test_postgresql_upgrade_from_0018_and_downgrade_keep_data(self):
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
