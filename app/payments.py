"""Apply confirmed payments once, with the subscription and credits in one transaction."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models import PaymentOrder, Subscription, Workspace
from app.usage import post_credit


def apply_paid(db, order_code: int, amount: int, reference: str = "") -> str:
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_code == order_code).with_for_update())
    if not order:
        return "unknown"
    if order.status in ("paid", "paid_unapplied"):
        return order.status
    if order.status not in ("pending", "expired", "failed", "cancelled") or order.provider != "payos":
        return order.status
    if amount != order.amount_vnd:
        raise ValueError("Payment amount mismatch")
    now = datetime.now(timezone.utc)
    order.provider_reference = reference[:128]
    order.paid_at = now
    subscription = db.scalar(select(Subscription).where(Subscription.workspace_id == order.workspace_id).with_for_update())
    if not subscription or subscription.starts_at.replace(tzinfo=timezone.utc) > order.created_at.replace(tzinfo=timezone.utc):
        order.status = "paid_unapplied"
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
    return order.status
