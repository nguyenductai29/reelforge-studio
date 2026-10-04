"""Transactional, idempotent credit accounting for future provider jobs."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from app import notifications
from app.models import CreditAccount, CreditLedger, UsageEvent


def post_credit(db, workspace_id: str, delta: int, reason: str, reference: str) -> int:
    if not delta or len(reference) > 100:
        raise ValueError("Invalid credit adjustment")
    account = db.scalar(select(CreditAccount).where(CreditAccount.workspace_id == workspace_id).with_for_update())
    if account is None:
        raise ValueError("Credit account not found")
    if db.scalar(select(CreditLedger.id).where(CreditLedger.reference == reference)):
        return account.balance
    if not 0 <= account.balance + delta <= 2_000_000_000:
        raise ValueError("Insufficient credits or balance limit reached")
    before = account.balance
    account.balance += delta
    db.add(CreditLedger(id=str(uuid.uuid4()), workspace_id=workspace_id, delta=delta,
                        reason=reason, reference=reference, created_at=datetime.now(timezone.utc)))
    # An admin adjustment, or a debit that crosses the low-balance threshold, tells the studio's owners.
    notifications.credits_changed(db, workspace_id, before, account.balance, reference, reason)
    return account.balance


def consume(db, workspace_id: str, *, tool: str, units: int, credits: int, reference: str) -> int:
    """Call once per confirmed provider usage; the unique reference prevents double charging."""
    if units <= 0 or credits <= 0 or not tool or len(tool) > 80 or len(reference) > 90:
        raise ValueError("Invalid usage")
    if db.scalar(select(UsageEvent.id).where(UsageEvent.reference == reference)):
        account = db.get(CreditAccount, workspace_id)
        return account.balance
    balance = post_credit(db, workspace_id, -credits, "usage", "usage:" + reference)
    db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=workspace_id, tool=tool, units=units,
                      credits=credits, reference=reference, created_at=datetime.now(timezone.utc)))
    return balance
