"""Apply confirmed payments once, with the subscription and credits in one transaction.

Every payment provider (``app/payment_providers``) settles through this module:
``settle`` takes a provider's evidence about one order, and ``apply_paid`` is the
only code that extends a subscription and awards credits. Settlement is
idempotent, and an order is settled only by evidence from the provider that
created it: a card callback can never pay a VietQR order, or the reverse.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models import PaymentOrder, Subscription, Workspace
from app import notifications
from app.usage import post_credit

# Statuses a confirmed payment may still settle. A manual VietQR order the buyer reported is
# awaiting_confirmation; one an admin rejected is rejected (the money may still turn up later).
OPEN_STATUSES = ("pending", "awaiting_confirmation", "expired", "failed", "cancelled", "rejected")
# Waiting for money or for an admin: counted as pending payments.
WAITING_STATUSES = ("pending", "awaiting_confirmation")


def apply_paid(db, order_code: int, amount: int, reference: str = "", provider: str = "payos") -> str:
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == order_code).with_for_update())
    if not order:
        return "unknown"
    if order.status in ("paid", "paid_unapplied"):
        return order.status
    if order.status not in OPEN_STATUSES or order.provider != provider:
        return order.status
    if amount != order.amount_vnd:
        raise ValueError("Payment amount mismatch")
    now = datetime.now(timezone.utc)
    order.provider_reference = reference[:128]
    order.paid_at = now
    subscription = db.scalar(select(Subscription).where(Subscription.workspace_id == order.workspace_id).with_for_update())
    if not subscription or subscription.starts_at.replace(tzinfo=timezone.utc) > order.created_at.replace(tzinfo=timezone.utc):
        order.status = "paid_unapplied"
        notifications.payment_settled(db, order, order.status)
        return order.status
    previous_plan = subscription.plan_code
    old_end = subscription.ends_at.replace(tzinfo=timezone.utc) if subscription.ends_at else None
    subscription.plan_code = order.plan_code
    subscription.status = "active"
    subscription.starts_at = now
    subscription.ends_at = (max(now, old_end) if previous_plan == order.plan_code and old_end else now) + timedelta(days=30)
    db.get(Workspace, order.workspace_id).plan = order.plan_code
    if order.credits_award:
        post_credit(db, order.workspace_id, order.credits_award, "subscription", f"payment:{order.id}")
    order.status = "paid"
    notifications.payment_settled(db, order, order.status)
    return order.status


def settle(db, provider: str, evidence) -> str:
    """Apply one provider's evidence (``payment_providers.Evidence``) to its order; returns the order status.

    Paid evidence goes through ``apply_paid`` (it checks the provider and the
    exact amount). A failed, cancelled or expired result only closes a pending
    order of the same provider; a later payment can still settle it.
    """
    if evidence.status == "paid":
        if evidence.amount_vnd is None:
            raise ValueError("Payment amount missing")
        return apply_paid(db, evidence.order_code, evidence.amount_vnd, evidence.reference, provider=provider)
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == evidence.order_code).with_for_update())
    if not order:
        return "unknown"
    if order.provider == provider and order.status == "pending" and evidence.status in ("failed", "cancelled", "expired"):
        order.status = evidence.status
        notifications.payment_settled(db, order, order.status)
    return order.status
