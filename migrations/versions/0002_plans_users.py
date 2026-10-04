"""Plans, workspace subscriptions and account state.

Revision ID: 0002_plans_users
Revises: 0001_initial
"""
from datetime import datetime, timezone
import json
from alembic import op
import sqlalchemy as sa

revision = "0002_plans_users"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("plans",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("project_limit", sa.Integer(), nullable=True),
        sa.Column("workflow_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_credits", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table("subscriptions",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("plan_code", sa.String(20), sa.ForeignKey("plans.code"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    conn = op.get_bind()
    settings = sa.table("system_settings", sa.column("key", sa.String), sa.column("value", sa.Text))
    stored_trial = conn.execute(sa.select(settings.c.value).where(settings.c.key == "trial_project_limit")).scalar()
    trial_limit = int(json.loads(stored_trial)) if stored_trial else 2
    plans = sa.table("plans", sa.column("code", sa.String), sa.column("name", sa.String), sa.column("project_limit", sa.Integer), sa.column("workflow_limit", sa.Integer), sa.column("monthly_credits", sa.Integer), sa.column("is_active", sa.Boolean))
    conn.execute(plans.insert(), [
        {"code": "trial", "name": "Trial", "project_limit": trial_limit, "workflow_limit": 2, "monthly_credits": 0, "is_active": True},
        {"code": "standard", "name": "Standard", "project_limit": 20, "workflow_limit": 10, "monthly_credits": 0, "is_active": True},
        {"code": "pro", "name": "Pro", "project_limit": None, "workflow_limit": None, "monthly_credits": 0, "is_active": True},
    ])
    workspaces = sa.table("workspaces", sa.column("id", sa.String), sa.column("plan", sa.String))
    subscriptions = sa.table("subscriptions", sa.column("workspace_id", sa.String), sa.column("plan_code", sa.String), sa.column("status", sa.String), sa.column("starts_at", sa.DateTime(timezone=True)), sa.column("ends_at", sa.DateTime(timezone=True)))
    now = datetime.now(timezone.utc)
    for ws_id, legacy_plan in conn.execute(sa.select(workspaces.c.id, workspaces.c.plan)):
        code = legacy_plan if legacy_plan in {"trial", "standard", "pro"} else "trial"
        conn.execute(subscriptions.insert().values(workspace_id=ws_id, plan_code=code, status="active", starts_at=now, ends_at=None))


def downgrade():
    op.drop_table("subscriptions")
    op.drop_table("plans")
    op.drop_column("users", "is_active")
