"""Database models for the application schema."""
import json
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    plan: Mapped[str] = mapped_column(String(20), default="trial")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    topic: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    bytes: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", name="fk_assets_project_id"), index=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id", name="fk_assets_run_id"), index=True, nullable=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_run_steps.id", name="fk_assets_step_id"), index=True, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    definition: Mapped[str] = mapped_column(Text, default='["idea","script","scenes","assets","voice","render","review","publish"]')


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class WorkspaceSetting(Base):
    __tablename__ = "workspace_settings"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Plan(Base):
    __tablename__ = "plans"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    project_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workflow_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_credits: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    price_vnd: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentOrder(Base):
    __tablename__ = "payment_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    provider: Mapped[str] = mapped_column(String(24))
    order_code: Mapped[int] = mapped_column(BigInteger, unique=True)
    amount_vnd: Mapped[int] = mapped_column(Integer)
    credits_award: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    units: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class AITool(Base):
    __tablename__ = "ai_tools"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(100))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    retry_of_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True)
    graph_snapshot: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowRunStep(Base):
    __tablename__ = "workflow_run_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(24))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowJob(Base):
    """One durable attemptable unit of work for a workflow run step."""

    __tablename__ = "workflow_jobs"
    __table_args__ = (
        CheckConstraint("state IN ('queued', 'leased', 'succeeded', 'failed')", name="ck_workflow_jobs_state"),
        CheckConstraint("attempt_count >= 0", name="ck_workflow_jobs_attempt_count"),
        UniqueConstraint("logical_key", name="uq_workflow_jobs_logical_key"),
        Index("ix_workflow_jobs_claim", "state", "available_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_run_steps.id"), index=True)
    logical_key: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="queued")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def payload(self) -> dict:
        """Return a fresh copy of the immutable enqueued JSON snapshot."""
        return json.loads(self.payload_json)


class CreditReconciliation(Base):
    """One final, append-only operator decision for a reserved paid job (migration 0012; 0011 keyed it by step)."""

    __tablename__ = "credit_reconciliations"
    __table_args__ = (
        CheckConstraint("decision IN ('confirmed_charge', 'refunded')", name="ck_reconciliation_decision"),
        CheckConstraint("credits > 0", name="ck_reconciliation_credits"),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("workflow_jobs.id"), primary_key=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_run_steps.id"), index=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("credit_ledger.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(24))
    credits: Mapped[int] = mapped_column(Integer)
    reconciled_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
