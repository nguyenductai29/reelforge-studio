"""Home, the user dashboard: the active studio at a glance, from a handful of aggregate queries.

``summary`` is everything Home shows besides what the shell already loads (``/api/dashboard``):

* the overview: credits, projects, runs and publications of the last ``PERIOD_DAYS`` days, storage;
* "Needs attention": what waits for the signed-in member, newest first within each kind;
* the most recent workflows (by their last run), projects (by their last activity) and runs;
* AI usage of the period by task (text, image, video, voice, transcription, render), from the usage events;
* publishing by channel: published in the period, scheduled, failed in the period.

Every query is limited to the active workspace, and tables are counted and grouped in SQL, never loaded whole.
The member's role decides the attention items: each one is something that role can act on (a viewer reviews
nothing; an editor neither buys credits nor reconnects a channel). Nothing system-wide is here (users, studios,
revenue, workers, backups): that is the admin console's.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import aliased

from app import notifications, permissions, storage, team
from app.models import (Asset, CreditAccount, Plan, Project, Subscription, SupportTicket, UsageEvent, Workflow,
                        WorkflowRun)
from app.publications import Publication

PERIOD_DAYS = 30
RECENT_WORKFLOWS = 4
RECENT_PROJECTS = 6
RECENT_RUNS = 6
ACTIVE = ("running", "queued", "submitting")
# A usage event's tool is "<provider>/<task>" (app/media_jobs.py, text_worker.py, render_worker.py, ...).
TASKS = ("text", "image", "video", "voice", "transcription", "render")
# Storage at 80 % of the quota or more (app/storage.py LEVELS).
STORAGE_ATTENTION = ("warning", "critical", "full")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _count(db, query) -> int:
    return int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def _runs_by_status(db, workspace_id: str, *conditions) -> dict[str, dict]:
    """Per run status: how many of the workspace's runs match, and the newest of them (what the item opens)."""
    newest_first = (WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
    ranked = (select(WorkflowRun.id, WorkflowRun.workflow_id, WorkflowRun.status, WorkflowRun.created_at,
                     func.row_number().over(partition_by=WorkflowRun.status, order_by=newest_first).label("rank"),
                     func.count().over(partition_by=WorkflowRun.status).label("total"))
              .where(WorkflowRun.workspace_id == workspace_id, *conditions).subquery())
    return {row.status: {"count": int(row.total), "run_id": row.id, "workflow_id": row.workflow_id,
                         "created_at": row.created_at}
            for row in db.execute(select(ranked).where(ranked.c.rank == 1))}


def _item(groups: list[dict]) -> dict | None:
    """Several statuses as one item: their total, opening the newest run."""
    if not groups:
        return None
    newest = max(groups, key=lambda group: group["created_at"])
    return {"count": sum(group["count"] for group in groups), "run_id": newest["run_id"],
            "workflow_id": newest["workflow_id"]}


def _usage(db, workspace_id: str, since: datetime) -> dict:
    tasks: dict[str, dict] = {}
    for tool, credits, events in db.execute(
            select(UsageEvent.tool, func.coalesce(func.sum(UsageEvent.credits), 0), func.count())
            .where(UsageEvent.workspace_id == workspace_id, UsageEvent.created_at >= since)
            .group_by(UsageEvent.tool)):
        task = tool.rsplit("/", 1)[-1]
        task = task if task in TASKS else "other"
        entry = tasks.setdefault(task, {"task": task, "credits": 0, "events": 0})
        entry["credits"] += int(credits)
        entry["events"] += int(events)
    by_task = sorted(tasks.values(), key=lambda entry: (-entry["credits"], entry["task"]))
    return {"credits_used": sum(entry["credits"] for entry in by_task), "by_task": by_task}


def _publishing(db, workspace_id: str, since: datetime, channels: list[dict]) -> dict:
    """Per channel: published in the period, scheduled, failed in the period. A channel shows when the studio
    connected it or ever published there; one it never used gets no metric."""
    when = func.coalesce(Publication.published_at, Publication.finished_at, Publication.updated_at)
    failed = Publication.state.in_(("failed", "needs_attention")) & (Publication.updated_at >= since)

    def tally(condition):
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    counts = {row.channel: row for row in db.execute(
        select(Publication.channel, tally((Publication.state == "succeeded") & (when >= since)).label("published"),
               tally(Publication.state == "scheduled").label("scheduled"), tally(failed).label("failed"),
               func.count().label("total"))
        .where(Publication.workspace_id == workspace_id).group_by(Publication.channel))}
    listed = []
    for channel in channels:
        row = counts.get(channel["channel"])
        if channel["status"] not in ("connected", "authorization_required") and row is None:
            continue
        listed.append({"channel": channel["channel"], "status": channel["status"],
                       **{key: int(getattr(row, key)) if row is not None else 0
                          for key in ("published", "scheduled", "failed")}})
    return {"channels": listed, **{key: sum(item[key] for item in listed) for key in ("published", "scheduled",
                                                                                     "failed")}}


def _support_reply(db, workspace_id: str, user_id: str, sees_all: bool) -> dict | None:
    """Support tickets waiting for the member's answer: theirs, or all of the studio's for owners and admins
    (as on the Support page)."""
    query = select(SupportTicket.id).where(SupportTicket.workspace_id == workspace_id,
                                          SupportTicket.status == "waiting_user")
    if not sees_all:
        query = query.where(SupportTicket.created_by_user_id == user_id)
    count = _count(db, query)
    if not count:
        return None
    newest = db.scalar(query.order_by(SupportTicket.updated_at.desc(), SupportTicket.id).limit(1))
    return {"count": count, "ticket_id": newest}


def _recent_workflows(db, workspace_id: str) -> list[dict]:
    """The workflows run last (a workflow keeps no edit time); those never run follow, by name."""
    last = (select(WorkflowRun.workflow_id, func.max(WorkflowRun.created_at).label("last"))
            .where(WorkflowRun.workspace_id == workspace_id).group_by(WorkflowRun.workflow_id).subquery())
    rows = db.execute(select(Workflow.id, Workflow.name, last.c.last)
                      .outerjoin(last, last.c.workflow_id == Workflow.id)
                      .where(Workflow.workspace_id == workspace_id)
                      .order_by(case((last.c.last.is_(None), 1), else_=0), last.c.last.desc(), Workflow.name,
                                Workflow.id)
                      .limit(RECENT_WORKFLOWS)).all()
    ran = [row.id for row in rows if row.last is not None]
    latest = {}
    if ran:
        ranked = (select(WorkflowRun.id, WorkflowRun.workflow_id, WorkflowRun.project_id, WorkflowRun.status,
                         WorkflowRun.created_at, WorkflowRun.finished_at,
                         func.row_number().over(partition_by=WorkflowRun.workflow_id,
                                                order_by=(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()))
                         .label("rank"))
                  .where(WorkflowRun.workspace_id == workspace_id, WorkflowRun.workflow_id.in_(ran)).subquery())
        for row in db.execute(select(ranked, Project.title).outerjoin(Project, Project.id == ranked.c.project_id)
                              .where(ranked.c.rank == 1)):
            latest[row.workflow_id] = {"id": row.id, "status": row.status, "project_id": row.project_id,
                                       "project_title": row.title, "created_at": _iso(row.created_at),
                                       "finished_at": _iso(row.finished_at)}
    return [{"id": row.id, "name": row.name, "last_run": latest.get(row.id)} for row in rows]


def _project_status(saved: str, runs: set[str], published: bool) -> str:
    """As the project board derives it (frontend lib/studio.ts): the backend stores only draft or approved."""
    if published:
        return "published"
    if saved == "approved":
        return "ready"
    if "awaiting_review" in runs:
        return "review"
    if runs & set(ACTIVE):
        return "generating"
    return "draft"


def _recent_projects(db, workspace_id: str) -> list[dict]:
    """The projects with the latest activity (their last run, else their creation), with what the board shows:
    status, videos, the channels they reached and their newest image as a cover."""
    last = (select(WorkflowRun.project_id, func.max(WorkflowRun.created_at).label("last"))
            .where(WorkflowRun.workspace_id == workspace_id).group_by(WorkflowRun.project_id).subquery())
    activity = func.coalesce(last.c.last, Project.created_at)
    rows = db.execute(select(Project, activity.label("activity")).outerjoin(last, last.c.project_id == Project.id)
                      .where(Project.workspace_id == workspace_id)
                      .order_by(activity.desc(), Project.id).limit(RECENT_PROJECTS)).all()
    ids = [project.id for project, _ in rows]
    if not ids:
        return []
    statuses: dict[str, set[str]] = {}
    for project_id, status in db.execute(
            select(WorkflowRun.project_id, WorkflowRun.status)
            .where(WorkflowRun.workspace_id == workspace_id, WorkflowRun.project_id.in_(ids))
            .group_by(WorkflowRun.project_id, WorkflowRun.status)):
        statuses.setdefault(project_id, set()).add(status)
    channels: dict[str, list[str]] = {}
    for project_id, channel in db.execute(
            select(WorkflowRun.project_id, Publication.channel)
            .join(WorkflowRun, WorkflowRun.id == Publication.run_id)
            .where(Publication.workspace_id == workspace_id, Publication.state == "succeeded",
                   WorkflowRun.project_id.in_(ids))
            .group_by(WorkflowRun.project_id, Publication.channel)):
        channels.setdefault(project_id, []).append(channel)
    stored = (Asset.workspace_id == workspace_id, Asset.project_id.in_(ids), Asset.bytes > 0)
    videos = dict(db.execute(select(Asset.project_id, func.count())
                             .where(*stored, Asset.content_type.like("video/%")).group_by(Asset.project_id)).all())
    ranked = (select(Asset.id, Asset.project_id,
                     func.row_number().over(partition_by=Asset.project_id,
                                            order_by=(Asset.created_at.desc(), Asset.id)).label("rank"))
              .where(*stored, Asset.content_type.like("image/%")).subquery())
    covers = {project_id: asset_id for asset_id, project_id in
              db.execute(select(ranked.c.id, ranked.c.project_id).where(ranked.c.rank == 1))}
    return [{"id": project.id, "title": project.title, "topic": project.topic,
             "created_at": _iso(project.created_at), "last_activity_at": _iso(when),
             "status": _project_status(project.status, statuses.get(project.id, set()), project.id in channels),
             "videos": int(videos.get(project.id, 0)), "channels": sorted(channels.get(project.id, [])),
             "cover_asset_id": covers.get(project.id)}
            for project, when in rows]


def _recent_runs(db, workspace_id: str) -> list[dict]:
    rows = db.execute(select(WorkflowRun.id, WorkflowRun.workflow_id, WorkflowRun.project_id, WorkflowRun.status,
                             WorkflowRun.created_at, WorkflowRun.finished_at, Workflow.name, Project.title)
                      .outerjoin(Workflow, Workflow.id == WorkflowRun.workflow_id)
                      .outerjoin(Project, Project.id == WorkflowRun.project_id)
                      .where(WorkflowRun.workspace_id == workspace_id)
                      .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()).limit(RECENT_RUNS)).all()
    return [{"id": row.id, "workflow_id": row.workflow_id, "workflow_name": row.name, "project_id": row.project_id,
             "project_title": row.title, "status": row.status, "created_at": _iso(row.created_at),
             "finished_at": _iso(row.finished_at)} for row in rows]


def summary(db, *, workspace_id: str, user_id: str, role: str, subscription_status: str, channels: list[dict],
            now: datetime | None = None) -> dict:
    """Home of ``workspace_id`` for a member with ``role``. ``subscription_status`` is the effective one
    (``expired`` once past its end) and ``channels`` the publishing channels with their connection status, as
    ``/api/channels`` lists them (both computed by the API)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=PERIOD_DAYS)
    publishing_allowed = team.editors_can_publish(db, workspace_id)

    def can(permission: str) -> bool:
        return permissions.allowed(role, permission, editors_can_publish=publishing_allowed)

    subscription = db.get(Subscription, workspace_id)
    plan = db.get(Plan, subscription.plan_code) if subscription else None
    account = db.get(CreditAccount, workspace_id)
    balance = account.balance if account else 0
    threshold = notifications.low_credit_threshold()
    stored = storage.usage(db, workspace_id)
    publishing = _publishing(db, workspace_id, since, channels)

    current = _runs_by_status(db, workspace_id, WorkflowRun.status.in_(("awaiting_review", "needs_attention", *ACTIVE)))
    # Failed or stopped runs of the period that nobody retried yet (a retry points back with retry_of_id).
    retry = aliased(WorkflowRun)
    stopped = _runs_by_status(db, workspace_id, WorkflowRun.status.in_(("failed", "blocked")),
                              WorkflowRun.created_at >= since,
                              ~select(retry.id).where(retry.retry_of_id == WorkflowRun.id).exists())
    active = _item([current[status] for status in ACTIVE if status in current])

    attention: list[dict] = []

    def attend(kind: str, severity: str = "warning", **values) -> None:
        attention.append({"kind": kind, "severity": severity, **values})

    if subscription_status != "active" and can("content.edit"):
        attend("plan_inactive", status=subscription_status)
    if can("runs.execute"):
        for kind, group in (("review", current.get("awaiting_review")), ("failed_runs", stopped.get("failed")),
                            ("blocked_runs", stopped.get("blocked"))):
            if group:
                attend(kind, **_item([group]))
    if can("publish") and publishing["failed"]:
        attend("publish_failed", count=publishing["failed"])
    if can("channels.manage"):
        # A grant to renew, or a channel disconnected while posts wait for it. A channel the server has no
        # credentials for is the system administrator's to configure, not the studio's.
        for channel in publishing["channels"]:
            if channel["status"] == "authorization_required" or (channel["status"] == "not_connected"
                                                                  and channel["scheduled"]):
                attend("channel_reconnect", channel=channel["channel"], scheduled=channel["scheduled"])
    if threshold and balance < threshold and (can("runs.execute") or can("billing.view")):
        attend("credits_low", balance=balance, threshold=threshold)
    if stored["level"] in STORAGE_ATTENTION and can("content.edit"):
        attend("storage", level=stored["level"], percent=stored["percent"])
    reply = _support_reply(db, workspace_id, user_id, permissions.allowed(role, "members.manage"))
    if reply:
        attend("support_reply", **reply)
    held = _item([current["needs_attention"]] if "needs_attention" in current else [])
    if held and can("runs.execute"):
        attend("reconciliation", "info", **held)
    if active:
        attend("generating", "info", **active)

    return {
        "period_days": PERIOD_DAYS,
        "overview": {
            "credits": balance,
            "credits_monthly": plan.monthly_credits if plan else None,
            "credits_low_threshold": threshold,
            "projects": _count(db, select(Project.id).where(Project.workspace_id == workspace_id)),
            "projects_limit": plan.project_limit if plan else None,
            "runs_30d": _count(db, select(WorkflowRun.id).where(WorkflowRun.workspace_id == workspace_id,
                                                               WorkflowRun.created_at >= since)),
            "runs_active": active["count"] if active else 0,
            "published_30d": publishing["published"],
            "storage_used_bytes": stored["used_bytes"],
            "storage_quota_bytes": stored["quota_bytes"],
            "storage_percent": stored["percent"],
            "storage_level": stored["level"],
        },
        "attention": attention,
        "recent_workflows": _recent_workflows(db, workspace_id),
        "recent_projects": _recent_projects(db, workspace_id),
        "recent_runs": _recent_runs(db, workspace_id),
        "usage": _usage(db, workspace_id, since),
        "publishing": publishing,
    }
