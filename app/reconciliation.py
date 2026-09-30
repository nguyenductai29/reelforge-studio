"""Resolve reserved paid operations using the existing ledger, never a second debit.

The caller owns the transaction. A final decision is inserted once per paid job
(migration 0012), never edited. Legacy jobs keep their original accounting
references (``reserve:<run>`` for video, ``text-reserve:<step>`` for text); jobs
queued per operation (images, voice, multi-scene and new single video) carry their own
references in the payload (app/workflow/nodes/media.py). A step with several
jobs keeps each job's facts under ``output["jobs"][<job_id>]``; each uncertain
job is listed and decided on its own, and the step leaves ``needs_attention``
only when none of its jobs is still waiting for a decision.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from uuid import uuid4

from sqlalchemy import select, update

from app import usage
from app.logs import log_event, scrub
from app.models import (Asset, CreditAccount, CreditLedger, CreditReconciliation, Project, UsageEvent, User,
                        Workflow, WorkflowJob, WorkflowRun, WorkflowRunStep, Workspace)
from app.workflow.results import derive_run_status

logger = logging.getLogger(__name__)
PAID_KINDS = {"video.generate": "video", "text.generate": "text", "image.generate": "image", "voice.generate": "voice"}


class ReconciliationError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PaidReservation:
    ledger: CreditLedger
    credits: int
    refund_reference: str
    usage_reference: str
    tool: str
    refund_reason: str


def _output(step):
    try:
        value = json.loads(step.output) if step.output else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _object(value):
    return value if isinstance(value, dict) else {}


def _safe_text(value, limit=500):
    if not isinstance(value, str):
        return None
    # URLs can contain OAuth tokens and signed media query strings, including in
    # legacy worker errors. The operator needs IDs, never raw URLs/responses.
    value = re.sub(r"https?://\S+", "[URL omitted]", scrub(value))
    return value[:limit]


def _identifier(value):
    return _safe_text(value, 255) if isinstance(value, str) and re.fullmatch(r"[\w.:-]{1,255}", value) else None


def _iso(value):
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _references(kind, payload, step, run):
    """(reserve, usage, refund) references: the job's own, or the legacy ones for older jobs."""
    reserve, usage_ref, refund = (payload.get("reserve_reference"), payload.get("usage_reference"),
                                  payload.get("refund_reference"))
    if reserve is None and usage_ref is None and refund is None:
        if kind == "video":
            return f"reserve:{run.id}", f"video:{step.id}", f"refund:{run.id}"
        if kind == "text":
            return f"text-reserve:{step.id}", f"text:{step.id}", f"text-refund:{step.id}"
        raise ReconciliationError("Paid job has no accounting references")
    operation = payload.get("operation")
    # The references name this step and operation; anything else was not written by the handler.
    if (not isinstance(operation, str) or reserve != f"{kind}-reserve:{step.id}:{operation}"
            or usage_ref != f"{kind}:{step.id}:{operation}" or refund != f"{kind}-refund:{step.id}:{operation}"):
        raise ReconciliationError("Paid job references do not match the step")
    return reserve, usage_ref, refund


def paid_reservation(db, job, step, run) -> PaidReservation:
    """Validate ownership and the actual debit, using the frozen job quote."""
    payload = job.payload
    kind = PAID_KINDS.get(payload.get("kind"))
    if not kind or job.step_id != step.id or job.run_id != run.id or step.run_id != run.id or job.workspace_id != run.workspace_id:
        raise ReconciliationError("Paid job relationships do not match")
    workflow, project = db.get(Workflow, run.workflow_id), db.get(Project, run.project_id)
    if not workflow or not project or workflow.workspace_id != run.workspace_id or project.workspace_id != run.workspace_id:
        raise ReconciliationError("Run relationships do not match the workspace")
    if (kind in ("video", "image", "voice") and step.node_type != kind) or (kind == "text" and step.node_type != payload.get("node_type")):
        raise ReconciliationError("Paid job does not match the step type")
    reserve, usage_reference, refund = _references(kind, payload, step, run)
    ledger = db.scalar(select(CreditLedger).where(CreditLedger.reference == reserve))
    credits = payload.get("credits")
    if (type(credits) is not int or credits <= 0 or not ledger or ledger.workspace_id != run.workspace_id
            or ledger.delta != -credits or ledger.reason != f"{kind}_reserve"):
        raise ReconciliationError("Reservation does not match the paid job")
    provider = payload.get("provider")
    if not isinstance(provider, str) or not provider or len(provider) > 60:
        raise ReconciliationError("Invalid paid job provider")
    return PaidReservation(ledger, credits, refund, usage_reference, f"{provider}/{kind}", f"{kind}_refund")


def _child(output, job):
    """This job's facts in a multi-job step, or ``None`` for a step with one job."""
    records = output.get("jobs")
    record = records.get(job.id) if isinstance(records, dict) else None
    return record if isinstance(record, dict) else None


def _uncertain(step, job, output) -> bool:
    """A failed paid job waiting for a decision: its own record says so, or its single-job step does."""
    if job.state != "failed":
        return False
    record = _child(output, job)
    return record.get("status") == "needs_attention" if record is not None else step.status == "needs_attention"


def _job_for(db, step):
    jobs = list(db.scalars(select(WorkflowJob).where(WorkflowJob.step_id == step.id)))
    if len(jobs) != 1:
        raise ReconciliationError("This step has several paid jobs; decide each job separately")
    return jobs[0]


def public_item(db, step, job, run, decision=None):
    """Explicit projection: never serialize a job payload or submission URLs."""
    payload, output = job.payload, _output(step)
    record = _child(output, job)
    facts = record if record is not None else output
    progress = _object(facts.get("provider_job"))
    submission = _object(facts.get("submission"))
    error = _object(facts.get("error"))
    workspace = db.get(Workspace, run.workspace_id)
    owner = db.get(User, workspace.owner_id)
    workflow = db.get(Workflow, run.workflow_id)
    admin = db.get(User, decision.reconciled_by) if decision else None
    if record is not None:
        assets = [entry["id"] for entry in record.get("assets") or [] if isinstance(entry, dict)
                  and isinstance(entry.get("id"), str)]
    else:
        assets = list(db.scalars(select(Asset.id).where(Asset.workspace_id == run.workspace_id,
                                                      Asset.run_id == run.id, Asset.step_id == step.id)))
    remote = _identifier(progress.get("remote_request_id") or submission.get("request_id"))
    scene_index = payload.get("scene_index")
    return {
        "step_id": step.id, "run_id": run.id, "job_id": job.id, "workspace_id": run.workspace_id,
        "scene_index": scene_index if isinstance(scene_index, int) and not isinstance(scene_index, bool) else None,
        "operation": _identifier(payload.get("operation")),
        "workspace_name": workspace.name, "user_email": owner.email,
        "workflow_id": run.workflow_id, "workflow_name": workflow.name,
        "node_id": step.node_id, "node_type": step.node_type,
        "provider": _safe_text(payload.get("provider"), 60),
        "model": _safe_text(payload.get("model_id") or payload.get("model"), 100),
        "remote_request_id": remote, "credits": decision.credits if decision else payload.get("credits"),
        "created_at": _iso(job.created_at), "stage": _safe_text(progress.get("stage"), 80),
        "submitted_at": _safe_text(progress.get("submitted_at"), 64),
        "last_polled_at": _safe_text(progress.get("last_polled_at"), 64),
        "last_provider_status": _safe_text(progress.get("last_provider_status"), 80),
        "submission_succeeded": progress.get("submission_succeeded") is True or bool(submission.get("request_id")),
        "error_category": _safe_text(error.get("category"), 80),
        "error_message": _safe_text(record.get("detail") if record is not None else step.detail) or "",
        "has_asset": bool(assets), "asset_ids": assets,
        "reconciliation_status": decision.decision if decision else "pending",
        "reconciled_at": _iso(decision.reconciled_at) if decision else None,
        "reconciled_by": decision.reconciled_by if decision else None,
        "reconciled_by_email": admin.email if admin else None,
        "note": _safe_text(decision.note, 1000) if decision else None,
    }


def list_items(db, *, status="pending", limit=50, offset=0):
    query = (select(WorkflowRunStep, WorkflowJob, WorkflowRun, CreditReconciliation)
             .join(WorkflowJob, WorkflowJob.step_id == WorkflowRunStep.id)
             .join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id)
             .outerjoin(CreditReconciliation, CreditReconciliation.job_id == WorkflowJob.id)
             .where(WorkflowJob.run_id == WorkflowRun.id, WorkflowJob.workspace_id == WorkflowRun.workspace_id))
    if status == "pending":
        # Multi-job steps can hold an uncertain job while siblings still run; its own record decides.
        query = query.where(WorkflowRunStep.status.in_(("needs_attention", "running")), WorkflowJob.state == "failed",
                            CreditReconciliation.job_id.is_(None))
    else:
        query = query.where(CreditReconciliation.job_id.is_not(None))
    # Paid kinds live in portable immutable JSON, not provider-specific SQL JSON
    # expressions. Validate historical references before exposing an action.
    rows = []
    for step, job, run, decision in db.execute(query.order_by(WorkflowJob.created_at.desc(), WorkflowRunStep.id,
                                                              WorkflowJob.id)):
        if status == "pending" and not _uncertain(step, job, _output(step)):
            continue
        try:
            paid_reservation(db, job, step, run)
        except ReconciliationError:
            continue
        rows.append((step, job, run, decision))
    return {"items": [public_item(db, *row) for row in rows[offset:offset + limit]], "total": len(rows)}


def reconcile(db, *, decision, admin_user_id, note=None, step_id=None, job_id=None):
    """Lock, validate and finalize one paid job once; caller must commit before logging success.

    ``job_id`` names the job. ``step_id`` is the older form for a step with one paid job.
    """
    admin = db.get(User, admin_user_id)
    if not admin or not admin.is_active or not admin.is_admin:
        raise ReconciliationError("System admin required", 403)
    if decision not in ("confirmed_charge", "refunded"):
        raise ReconciliationError("Invalid reconciliation decision", 422)
    # A no-op UPDATE also obtains a write lock on SQLite (FOR UPDATE is ignored
    # there). PostgreSQL locks this same run row used by the executor and retry.
    if job_id is not None:
        run_id = select(WorkflowJob.run_id).where(WorkflowJob.id == job_id).scalar_subquery()
    else:
        run_id = select(WorkflowRunStep.run_id).where(WorkflowRunStep.id == step_id).scalar_subquery()
    db.execute(update(WorkflowRun).where(WorkflowRun.id == run_id).values(status=WorkflowRun.status))
    if job_id is not None:
        job = db.get(WorkflowJob, job_id)
        if not job:
            raise ReconciliationError("Paid job not found", 404)
        step = db.get(WorkflowRunStep, job.step_id)
    else:
        step = db.get(WorkflowRunStep, step_id)
        if not step:
            raise ReconciliationError("Workflow step not found", 404)
        job = _job_for(db, step)
    run = db.get(WorkflowRun, step.run_id)
    reservation = paid_reservation(db, job, step, run)
    existing = db.get(CreditReconciliation, job.id)
    if existing:
        if existing.decision != decision:
            raise ReconciliationError("This job already has a different final reconciliation decision")
        return public_item(db, step, job, run, existing), False
    if not _uncertain(step, job, _output(step)):
        raise ReconciliationError("Only terminal paid steps needing attention can be reconciled")
    account = db.scalar(select(CreditAccount).where(CreditAccount.workspace_id == run.workspace_id).with_for_update())
    if account is None or account.balance < 0:
        raise ReconciliationError("Invalid credit account")
    refunded = db.scalar(select(CreditLedger).where(CreditLedger.reference == reservation.refund_reference))
    consumed = db.scalar(select(UsageEvent).where(UsageEvent.reference == reservation.usage_reference))
    if refunded and (refunded.workspace_id != run.workspace_id or refunded.delta != reservation.credits
                     or refunded.reason != reservation.refund_reason):
        raise ReconciliationError("Existing refund does not match the reservation")
    if consumed and (consumed.workspace_id != run.workspace_id or consumed.credits != reservation.credits
                     or consumed.tool != reservation.tool):
        raise ReconciliationError("Existing usage does not match the reservation")
    now = datetime.now(timezone.utc)
    if decision == "confirmed_charge":
        if refunded:
            raise ReconciliationError("A refunded reservation cannot be charged")
        if not consumed:
            db.add(UsageEvent(id=str(uuid4()), workspace_id=run.workspace_id, tool=reservation.tool,
                              units=1, credits=reservation.credits, reference=reservation.usage_reference, created_at=now))
    else:
        if consumed:
            raise ReconciliationError("Finalized usage cannot be refunded by reconciliation")
        try:
            usage.post_credit(db, run.workspace_id, reservation.credits, reservation.refund_reason, reservation.refund_reference)
        except ValueError as exc:
            raise ReconciliationError(str(exc)) from exc
    record = CreditReconciliation(step_id=step.id, job_id=job.id, reservation_id=reservation.ledger.id,
                                  decision=decision, credits=reservation.credits, reconciled_by=admin_user_id,
                                  reconciled_at=now, note=_safe_text(note.strip(), 1000) if note and note.strip() else None)
    db.add(record)
    output = _output(step)
    marker = {"status": decision, "reconciled_at": now.isoformat(), "credits": reservation.credits}
    child = _child(output, job)
    if child is None:
        if decision == "refunded":
            step.status = "failed"
        output["reconciliation"] = marker
    else:
        child["reconciliation"] = marker
        if decision == "refunded":
            child["status"] = "failed"
        _settle_children(step, output)
    step.output = json.dumps(output, ensure_ascii=False)
    # Preserve the original failure explanation for the immutable incident.
    db.flush()
    run.status = derive_run_status(db.scalars(select(WorkflowRunStep.status).where(WorkflowRunStep.run_id == run.id)))
    run.finished_at = None if run.status in ("running", "awaiting_review") else now
    db.flush()
    return public_item(db, step, job, run, record), True


def _settle_children(step, output):
    """After one job's decision, update a finished multi-job step from all its jobs.

    A step still running is settled by its workers. A finished step stays
    ``needs_attention`` while any job waits for a decision or was confirmed as
    charged; once every uncertain job was refunded it becomes ``failed``. The
    step-level ``reconciliation`` summary appears only when no job is waiting.
    """
    if step.status != "needs_attention":
        return
    records = [record for record in output["jobs"].values() if isinstance(record, dict)]
    decided = [record["reconciliation"] for record in records if isinstance(record.get("reconciliation"), dict)]
    waiting = any(record.get("status") == "needs_attention" and not isinstance(record.get("reconciliation"), dict)
                  for record in records)
    confirmed = any(item.get("status") == "confirmed_charge" for item in decided)
    if not waiting and not confirmed:
        step.status = "failed"
    if not waiting and decided:
        output["reconciliation"] = {"status": "confirmed_charge" if confirmed else "refunded",
                                    "reconciled_at": max(item.get("reconciled_at") or "" for item in decided),
                                    "credits": sum(item.get("credits") or 0 for item in decided)}


def log_resolution(item, admin_user_id):
    """Emit only after the decision and accounting transaction has committed."""
    event = "reconciliation_refunded" if item["reconciliation_status"] == "refunded" else "reconciliation_charge_confirmed"
    log_event(logger, event, admin_user_id=admin_user_id, workspace_id=item["workspace_id"],
              run_id=item["run_id"], step_id=item["step_id"], job_id=item["job_id"],
              scene_index=item.get("scene_index"), credits=item["credits"], provider=item["provider"])
