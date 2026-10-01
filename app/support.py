"""Customer support tickets (Phase 18C).

A ticket belongs to one workspace. Its creator sees it, and so does the workspace
owner; system admins see every ticket. Messages are append-only. There are no
internal admin notes, so nothing hidden can leak to the user.

A ticket may point at a run, project, payment order or publication of its own
workspace. Only the IDs are stored, after checking that they belong to that
workspace; nothing is copied from them (no provider data, logs or job payloads).

Every user message notifies the system admins; every admin message or status
change notifies the ticket's creator, through ``app.notifications``.
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy import select

from app import notifications
from app.models import PaymentOrder, Project, SupportMessage, SupportTicket, User, WorkflowRun

CATEGORIES = ("billing", "credits", "generation", "publishing", "account", "storage", "bug", "other")
STATUSES = ("open", "waiting_support", "waiting_user", "resolved", "closed")
PRIORITIES = ("normal", "high")
# Tickets an admin still has to answer.
AWAITING_SUPPORT = ("open", "waiting_support")


class SupportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(text: str, limit: int, field: str) -> str:
    value = (text or "").strip()
    if not value:
        raise SupportError(f"invalid_{field}", f"{field} is required")
    if len(value) > limit:
        raise SupportError(f"invalid_{field}", f"{field} is too long")
    return value


def check_context(db, workspace_id: str, context: dict) -> dict:
    """Keep only context IDs that name records of this workspace; a foreign or unknown ID is an error."""
    from app.publications import Publication

    models = {"run_id": WorkflowRun, "project_id": Project, "payment_order_id": PaymentOrder,
              "publication_id": Publication}
    checked = {}
    for key, model in models.items():
        value = context.get(key)
        if not value:
            continue
        if db.scalar(select(model.id).where(model.id == value, model.workspace_id == workspace_id)) is None:
            raise SupportError("invalid_context", f"{key} does not belong to this studio")
        checked[key] = value
    return checked


def create_ticket(db, *, workspace_id: str, user: User, subject: str, category: str, body: str,
                  context: dict | None = None) -> SupportTicket:
    if category not in CATEGORIES:
        raise SupportError("invalid_category", "Unknown category")
    now = _now()
    ticket = SupportTicket(id=str(uuid.uuid4()), workspace_id=workspace_id, created_by_user_id=user.id,
                           subject=_clean(subject, 200, "subject"), category=category, status="waiting_support",
                           priority="normal", created_at=now, updated_at=now,
                           **check_context(db, workspace_id, context or {}))
    db.add(ticket)
    db.flush()
    db.add(SupportMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, author_user_id=user.id, author_type="user",
                          body=_clean(body, 5000, "body"), created_at=now))
    notifications.notify_admins(db, "support.new", "New support request", ticket.subject,
                                link=f"/admin?tab=support&ticket={ticket.id}",
                                params={"subject": ticket.subject, "category": category, "email": user.email},
                                dedupe=f"support:{ticket.id}:new")
    return ticket


def add_message(db, ticket: SupportTicket, user: User, body: str, *, as_admin: bool) -> SupportMessage:
    """Append a message. A user's reply reopens a resolved ticket for support; a closed ticket takes no reply."""
    if ticket.status == "closed":
        raise SupportError("ticket_closed", "This ticket is closed")
    now = _now()
    message = SupportMessage(id=str(uuid.uuid4()), ticket_id=ticket.id, author_user_id=user.id,
                             author_type="admin" if as_admin else "user", body=_clean(body, 5000, "body"),
                             created_at=now)
    db.add(message)
    ticket.updated_at = now
    if as_admin:
        if ticket.status != "resolved":
            ticket.status = "waiting_user"
        notifications.notify(db, [ticket.created_by_user_id], "support.reply", "Support replied to your request",
                             ticket.subject, workspace_id=ticket.workspace_id, link=f"/support/{ticket.id}",
                             params={"subject": ticket.subject}, dedupe=f"support:{message.id}")
    else:
        ticket.status = "waiting_support"
        notifications.notify_admins(db, "support.reply", "New reply on a support request", ticket.subject,
                                    link=f"/admin?tab=support&ticket={ticket.id}",
                                    params={"subject": ticket.subject, "email": user.email},
                                    dedupe=f"support:{message.id}")
    return message


def set_status(db, ticket: SupportTicket, status: str, *, by_admin: bool) -> None:
    if status not in STATUSES:
        raise SupportError("invalid_status", "Unknown status")
    if status == ticket.status:
        return
    now = _now()
    ticket.status, ticket.updated_at = status, now
    ticket.closed_at = now if status == "closed" else None
    if by_admin:
        notifications.notify(db, [ticket.created_by_user_id], "support.status", "Your support request was updated",
                             ticket.subject, workspace_id=ticket.workspace_id, link=f"/support/{ticket.id}",
                             params={"subject": ticket.subject, "status": status},
                             dedupe=f"support:{ticket.id}:status:{status}:{now.isoformat()}")


def set_priority(ticket: SupportTicket, priority: str) -> None:
    if priority not in PRIORITIES:
        raise SupportError("invalid_priority", "Unknown priority")
    ticket.priority = priority


def ticket_data(ticket: SupportTicket, *, creator_email: str | None = None, workspace_name: str | None = None,
                messages: int | None = None) -> dict:
    iso = lambda value: value.isoformat() if value else None  # noqa: E731
    return {"id": ticket.id, "subject": ticket.subject, "category": ticket.category, "status": ticket.status,
            "priority": ticket.priority, "workspace_id": ticket.workspace_id, "workspace_name": workspace_name,
            "created_by_email": creator_email, "messages": messages,
            "context": {key: getattr(ticket, key) for key in ("run_id", "project_id", "payment_order_id",
                                                               "publication_id") if getattr(ticket, key)},
            "created_at": iso(ticket.created_at), "updated_at": iso(ticket.updated_at),
            "closed_at": iso(ticket.closed_at)}


def message_data(message: SupportMessage, author_email: str | None = None) -> dict:
    return {"id": message.id, "author_type": message.author_type, "author_email": author_email,
            "body": message.body, "created_at": message.created_at.isoformat() if message.created_at else None}
