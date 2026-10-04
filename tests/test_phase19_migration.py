"""Migration 0018: admin-managed payment gateway configuration and its audit trail, from 0017 and back.

Existing payment orders, subscriptions, credit ledger entries and the provider activity record must
survive both directions. Runs on a disposable SQLite database; set ``REELFORGE_TEST_DATABASE_URL`` to an
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
command.upgrade(config, "0017_notify_support_verify")
NOW = "2026-09-01 00:00:00"
with engine.begin() as connection:
    connection.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) "
                            "VALUES ('u1', 'owner@example.com', 'x', true, true)"))
    connection.execute(text("INSERT INTO workspaces (id, name, owner_id, plan, created_at) "
                            "VALUES ('w1', 'Studio', 'u1', 'standard', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO subscriptions (workspace_id, plan_code, status, starts_at, ends_at) "
                            "VALUES ('w1', 'standard', 'active', :now, :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO payment_orders (id, workspace_id, plan_code, provider, order_code, amount_vnd, "
                            "credits_award, status, provider_reference, created_at, paid_at) VALUES "
                            "('o1', 'w1', 'standard', 'payos', 1234567890123, 30000, 5, 'paid', 'REF', :now, :now), "
                            "('o2', 'w1', 'standard', 'onepay', 1234567890124, 30000, 5, 'pending', NULL, :now, NULL)"),
                       {"now": NOW})
    connection.execute(text("INSERT INTO credit_ledger (id, workspace_id, delta, reason, reference, created_at) "
                            "VALUES ('l1', 'w1', 5, 'subscription', 'payment:o1', :now)"), {"now": NOW})
    connection.execute(text("INSERT INTO system_settings (key, value) VALUES ('payment_activity', :value)"),
                       {"value": json.dumps({"payos": {"webhook": "2026-09-01T00:00:00+00:00"}})})

def rows(table, key):
    with engine.connect() as connection:
        return sorted((dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {table}"))),
                      key=lambda row: str(row[key]))

KEPT = (("payment_orders", "id"), ("subscriptions", "workspace_id"), ("credit_ledger", "id"),
        ("system_settings", "key"), ("plans", "code"), ("users", "id"))
before = {table: rows(table, key) for table, key in KEPT}
command.upgrade(config, "head")
inspector = inspect(engine)
assert inspector.has_table("payment_provider_configs") and inspector.has_table("payment_config_audit")
assert "config_ciphertext" in {c["name"] for c in inspector.get_columns("payment_provider_configs")}
# No secret has a column of its own.
for column in inspector.get_columns("payment_provider_configs"):
    assert not any(word in column["name"] for word in ("key", "secret", "password", "access")), column["name"]
for table, key in KEPT:
    # Columns later migrations add (e.g. 0019's transfer_reported_at) are not part of this comparison.
    assert [{name: row[name] for name in old} for row, old in zip(rows(table, key), before[table])] == before[table], table
assert {"ix_payment_config_audit_provider"} <= {i["name"] for i in inspector.get_indexes("payment_config_audit")}

with engine.begin() as connection:
    connection.execute(text("INSERT INTO payment_provider_configs (provider, enabled, mode, config_ciphertext, created_at, "
                            "updated_at, updated_by_user_id) VALUES ('onepay', true, 'sandbox', 'gAAAA', :now, :now, 'u1')"),
                       {"now": NOW})
    connection.execute(text("INSERT INTO payment_config_audit (provider, action, admin_user_id, created_at, metadata_json) "
                            "VALUES ('onepay', 'created', 'u1', :now, '{\"changed\": [\"hash_key\"]}')"), {"now": NOW})
for bad in ("INSERT INTO payment_provider_configs (provider, enabled, created_at, updated_at) "
            "VALUES ('onepay', false, '2026-09-01', '2026-09-01')",
            "INSERT INTO payment_provider_configs (provider, enabled, created_at, updated_at) "
            "VALUES ('stripe', true, '2026-09-01', '2026-09-01')",
            "INSERT INTO payment_provider_configs (provider, enabled, mode, created_at, updated_at) "
            "VALUES ('payos', true, 'live', '2026-09-01', '2026-09-01')",
            "INSERT INTO payment_config_audit (provider, action, created_at, metadata_json) "
            "VALUES ('payos', 'deleted', '2026-09-01', '{}')"):
    try:
        with engine.begin() as connection:
            connection.execute(text(bad))
        raise AssertionError(f"the database accepted: {bad}")
    except AssertionError:
        raise
    except Exception as exc:
        assert any(word in str(exc).lower() for word in ("unique", "check", "constraint", "duplicate")), exc

command.downgrade(config, "0017_notify_support_verify")
inspector = inspect(engine)
assert not inspector.has_table("payment_provider_configs") and not inspector.has_table("payment_config_audit")
for table, key in KEPT:
    # Columns later migrations add (e.g. 0019's transfer_reported_at) are not part of this comparison.
    assert [{name: row[name] for name in old} for row, old in zip(rows(table, key), before[table])] == before[table], table
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


class Phase19MigrationTest(unittest.TestCase):
    def test_sqlite_upgrade_from_0017_and_downgrade_keep_payment_records(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn("ok", result.stdout)

    def test_postgresql_upgrade_from_0017_and_downgrade_keep_payment_records(self):
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
