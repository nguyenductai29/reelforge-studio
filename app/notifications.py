"""Durable in-app notifications (Phase 18B).

A notification belongs to one user. Workspace events go to the members of that
workspace (payments and credits to its owners only); system events go to active
system admins. They are written in the same transaction as the state change that
causes them, so a rolled-back transition notifies nobody.

Machine-generated notifications carry a ``dedupe_key`` and are inserted with
``ON CONFLICT DO NOTHING`` on ``(user_id, dedupe_key)``: a webhook delivered twice, a
second worker pass or a retried request adds nothing and never fails the caller.

``type`` names the event (see ``TYPES``); the frontend renders a localized title
and message from it and ``payload`` (safe display parameters only: names, counts,
IDs, never provider data). ``title`` and ``message`` are an English fallback.

The integer ``id`` increases with every notification; the SSE stream
(``GET /api/notifications/stream``) uses it as the event id a client resumes from.
"""
from datetime import datetime, timezone
import json
import os
from typing import Any, Iterable

from sqlalchemy import func, inspect, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Membership, Notification, User, Workflow, WorkflowRunStep
from app import system_config

TYPES = (
    "run.completed", "run.failed", "run.needs_attention", "run.awaiting_review",
    "publish.scheduled", "publish.succeeded", "publish.failed", "publish.needs_attention",
    "payment.succeeded", "payment.failed", "payment.unapplied", "payment.transfer_reported",
    "credits.low", "credits.adjusted",
    "storage.warning", "storage.critical", "storage.full",
    "support.new", "support.reply", "support.status",
)
RUN_STATUSES = {"completed": "run.completed", "failed": "run.failed", "needs_attention": "run.needs_attention",
                "awaiting_review": "run.awaiting_review"}
STORAGE_LEVELS = ((100, "full"), (90, "critical"), (80, "warning"))
_PARAM_LIMIT = 12

_ready = False


def ready(db) -> bool:
    """Whether the notifications table exists (migration 0017). Code running against an older database
    (a deployment not migrated yet, or the tests of older migrations) simply notifies nobody."""
    global _ready
    if not _ready:
        _ready = inspect(db.connection()).has_table("notifications")
    return _ready


def _params(params: dict[str, Any] | None) -> str | None:
    if not params:
        return None
    safe = {}
    for key, value in list(params.items())[:_PARAM_LIMIT]:
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            safe[str(key)[:40]] = value
        elif isinstance(value, str):
            safe[str(key)[:40]] = value[:200]
    return json.dumps(safe, ensure_ascii=False) if safe else None


def notify(db, user_ids: Iterable[str], type_: str, title: str, message: str = "", *, workspace_id: str | None = None,
           link: str | None = None, params: dict[str, Any] | None = None, dedupe: str | None = None) -> int:
    """Add one notification per user; with ``dedupe``, at most one per user ever. Returns the users targeted."""
    users = list(dict.fromkeys(user_id for user_id in user_ids if user_id))
    if not users or not ready(db):
        return 0
    now = datetime.now(timezone.utc)
    rows = [{"user_id": user_id, "workspace_id": workspace_id, "type": type_[:40], "title": title[:200],
             "message": message[:2000], "link": link[:500] if link else None, "payload": _params(params),
             "dedupe_key": dedupe[:160] if dedupe else None, "created_at": now} for user_id in users]
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(Notification).values(rows)
    elif dialect == "sqlite":
        statement = sqlite_insert(Notification).values(rows)
    else:
        raise NotImplementedError(f"Notifications are unsupported on {dialect}")
    if dedupe:
        statement = statement.on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
    db.execute(statement)
    return len(users)


def members(db, workspace_id: str, *, owners_only: bool = False) -> list[str]:
    query = (select(Membership.user_id).join(User, User.id == Membership.user_id)
             .where(Membership.workspace_id == workspace_id, User.is_active.is_(True)))
    if owners_only:
        query = query.where(Membership.role == "owner")
    return list(db.scalars(query))


def admins(db) -> list[str]:
    return list(db.scalars(select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True))))


def notify_workspace(db, workspace_id: str, type_: str, title: str, message: str = "", *, owners_only: bool = False,
                     **options) -> int:
    if not ready(db):
        return 0
    return notify(db, members(db, workspace_id, owners_only=owners_only), type_, title, message,
                  workspace_id=workspace_id, **options)


def notify_admins(db, type_: str, title: str, message: str = "", **options) -> int:
    if not ready(db):
        return 0
    return notify(db, admins(db), type_, title, message, **options)


# --- reading -----------------------------------------------------------------------------------------------

def visible(user_id: str):
    """A user's notifications from studios they still belong to, plus their personal and admin ones."""
    return (Notification.user_id == user_id) & or_(
        Notification.workspace_id.is_(None),
        Notification.workspace_id.in_(select(Membership.workspace_id).where(Membership.user_id == user_id)))


def public(row: Notification) -> dict[str, Any]:
    try:
        params = json.loads(row.payload) if row.payload else {}
    except ValueError:
        params = {}
    return {"id": row.id, "type": row.type, "title": row.title, "message": row.message, "link": row.link,
            "params": params, "workspace_id": row.workspace_id,
            "read_at": row.read_at.isoformat() if row.read_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def unread_count(db, user_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(Notification)
                         .where(visible(user_id), Notification.read_at.is_(None))) or 0)


def newer_than(db, user_id: str, last_id: int, limit: int = 50) -> list[Notification]:
    return list(db.scalars(select(Notification).where(visible(user_id), Notification.id > last_id)
                           .order_by(Notification.id).limit(limit)))


def latest_id(db, user_id: str) -> int:
    return int(db.scalar(select(func.max(Notification.id)).where(Notification.user_id == user_id)) or 0)


def mark_read(db, user_id: str, notification_id: int | None = None) -> int:
    query = update(Notification).where(visible(user_id), Notification.read_at.is_(None))
    if notification_id is not None:
        query = query.where(Notification.id == notification_id)
    return db.execute(query.values(read_at=datetime.now(timezone.utc)).execution_options(
        synchronize_session=False)).rowcount


# --- events ------------------------------------------------------------------------------------------------

def run_status_changed(db, run, previous: str, status: str) -> None:
    """A run reached an outcome the user should hear about: completed, failed, needs attention, or review."""
    type_ = RUN_STATUSES.get(status)
    if type_ is None or status == previous:
        return
    steps = list(db.execute(select(WorkflowRunStep.node_type, WorkflowRunStep.status)
                            .where(WorkflowRunStep.run_id == run.id).order_by(WorkflowRunStep.position)))
    focus = next((node for node, state in steps if state == status), None) if status in ("failed", "needs_attention") \
        else None
    rendered = any(node == "render" and state == "completed" for node, state in steps)
    workflow = db.scalar(select(Workflow.name).where(Workflow.id == run.workflow_id)) or ""
    titles = {"run.completed": "Workflow completed", "run.failed": "Render failed" if focus == "render" else
              "Workflow failed", "run.needs_attention": "Workflow needs attention",
              "run.awaiting_review": "Video ready for review" if rendered else "Waiting for your review"}
    notify_workspace(db, run.workspace_id, type_, titles[type_], workflow,
                     link=f"/workflows/{run.workflow_id}?run={run.id}",
                     params={"workflow": workflow, "node_type": focus, "rendered": rendered, "run_id": run.id},
                     dedupe=f"run:{run.id}:{status}")


def publication_changed(db, publication, event: str, *, reference: str = "") -> None:
    """``event``: scheduled, succeeded, failed or needs_attention."""
    titles = {"scheduled": "Post scheduled", "succeeded": "Upload succeeded", "failed": "Publishing failed",
              "needs_attention": "Publishing needs attention"}
    if event not in titles:
        return
    params = {"channel": publication.channel, "title": publication.title,
              "scheduled_for": publication.scheduled_for.isoformat() if publication.scheduled_for else None}
    notify_workspace(db, publication.workspace_id, f"publish.{event}", titles[event],
                     f"{publication.channel}: {publication.title}", link="/publishing", params=params,
                     dedupe=f"publication:{publication.id}:{event}:{reference}")


def payment_settled(db, order, status: str) -> None:
    """A payment order became paid, paid_unapplied or failed."""
    params = {"plan": order.plan_code, "amount": order.amount_vnd, "provider": order.provider}
    if status == "paid":
        notify_workspace(db, order.workspace_id, "payment.succeeded", "Payment completed",
                         f"{order.plan_code.upper()} plan is active.", owners_only=True, link="/billing",
                         params=params, dedupe=f"payment:{order.id}:paid")
    elif status == "failed":
        notify_workspace(db, order.workspace_id, "payment.failed", "Payment failed",
                         f"{order.plan_code.upper()} plan was not paid.", owners_only=True, link="/billing",
                         params=params, dedupe=f"payment:{order.id}:failed")
    elif status == "paid_unapplied":
        notify_admins(db, "payment.unapplied", "Payment needs review",
                      "A paid order could not be applied to its subscription.", link="/admin?tab=payments",
                      params={**params, "order_id": order.id}, dedupe=f"payment:{order.id}:unapplied")


def transfer_reported(db, order, content: str) -> None:
    """A buyer says a manual VietQR transfer was made: system admins check the bank and confirm or reject it."""
    notify_admins(db, "payment.transfer_reported", "Bank transfer to confirm",
                  f"{order.amount_vnd} VND for the {order.plan_code.upper()} plan ({content}).",
                  link="/admin?tab=payments&review=1",
                  params={"plan": order.plan_code, "amount": order.amount_vnd, "reference": content,
                          "order_id": order.id}, dedupe=f"payment:{order.id}:transfer_reported")


def low_credit_threshold() -> int:
    raw = system_config.env("CREDITS_LOW_THRESHOLD").strip()
    try:
        return max(0, int(raw)) if raw else 20
    except ValueError:
        return 20


def credits_changed(db, workspace_id: str, before: int, after: int, reference: str, reason: str) -> None:
    """Admin adjustments are always told; any debit that crosses the low-balance threshold is told once."""
    if reason.startswith("admin: "):
        notify_workspace(db, workspace_id, "credits.adjusted", "Credits adjusted by an administrator",
                         f"{after - before:+d} credits", owners_only=True, link="/billing",
                         params={"delta": after - before, "balance": after, "reason": reason[len("admin: "):]},
                         dedupe=f"credits:{reference}")
        return
    threshold = low_credit_threshold()
    if threshold and after < before and before >= threshold > after:
        notify_workspace(db, workspace_id, "credits.low", "Credits running low", f"{after} credits left",
                         owners_only=True, link="/billing", params={"balance": after, "threshold": threshold},
                         dedupe=f"credits_low:{reference}")


def storage_crossed(db, workspace_id: str, before: int, after: int, quota: int) -> None:
    """Storage passed 80, 90 or 100 % of the quota with this write (at most once a day per level)."""
    if quota <= 0 or after <= before:
        return
    for threshold, level in STORAGE_LEVELS:
        if before * 100 < threshold * quota <= after * 100:
            day = datetime.now(timezone.utc).date().isoformat()
            notify_workspace(db, workspace_id, f"storage.{level}", f"Storage at {threshold}%",
                             f"{after} of {quota} bytes used", link="/settings?tab=storage",
                             params={"percent": threshold, "used": after, "quota": quota},
                             dedupe=f"storage:{workspace_id}:{level}:{day}")
            return

