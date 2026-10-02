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
    # Migration 0022 (Phase 22, app/accounts.py). Accounts from before it count as verified. Deferred, like
    # every later column, so the API keeps reading users on a database not migrated yet.
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True, deferred=True)
    # The TOTP secret (and one being enrolled) as master-key ciphertext; never returned once enabled.
    totp_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    totp_pending_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True, deferred=True)
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True, deferred=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    # Migration 0023: the workspace a new session starts in.
    last_workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, deferred=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (Index("ix_login_sessions_id", "id", unique=True),)
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Migration 0022: a public id (sessions are listed and revoked by it, never by token), and where from.
    id: Mapped[str | None] = mapped_column(String(36), nullable=True, deferred=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, deferred=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, deferred=True)
    # Migration 0023: the workspace this session works in (checked against the memberships on every request).
    active_workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, deferred=True)


class UserProfile(Base):
    """Optional profile fields of a user (migration 0015); ``users`` itself is unchanged."""

    __tablename__ = "user_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(20), default="trial")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    # owner, admin, editor or viewer (app/permissions.py); exactly one owner per workspace.
    role: Mapped[str] = mapped_column(String(20), default="owner")
    # Migration 0023: when the member joined (null for members from before it).
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)


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
    # Declared as migration 0016 created it (the storage lifecycle scans by kind and age).
    __table_args__ = (Index("ix_assets_kind_created_at", "kind", "created_at"),)
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
    # Migration 0014: the source video a clip was cut from (Movie Recap source clips).
    # Deferred: only written (Extract Source Clips) and read on demand, so queries of
    # assets keep working on a database that has not reached migration 0014 yet.
    source_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Migration 0016 (storage lifecycle, app/storage.py): what the asset is, and when its file was removed.
    # An expired or deleted asset keeps its row with bytes 0; expired_bytes records what was freed.
    # Deferred like source_asset_id, so asset queries keep working before the migration runs.
    kind: Mapped[str | None] = mapped_column(String(24), nullable=True, deferred=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, deferred=True)
    expired_reason: Mapped[str | None] = mapped_column(String(24), nullable=True, deferred=True)
    expired_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True, deferred=True)


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
    # Migration 0016: media storage per workspace on this plan; null uses WORKSPACE_MEDIA_QUOTA_BYTES.
    storage_limit_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True, deferred=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentOrder(Base):
    """One checkout. ``provider`` is ``payos`` (VietQR) or ``onepay`` (card); ``order_code`` is the
    reference both providers know the order by, and ``provider_reference`` their transaction ID."""

    __tablename__ = "payment_orders"
    __table_args__ = (Index("ix_payment_orders_provider_status", "provider", "status"),)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Migration 0019: when the buyer said a manual VietQR transfer was made (manual orders only; deferred so
    # code running on an older schema never selects it).
    transfer_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True,
                                                                  deferred=True)


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
    # Migration 0006: one step per node of a run.
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_workflow_run_node"),)
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
    # Migration 0012 made job_id the primary key and kept its unique constraint.
    job_id: Mapped[str] = mapped_column(ForeignKey("workflow_jobs.id"), primary_key=True, unique=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_run_steps.id"), index=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("credit_ledger.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(24))
    credits: Mapped[int] = mapped_column(Integer)
    reconciled_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class WorkerHeartbeat(Base):
    """The last report of each worker process kind (migration 0014), for the admin health view."""

    __tablename__ = "worker_heartbeats"
    worker: Mapped[str] = mapped_column(String(64), primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    pid: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    """One in-app notification for one user (migration 0017, app/notifications.py)."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_read", "user_id", "read_at"),
    )
    # An increasing integer: it orders a user's notifications and is the SSE event id clients resume from.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SupportTicket(Base):
    """A customer support request of one workspace (migration 0017, app/support.py)."""

    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_workspace", "workspace_id", "updated_at"),
        Index("ix_support_tickets_status", "status", "updated_at"),
        Index("ix_support_tickets_creator", "created_by_user_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    subject: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    priority: Mapped[str] = mapped_column(String(12), default="normal")
    # Optional context the user attached: IDs only, checked to belong to the workspace.
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    publication_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportMessage(Base):
    """One message of a support ticket's thread; messages are never edited or deleted."""

    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_messages_ticket", "ticket_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id"))
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    author_type: Mapped[str] = mapped_column(String(8))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VerificationCheck(Base):
    """One item of the system admin's manual live-verification checklist (migration 0017).

    Migration 0025: ``status`` is passed, failed, not_applicable or None (not checked); ``verified_at`` and
    ``verified_by_user_id`` say when and by whom the current status was recorded. Only an admin sets them."""

    __tablename__ = "verification_checks"
    __table_args__ = (
        CheckConstraint("status IN ('passed', 'failed', 'not_applicable')", name="ck_verification_checks_status"),
    )
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)


class PaymentProviderConfig(Base):
    """A payment provider's admin-managed configuration (migration 0018); every credential is ciphertext."""

    __tablename__ = "payment_provider_configs"
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Fernet ciphertext of a JSON object (app/secret_box.py); null while credentials stay in bootstrap/env.
    config_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PaymentConfigAudit(Base):
    """Who changed or tested a payment provider's configuration; changed field names only, never values."""

    __tablename__ = "payment_config_audit"
    __table_args__ = (Index("ix_payment_config_audit_provider", "provider", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))
    admin_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class SystemConfig(Base):
    """One admin-managed setting (migration 0019): ``value`` is JSON for plain settings, ``ciphertext`` for secrets."""

    __tablename__ = "system_config"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class SystemConfigAudit(Base):
    """Who changed or tested system settings; setting names and statuses only, never values."""

    __tablename__ = "system_config_audit"
    __table_args__ = (Index("ix_system_config_audit_section", "section", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section: Mapped[str] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(16))
    admin_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class PaymentOrderEvent(Base):
    """A manual VietQR order's history: the buyer reported the transfer, an admin confirmed or rejected it."""

    __tablename__ = "payment_order_events"
    __table_args__ = (Index("ix_payment_order_events_order", "order_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id"))
    action: Mapped[str] = mapped_column(String(24))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    amount_vnd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountToken(Base):
    """A one-time token (migration 0022): verify_email, password_reset or login_challenge. Only its hash is kept."""

    __tablename__ = "account_tokens"
    __table_args__ = (Index("ix_account_tokens_user", "user_id", "purpose"),
                      Index("ix_account_tokens_expires", "expires_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The address a verification token was sent to: it verifies that address only.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RecoveryCode(Base):
    """A 2FA recovery code (migration 0022), hashed; ``used_at`` once it served."""

    __tablename__ = "recovery_codes"
    __table_args__ = (UniqueConstraint("user_id", "code_hash", name="uq_recovery_codes_user_code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailOutbox(Base):
    """A transactional email (migration 0022, app/mailer.py). Its parameters are encrypted and erased once sent."""

    __tablename__ = "email_outbox"
    __table_args__ = (Index("ix_email_outbox_due", "status", "next_attempt_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    to_address: Mapped[str] = mapped_column(String(320))
    template: Mapped[str] = mapped_column(String(40))
    locale: Mapped[str] = mapped_column(String(8))
    payload_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """A security or administration event (migration 0022, app/audit.py). Never a password, token or secret."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_action", "action", "created_at"),
        Index("ix_audit_events_actor", "actor_user_id", "created_at"),
        Index("ix_audit_events_workspace", "workspace_id", "created_at"),
        Index("ix_audit_events_created", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String(48))
    outcome: Mapped[str] = mapped_column(String(12))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")


class RateLimitBucket(Base):
    """One fixed-window counter (migration 0022, app/ratelimit.py), shared by every API process."""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (Index("ix_rate_limit_buckets_expires", "expires_at"),)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32))
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceInvite(Base):
    """An invitation to join a workspace (migration 0023, app/team.py); only the token's hash is kept."""

    __tablename__ = "workspace_invites"
    __table_args__ = (Index("ix_workspace_invites_workspace", "workspace_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"),
                                                            nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupRun(Base):
    """One database backup run (migration 0024, app/backup.py)."""

    __tablename__ = "backup_runs"
    __table_args__ = (Index("ix_backup_runs_started", "kind", "started_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    host: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SystemAlert(Base):
    """One alert condition's state (migration 0024, app/alerts.py): active or resolved, and when admins were told."""

    __tablename__ = "system_alerts"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    level: Mapped[str] = mapped_column(String(12))
    active: Mapped[bool] = mapped_column(Boolean)
    message: Mapped[str] = mapped_column(String(300))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
