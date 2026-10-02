"""Admin → Overview: the whole installation at a glance, for system administrators only (GET /api/admin/overview).

``summary`` reads what the console's other tabs show in detail with a handful of aggregate queries (COUNT, SUM,
MIN, GROUP BY, conditional CASE): no table is loaded whole. Every figure has one definition, and the periods come
back with the data (``periods``):

* **users**: every account; **active users**: active accounts that signed in during the last 30 days.
* **studios**: every workspace; **active subscriptions**: status active, with no end date or one still ahead;
  **on paid plans**: those of them on a plan with a price.
* **jobs**: workflow jobs created in the period, by queue (text, image, video, voice, source, render); publishing
  jobs count with publishing. Success rate (computed by the UI): succeeded / (succeeded + failed) of those jobs.
* **credits** (30 days, from the credit ledger): added by paid plans, adjusted by administrators (in and out),
  refunded for failed steps; **consumed**: confirmed provider usage (usage events); **available**: the sum of
  every studio's balance now; **held**: credits of paid jobs waiting for reconciliation now.
* **payments**: **paid** orders (paid, or paid but not applied) by their payment time in the 30 days, with their
  amount, the only money figure (nothing is estimated); orders waiting now; failed, rejected, cancelled or expired
  orders created in the 30 days.
* **publishing** (30 days): published (succeeded, by publishing time) and failed or needing attention (by last
  change); scheduled now. A channel shows when it is configured or was ever used.
* **storage**: media bytes now, the media disk, studios per warning level (one SQL aggregate).
* **support**: tickets waiting for support now (open, waiting_support) and for users, those of high priority, and
  since when the oldest user message is unanswered.
* **growth**: new users and new studios per UTC day over the 30 days.
* **system status**: the worst of the health rows and the live alert conditions (``app/alerts.py``: workers, media
  disk, backups, failed jobs, payment callbacks, master key, email), which the rows also follow, with the readiness
  checks of ``app/readiness.py``: the same rules as the alerts and Admin → Verification, never a second set.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select

from app import alerts, audit, heartbeat, mailer, readiness, reconciliation, render, storage
from app.models import (Asset, AuditEvent, CreditAccount, CreditLedger, PaymentOrder, Plan, Subscription,
                        SupportMessage, SupportTicket, UsageEvent, User, WorkflowJob, Workspace)
from app.publications import Publication

PERIOD_DAYS = 30
JOB_HOURS = 24
# Generation and render queues (a job's logical key starts with "<queue>:"); "source" fetches pages and transcribes.
QUEUES = ("text", "image", "video", "voice", "source", "render")
# A usage event's tool is "<provider>/<task>"; transcription runs in the source queue.
TASK_OF_TOOL = {"text": "text", "image": "image", "video": "video", "voice": "voice", "transcription": "source",
                "render": "render"}
CHANNELS = ("youtube", "tiktok", "facebook")
PAID = ("paid", "paid_unapplied")
WAITING = ("pending", "awaiting_confirmation", "paid_unapplied")
FAILED_ORDERS = ("failed", "rejected", "cancelled", "expired")
AWAITING_SUPPORT = ("open", "waiting_support")
OVERDUE_MINUTES = 10  # as jobs.stuck_jobs
RECENT_ACTIVITY = 8
SEVERITY = {"critical": 0, "warning": 1, "info": 2}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def _tally(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def worst(statuses) -> str:
    """``critical`` over ``warning`` over ``healthy``; ``not_configured`` (switched off on purpose) is healthy."""
    statuses = set(statuses)
    return "critical" if "critical" in statuses else "warning" if "warning" in statuses else "healthy"


# --- health ---------------------------------------------------------------------------------------------------

def _health(db, now: datetime, conditions: list) -> list[dict]:
    """The health rows, each from the readiness checks it summarizes, made critical or warning by a live alert
    condition of its own (Admin → Verification has every check)."""
    alerted = {condition.key: condition for condition in conditions}
    rows = [{"key": "api", "status": "healthy"}]

    checks = readiness.database(db)
    by_key = {check["key"]: check for check in checks}
    rows.append({"key": "database",
                 "status": "critical" if any(c["status"] == "error" for c in checks)
                 else "warning" if any(c["status"] == "warning" for c in checks) else "healthy",
                 "detail": next((c.get("detail") for c in checks if c["status"] != "ok"), None),
                 "dialect": by_key.get("connection", {}).get("dialect"),
                 "migration": by_key.get("migration", {}).get("current")})

    workers = heartbeat.worker_health(db, now)
    names = {state: [w["worker"] for w in workers if w["status"] == state] for state in ("stale", "error", "missing")}
    rows.append({"key": "workers",
                 "status": "critical" if names["stale"] else "warning" if names["error"] or names["missing"] else "healthy",
                 "healthy": sum(1 for w in workers if w["status"] == "ok"), "total": len(workers), **names})

    disk = storage.disk_usage(db)
    disk_alert = alerted.get("disk:media")
    rows.append({"key": "storage", "status": disk_alert.level if disk_alert else "warning" if disk is None else "healthy",
                 "detail": None if disk else "unavailable",
                 **({"percent": disk["percent"], "used_bytes": disk["used_bytes"], "total_bytes": disk["total_bytes"],
                     "free_bytes": disk["free_bytes"]} if disk else {})})

    backup_check = readiness.backup_checks(db, now)[0]
    backup_alert = alerted.get("backup:failed") or alerted.get("backup:overdue")
    never = backup_check.get("last_success") is None
    rows.append({"key": "backups",
                 "status": "critical" if backup_alert else "warning" if never or backup_check["status"] != "ok" else "healthy",
                 "detail": (backup_alert.key.split(":", 1)[1] if backup_alert else backup_check.get("detail")),
                 "last_success": backup_check.get("last_success"), "age_hours": backup_check.get("age_hours"),
                 "max_age_hours": backup_check.get("max_age_hours")})

    problem = mailer.problem()
    outbox = mailer.stats(db, since=now - timedelta(hours=24))
    rows.append({"key": "email",
                 "status": "not_configured" if problem == "disabled"
                 else "warning" if problem or outbox["recent_failed"] or "email:failures" in alerted else "healthy",
                 # failures: sends that failed for good in 24 h, or failing ones being retried (the alert).
                 "detail": problem or ("failures" if outbox["recent_failed"] or "email:failures" in alerted else None),
                 "failed_24h": outbox["recent_failed"], "queued": outbox["queued"]})

    # Errors are what will fail (an enabled model without its key, FFmpeg missing, a gateway that cannot be read);
    # warnings and missing optional setup are advisories, listed in Admin → Verification.
    configuration = [*readiness.ai_checks(db), *readiness.publishing_checks(), *readiness.payment_checks(db),
                     *readiness.security_checks(db), *readiness.configuration_checks(), *readiness.account_checks(db)]
    tools = render.tools_issue()
    errors = sum(1 for check in configuration if check["status"] == "error") + (1 if tools else 0)
    advisories = sum(1 for check in configuration if check["status"] in ("warning", "missing"))
    rows.append({"key": "configuration",
                 "status": "critical" if "master_key" in alerted else "warning" if errors else "healthy",
                 "errors": errors, "advisories": advisories})
    return rows


# --- figures --------------------------------------------------------------------------------------------------

def _overview(db, now: datetime, since: datetime) -> dict:
    users, active_users = db.execute(select(
        func.count(User.id), _tally(and_(User.is_active.is_(True), User.last_login_at >= since)))).one()
    current = and_(Subscription.status == "active", or_(Subscription.ends_at.is_(None), Subscription.ends_at > now))
    subscriptions, paid_plans = db.execute(
        select(func.count(Subscription.workspace_id), _tally(Plan.price_vnd > 0)).select_from(Subscription)
        .outerjoin(Plan, Plan.code == Subscription.plan_code).where(current)).one()
    return {"users": int(users), "active_users": int(active_users),
            "workspaces": int(db.scalar(select(func.count(Workspace.id))) or 0),
            "active_subscriptions": int(subscriptions), "paid_subscriptions": int(paid_plans)}


def _jobs(db, since: datetime, day: datetime) -> dict:
    queue = case(*((WorkflowJob.logical_key.startswith(f"{name}:"), name) for name in (*QUEUES, "publish")),
                 else_="other")
    recent = (select(queue.label("queue"), WorkflowJob.state.label("state"), WorkflowJob.created_at.label("created"))
              .where(WorkflowJob.created_at >= since).subquery())
    tasks = {name: {"task": name, "jobs": 0, "jobs_24h": 0, "succeeded": 0, "failed": 0, "active": 0, "credits": 0}
             for name in QUEUES}
    for name, state, count, last_day in db.execute(
            select(recent.c.queue, recent.c.state, func.count(), _tally(recent.c.created >= day))
            .group_by(recent.c.queue, recent.c.state)):
        if name not in tasks:
            continue
        entry = tasks[name]
        entry["jobs"] += int(count)
        entry["jobs_24h"] += int(last_day)
        if state == "succeeded":
            entry["succeeded"] += int(count)
        elif state == "failed":
            entry["failed"] += int(count)
        else:
            entry["active"] += int(count)
    consumed = 0
    for tool, credits in db.execute(select(UsageEvent.tool, func.coalesce(func.sum(UsageEvent.credits), 0))
                                    .where(UsageEvent.created_at >= since).group_by(UsageEvent.tool)):
        consumed += int(credits)
        task = TASK_OF_TOOL.get(tool.rsplit("/", 1)[-1])
        if task:
            tasks[task]["credits"] += int(credits)
    listed = list(tasks.values())
    # The credits total is every usage event of the period, as Credits → Consumed (a tool of no task included).
    return {"tasks": listed, "credits": consumed,
            **{key: sum(entry[key] for entry in listed) for key in ("jobs", "jobs_24h", "succeeded", "failed", "active")}}


def _credits(db, since: datetime, consumed: int) -> dict:
    reason = CreditLedger.reason
    kind = case((reason == "subscription", "plans"), (reason.startswith("admin:"), "admin"),
                (reason.endswith("_refund", autoescape=True), "refunds"),
                (or_(reason.endswith("_reserve", autoescape=True), reason == "usage"), "charges"), else_="other")
    recent = select(kind.label("kind"), CreditLedger.delta.label("delta")).where(CreditLedger.created_at >= since).subquery()
    moved = {name: (int(added), int(removed), int(count)) for name, added, removed, count in db.execute(
        select(recent.c.kind, func.coalesce(func.sum(case((recent.c.delta > 0, recent.c.delta), else_=0)), 0),
               func.coalesce(func.sum(case((recent.c.delta < 0, -recent.c.delta), else_=0)), 0), func.count())
        .group_by(recent.c.kind))}

    def part(name: str, index: int) -> int:
        return moved.get(name, (0, 0, 0))[index]

    held = reconciliation.pending_summary(db)
    return {"added_by_plans": part("plans", 0), "admin_added": part("admin", 0), "admin_removed": part("admin", 1),
            "admin_adjustments": part("admin", 2), "refunded": part("refunds", 0), "other_added": part("other", 0),
            "consumed": consumed,
            "available": int(db.scalar(select(func.coalesce(func.sum(CreditAccount.balance), 0))) or 0),
            "held": held["credits"], "held_jobs": held["count"]}


def _payments(db, since: datetime) -> dict:
    by_provider = [{"provider": provider, "paid": int(count), "amount_vnd": int(amount)} for provider, count, amount in
                   db.execute(select(PaymentOrder.provider, func.count(), func.coalesce(func.sum(PaymentOrder.amount_vnd), 0))
                              .where(PaymentOrder.status.in_(PAID), PaymentOrder.paid_at >= since)
                              .group_by(PaymentOrder.provider).order_by(PaymentOrder.provider))]
    waiting = dict(db.execute(select(PaymentOrder.status, func.count()).where(PaymentOrder.status.in_(WAITING))
                              .group_by(PaymentOrder.status)).all())
    failed = db.scalar(select(func.count()).select_from(PaymentOrder)
                       .where(PaymentOrder.status.in_(FAILED_ORDERS), PaymentOrder.created_at >= since)) or 0
    return {"paid": sum(item["paid"] for item in by_provider),
            "paid_amount_vnd": sum(item["amount_vnd"] for item in by_provider), "by_provider": by_provider,
            **{status: int(waiting.get(status, 0)) for status in WAITING}, "failed": int(failed)}


def _publishing(db, since: datetime) -> dict:
    when = func.coalesce(Publication.published_at, Publication.finished_at, Publication.updated_at)
    counts = {row.channel: row for row in db.execute(
        select(Publication.channel, _tally((Publication.state == "succeeded") & (when >= since)).label("published"),
               _tally(Publication.state == "scheduled").label("scheduled"),
               _tally(Publication.state.in_(("failed", "needs_attention")) & (Publication.updated_at >= since))
               .label("failed"), func.count().label("total"))
        .group_by(Publication.channel))}
    configured = {check["key"]: check["status"] != "missing" for check in readiness.publishing_checks()}
    channels = []
    for name in CHANNELS:
        row = counts.get(name)
        if not configured.get(name) and row is None:
            continue  # never set up and never used: no metric
        channels.append({"channel": name, "configured": bool(configured.get(name)),
                         **{key: int(getattr(row, key)) if row is not None else 0
                            for key in ("published", "scheduled", "failed")}})
    return {"channels": channels, **{key: sum(item[key] for item in channels)
                                     for key in ("published", "scheduled", "failed")}}


def _storage(db) -> dict:
    stored, files = db.execute(select(func.coalesce(func.sum(Asset.bytes), 0), _tally(Asset.bytes > 0))).one()
    disk = storage.disk_usage(db)
    return {"stored_bytes": int(stored), "files": int(files),
            "disk": {key: disk[key] for key in ("total_bytes", "used_bytes", "free_bytes", "percent")} if disk else None,
            "levels": storage.level_counts(db)}


def _support(db) -> dict:
    awaiting = SupportTicket.status.in_(AWAITING_SUPPORT)
    open_now, waiting_user, high = db.execute(select(
        _tally(awaiting), _tally(SupportTicket.status == "waiting_user"),
        _tally(awaiting & (SupportTicket.priority == "high")))).one()
    # Since when the oldest ticket waiting for support has its last user message unanswered.
    last_user = (select(SupportMessage.ticket_id, func.max(SupportMessage.created_at).label("at"))
                 .join(SupportTicket, SupportTicket.id == SupportMessage.ticket_id)
                 .where(awaiting, SupportMessage.author_type == "user").group_by(SupportMessage.ticket_id).subquery())
    oldest = db.scalar(select(func.min(last_user.c.at)))
    return {"awaiting_support": int(open_now), "waiting_user": int(waiting_user), "high_priority": int(high),
            "oldest_waiting_since": _iso(oldest)}


def _growth(db, start: datetime, days: int) -> dict:
    """New users and studios per UTC day, ``days`` of them ending today."""
    postgres = db.get_bind().dialect.name == "postgresql"

    def per_day(column):
        day = func.date(func.timezone("UTC", column)) if postgres else func.date(column)
        rows = select(day.label("day")).where(column >= start).subquery()
        return {str(value)[:10]: int(count) for value, count in
                db.execute(select(rows.c.day, func.count()).group_by(rows.c.day)) if value is not None}

    labels = [(start + timedelta(days=offset)).date().isoformat() for offset in range(days)]
    users, studios = per_day(User.created_at), per_day(Workspace.created_at)
    return {"days": labels, "users": [users.get(day, 0) for day in labels],
            "workspaces": [studios.get(day, 0) for day in labels]}


def _activity(db) -> list[dict]:
    """The latest administrator actions (Admin → Audit log has them all, with their details)."""
    if not audit.ready(db):
        return []
    rows = db.execute(select(AuditEvent.id, AuditEvent.action, AuditEvent.outcome, AuditEvent.created_at, User.email)
                      .outerjoin(User, User.id == AuditEvent.actor_user_id)
                      .where(AuditEvent.action.startswith("admin."))
                      .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(RECENT_ACTIVITY))
    return [{"id": row.id, "action": row.action, "outcome": row.outcome, "actor": row.email,
             "created_at": _iso(row.created_at)} for row in rows]


# --- needs attention ------------------------------------------------------------------------------------------

def _attention(db, now: datetime, day: datetime, conditions: list, health: list[dict], payments: dict,
               support: dict, levels: dict, held: dict) -> list[dict]:
    """What an administrator should act on, most severe first; ``tab`` (and ``filter``) open where it is handled."""
    items: list[dict] = []
    rows = {row["key"]: row for row in health}

    def add(key: str, severity: str, tab: str, **values) -> None:
        items.append({"key": key, "severity": severity, "tab": tab, **values})

    for condition in conditions:  # the live alert conditions, as the alert notifications state them
        if condition.key.startswith("worker:"):
            add("worker_stale" if condition.level == "critical" else "worker_error", condition.level, "operations",
                worker=condition.details.get("worker"))
        elif condition.key == "disk:media":
            add("disk", condition.level, "operations", percent=condition.details.get("percent"))
        elif condition.key in ("backup:failed", "backup:overdue"):
            add(condition.key.replace(":", "_"), "critical", "system", filter="backups",
                max_age_hours=condition.details.get("max_age_hours"))
        elif condition.key == "master_key":
            add("master_key", "critical", "verification", problem=condition.details.get("problem"))
    if rows["database"]["status"] == "critical":
        add("database", "critical", "verification", detail=rows["database"].get("detail"))
    if rows["workers"]["missing"]:
        add("workers_missing", "warning", "operations", count=len(rows["workers"]["missing"]),
            workers=rows["workers"]["missing"])

    stuck, failed = db.execute(select(
        _tally(or_(and_(WorkflowJob.state == "leased", WorkflowJob.lease_expires_at <= now),
                   and_(WorkflowJob.state == "queued",
                        WorkflowJob.available_at <= now - timedelta(minutes=OVERDUE_MINUTES)))),
        _tally((WorkflowJob.state == "failed") & (WorkflowJob.updated_at >= day)))).one()
    if stuck:
        add("stuck_jobs", "warning", "operations", count=int(stuck))
    if failed:
        add("failed_jobs", "warning", "operations", count=int(failed))
    if held["count"]:
        add("reconciliation", "warning", "reconciliation", count=held["count"], credits=held["credits"])
    if payments["awaiting_confirmation"]:
        add("transfers", "warning", "payments", filter="awaiting_confirmation", count=payments["awaiting_confirmation"])
    if payments["paid_unapplied"]:
        add("paid_unapplied", "warning", "payments", filter="paid_unapplied", count=payments["paid_unapplied"])
    if audit.ready(db):
        rejected = db.scalar(select(func.count()).select_from(AuditEvent)
                             .where(AuditEvent.action == "payment.callback_rejected", AuditEvent.created_at >= day)) or 0
        if rejected:
            add("callbacks_rejected", "warning", "payments", count=int(rejected))
    if levels["critical"] or levels["full"]:
        add("studios_storage", "warning", "operations", count=levels["critical"] + levels["full"], full=levels["full"])
    if rows["email"]["status"] == "warning":
        add("email", "warning", "system", filter="email", detail=rows["email"].get("detail"),
            count=rows["email"].get("failed_24h", 0))
    if rows["configuration"]["errors"]:
        add("configuration", "warning", "verification", count=rows["configuration"]["errors"])
    if support["high_priority"]:
        add("support_high", "warning", "support", filter="high", count=support["high_priority"])
    if support["awaiting_support"]:
        add("support_waiting", "info", "support", count=support["awaiting_support"],
            since=support["oldest_waiting_since"])
    if rows["backups"]["status"] == "warning" and rows["backups"]["last_success"] is None:
        add("backup_never", "info", "system", filter="backups")
    return sorted(items, key=lambda item: SEVERITY[item["severity"]])


def summary(db, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=PERIOD_DAYS)
    day = now - timedelta(hours=JOB_HOURS)
    growth_start = datetime.combine((now - timedelta(days=PERIOD_DAYS - 1)).date(), datetime.min.time(), timezone.utc)
    conditions = alerts.conditions(db, now)
    health = _health(db, now, conditions)
    status = worst([row["status"] for row in health] + [condition.level for condition in conditions])
    jobs = _jobs(db, since, day)
    payments = _payments(db, since)
    support = _support(db)
    store = _storage(db)
    credits = _credits(db, since, jobs["credits"])
    held = {"count": credits["held_jobs"], "credits": credits["held"]}
    return {
        "generated_at": now.isoformat(),
        "periods": {"days": PERIOD_DAYS, "since": since.isoformat(), "jobs_hours": JOB_HOURS, "jobs_since": day.isoformat(),
                    "growth_from": growth_start.date().isoformat()},
        "overview": {**_overview(db, now, since), "paid_orders": payments["paid"],
                     "paid_amount_vnd": payments["paid_amount_vnd"], "jobs_24h": jobs["jobs_24h"],
                     "system_status": status},
        "attention": _attention(db, now, day, conditions, health, payments, support, store["levels"], held),
        "health": {"status": status, "rows": health},
        "ai_usage": jobs,
        "credits": credits,
        "payments": payments,
        "publishing": _publishing(db, since),
        "storage": store,
        "support": support,
        "growth": _growth(db, growth_start, PERIOD_DAYS),
        "activity": _activity(db),
    }
