"""A run at a glance, derived from what is already stored: steps, jobs, the credit ledger and usage events.

Nothing here is written, and no schema is needed. Credits are ReelForge credits,
never provider money:

* **reserved**: held when paid steps were queued (``*-reserve`` ledger entries);
* **refunded**: returned automatically or by reconciliation (``*-refund`` entries);
* **consumed**: charged for finished work (usage events, including confirmed charges);
* **held**: reserved and neither refunded nor consumed yet (running or awaiting a decision).

A ledger or usage reference belongs to the run when it names the run or one of
its steps (``text-reserve:<step>``, ``video-reserve:<step>:scene:2``,
``reserve:<run>``, ``video:<step>``…).
"""
from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import or_, select

from app.models import CreditLedger, Project, UsageEvent, WorkflowJob, WorkflowRun, WorkflowRunStep
from app.publications import final_video, fit_metadata, platform_metadata
from app.workflow.nodes.text import TEXT_HANDLERS

TEXT_TYPES = frozenset(handler.node_type for handler in TEXT_HANDLERS)
ACTIVE = ("queued", "submitting", "running")


def _output(step: WorkflowRunStep) -> dict:
    try:
        value = json.loads(step.output) if step.output else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _count(items: Any) -> int:
    return len(items) if isinstance(items, list) else 0


def credits(db, run: WorkflowRun, steps: list[WorkflowRunStep]) -> dict[str, int]:
    owners = {run.id, *(step.id for step in steps)}
    patterns = [CreditLedger.reference.like(f"%:{owner}%") for owner in owners]
    ledger = db.scalars(select(CreditLedger).where(CreditLedger.workspace_id == run.workspace_id, or_(*patterns)))
    usage_patterns = [UsageEvent.reference.like(f"%:{owner}%") for owner in owners]
    usage = db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == run.workspace_id, or_(*usage_patterns)))

    def mine(reference: str) -> bool:
        return bool(owners.intersection(reference.split(":")[1:]))

    reserved = refunded = 0
    for entry in ledger:
        if not mine(entry.reference):
            continue
        if entry.reason.endswith("_reserve"):
            reserved += -entry.delta
        elif entry.reason.endswith("_refund"):
            refunded += entry.delta
    consumed = sum(event.credits for event in usage if mine(event.reference))
    return {"reserved": reserved, "refunded": refunded, "consumed": consumed,
            "held": max(reserved - refunded - consumed, 0)}


def publishing_defaults(db, run: WorkflowRun, steps: list[WorkflowRunStep]) -> dict[str, Any]:
    """What the publish form starts with: the Publish step's hand-off, else Metadata output, else the project title.

    The top-level values are YouTube's; ``platforms`` holds every channel's (Phase 12).
    """
    for step in steps:
        output = _output(step)
        if step.node_type == "publish" and step.status == "completed" and isinstance(output.get("metadata"), dict):
            platforms = (output["platforms"] if isinstance(output.get("platforms"), dict)
                         else platform_metadata(output["metadata"]))
            return {**output["metadata"], "platforms": platforms, "source": "publish"}
    for step in steps:
        output = _output(step)
        if step.node_type == "metadata" and step.status == "completed" and isinstance(output.get("metadata"), dict):
            youtube = {**fit_metadata(output["metadata"].get("title"), output["metadata"].get("description"),
                                      output["metadata"].get("tags")), "privacy_status": "private"}
            platforms = output["platforms"] if isinstance(output.get("platforms"), dict) else platform_metadata(youtube)
            return {**youtube, "platforms": platforms, "source": "metadata"}
    project = db.get(Project, run.project_id)
    youtube = {**fit_metadata(project.title if project else "", "", []), "privacy_status": "private"}
    return {**youtube, "platforms": platform_metadata(youtube), "source": "project"}


def summarize(db, run: WorkflowRun) -> dict[str, Any]:
    steps = list(db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id)
                            .order_by(WorkflowRunStep.position)))
    counts = {"script_words": 0, "scenes": 0, "images": 0, "clips": 0, "narrations": 0, "subtitle_cues": 0}
    for step in steps:
        output = _output(step)
        if step.node_type == "ai_writer" and isinstance(output.get("script"), str):
            counts["script_words"] += len(output["script"].split())
        elif step.node_type == "scenes":
            counts["scenes"] += _count(output.get("scenes"))
        elif step.node_type == "image":
            counts["images"] += _count(output.get("image_assets"))
        elif step.node_type == "video":
            counts["clips"] += _count(output.get("video_assets")) or (1 if output.get("asset_id") else 0)
        elif step.node_type == "voice":
            counts["narrations"] += _count(output.get("audio_assets"))
        elif step.node_type == "subtitle":
            counts["subtitle_cues"] += output.get("cue_count") if isinstance(output.get("cue_count"), int) else 0

    def item(step: WorkflowRunStep) -> dict[str, Any]:
        error = _output(step).get("error")
        return {"node_id": step.node_id, "node_type": step.node_type, "status": step.status, "detail": step.detail,
                "error_code": error.get("code") if isinstance(error, dict) else None}

    video = final_video(db, run)
    final = None
    if video is not None:
        source = db.get(WorkflowRunStep, video.step_id)
        entry = next((clip for clip in _output(source).get("video_assets") or []
                      if isinstance(clip, dict) and clip.get("id") == video.id), {}) if source else {}
        final = {"asset_id": video.id, "filename": video.filename, "bytes": video.bytes,
                 "final": bool(source and source.node_type == "render"), "duration": entry.get("duration"),
                 "width": entry.get("width"), "height": entry.get("height")}
    review = next((step for step in steps if step.node_type == "review"), None)
    approved = any(step.node_type == "review" and step.status == "completed"
                   and isinstance(_output(step).get("approved_by"), str) for step in steps)
    render_failed = next((item(step) for step in steps if step.node_type == "render" and step.status == "failed"), None)
    if render_failed:
        render_failed["message"] = _output(next(step for step in steps if step.node_id == render_failed["node_id"])
                                           ).get("render_error")
    end = _utc(run.finished_at) if run.finished_at else datetime.now(timezone.utc)
    return {
        "run_id": run.id, "status": run.status,
        "elapsed_seconds": max(0, round((end - _utc(run.created_at)).total_seconds())),
        "steps": {"total": len(steps), "completed": sum(step.status == "completed" for step in steps)},
        "current": [item(step) for step in steps if step.status in ACTIVE],
        "failed": [item(step) for step in steps if step.status == "failed"],
        "needs_attention": [item(step) for step in steps if step.status == "needs_attention"],
        "blocked": [item(step) for step in steps if step.status == "blocked"],
        "render_failed": render_failed,
        "active_jobs": len(list(db.scalars(select(WorkflowJob.id).where(WorkflowJob.run_id == run.id,
                                                                         WorkflowJob.state.in_(("queued", "leased")))))),
        "counts": counts,
        "final_video": final,
        "review": {"present": review is not None, "status": review.status if review else None, "approved": approved},
        "credits": credits(db, run, steps),
        "publishing": {"ready": approved and final is not None, "defaults": publishing_defaults(db, run, steps)},
    }
