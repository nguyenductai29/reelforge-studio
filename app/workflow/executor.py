"""Evaluates a run's graph node by node through the handler registry.

A run is evaluated in passes over the snapshot, in topological order:

* ``start_run`` creates one step per node. A node whose parents all completed
  is handed to its handler; any other node stays ``skipped`` (pending).
* ``advance_run`` re-evaluates pending steps after an external event, such as
  a worker finishing a job (``finish_step``) or a reviewer approving. It locks
  the run row first, so concurrent passes over one run happen one at a time.

A step that did not complete never lets its children run, and a pass never
rewrites a step that already left the pending state. Every change happens in
the caller's transaction. A node whose settings are invalid is blocked with the
setting's error code before its handler sees it; the rest of the run goes on.

Each pass logs structured events (``workflow_run_started``, ``workflow_step_*``,
``credit_reserved``…; see ``app/logs.py``) once it has finished, so a start
that is rejected and rolled back logs only ``workflow_run_rejected``.
"""
from dataclasses import dataclass
import json
import logging
from typing import Any, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import jobs, movie_sources, notifications
from app.logs import log_event, payload_summary
from app.models import WorkflowRun, WorkflowRunStep
from app.workflow.config import TEXT as TEXT_FIELD, ConfigError
from app.workflow.context import ExecutionContext, NodeInputs, StepState, resolve_node_inputs
from app.workflow.graph import ordered_nodes
from app.workflow.nodes.base import INVALID_CONFIG_DETAIL
from app.workflow.ports import unsatisfied_inputs
from app.workflow.registry import NodeRegistry, default_registry
from app.workflow.results import (AWAITING_REVIEW, BLOCKED, COMPLETED, FAILED, NEEDS_ATTENTION, OPEN_STATUSES, QUEUED,
                                  RUNNING, SKIPPED, WAITING_DETAIL, NodeError, NodeExecutionResult, NodeReadiness,
                                  RunRequestError, derive_run_status)

logger = logging.getLogger(__name__)

HANDLER_FAILED_DETAIL = "Bước gặp lỗi khi thực thi."
STEP_EVENTS = {COMPLETED: "workflow_step_completed", FAILED: "workflow_step_failed", BLOCKED: "workflow_step_blocked",
               QUEUED: "workflow_step_queued", AWAITING_REVIEW: "workflow_step_awaiting_review",
               NEEDS_ATTENTION: "workflow_step_needs_attention", RUNNING: "workflow_step_running"}


def config_summary(handler, config) -> dict[str, Any]:
    """A node's settings for logs: choices as they are, free text by length only."""
    if not isinstance(config, Mapping):
        return {}
    fields = {field.key: field for field in handler.config_fields}
    summary = {}
    for key, value in config.items():
        field = fields.get(key)
        if isinstance(value, str) and (field is None or field.type == TEXT_FIELD):
            summary[f"{key}_chars"] = len(value)
        else:
            summary[key] = value
    return summary


@dataclass
class RunProgress:
    steps: list[WorkflowRunStep]
    # Results of the nodes evaluated in this pass, by node ID.
    results: dict[str, NodeExecutionResult]


def _stored_output(step: WorkflowRunStep) -> Mapping[str, Any] | None:
    try:
        value = json.loads(step.output) if step.output else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class WorkflowExecutor:
    def __init__(self, registry: NodeRegistry | None = None):
        self.registry = registry or default_registry

    def start_run(self, context: ExecutionContext) -> RunProgress:
        """Evaluate a new run and persist its steps, queued jobs and status.

        A ``RunRequestError`` from any handler propagates, so the caller can
        reject the whole request and roll back.
        """
        order = ordered_nodes(context.graph)
        steps = [WorkflowRunStep(id=str(uuid.uuid4()), run_id=context.run.id, node_id=node["id"],
                                 node_type=node["type"], position=position, status=SKIPPED, detail=WAITING_DETAIL)
                 for position, (node, _) in enumerate(order)]
        context.db.add_all(steps)
        context.db.flush()
        context.steps = {step.node_id: step for step in steps}
        states: dict[str, StepState] = {}
        evaluated = {}
        try:
            for (node, _), step in zip(order, steps):
                result = self._evaluate(context, node, self._inputs(context, node, states), starting=True)
                self._record(context, step, result, node)
                states[node["id"]] = StepState(node["id"], node["type"], result.status, result.stored_output())
                evaluated[node["id"]] = result
        except RunRequestError as exc:
            # The caller rolls the whole start back, so nothing evaluated so far happened.
            context.events.clear()
            log_event(logger, "workflow_run_rejected", level=logging.WARNING, **self._run_fields(context),
                      code=exc.code, status_code=exc.status_code)
            raise
        self._settle(context.run, steps, context)
        context.events.insert(0, ("workflow_run_started", logging.INFO, {
            **self._run_fields(context), "project_id": context.run.project_id,
            "retry_of_id": context.run.retry_of_id, "nodes": len(steps), "status": context.run.status}))
        self._flush(context)
        return RunProgress(steps, evaluated)

    def advance_run(self, context: ExecutionContext) -> RunProgress:
        """Evaluate pending steps whose parents have all completed, then update the run status."""
        # Serialize passes over one run, so two workers finishing sibling steps cannot
        # each miss the other's completion and leave a shared child pending forever.
        self._lock_run(context)
        steps = list(context.db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == context.run.id)
                                        .order_by(WorkflowRunStep.position)
                                        .execution_options(populate_existing=True)))
        context.steps = {step.node_id: step for step in steps}
        states = {step.node_id: StepState(step.node_id, step.node_type, step.status, _stored_output(step))
                  for step in steps}
        evaluated = {}
        for node, _ in ordered_nodes(context.graph):
            step = context.steps.get(node["id"])
            if step is None or step.status != SKIPPED:
                continue
            inputs = self._inputs(context, node, states)
            if not inputs.ready:
                continue
            result = self._evaluate(context, node, inputs, starting=False)
            self._record(context, step, result, node)
            states[node["id"]] = StepState(node["id"], node["type"], result.status, result.stored_output())
            evaluated[node["id"]] = result
        previous = context.run.status
        self._settle(context.run, steps, context)
        if context.run.status != previous:
            context.events.append(("workflow_run_status_changed", logging.INFO, {
                **self._run_fields(context), "previous_status": previous, "status": context.run.status}))
            # Completed, failed, needs attention or awaiting review: tell the studio (same transaction).
            notifications.run_status_changed(context.db, context.run, previous, context.run.status)
            # A movie source a finished run used becomes ready again, or completed after a success.
            movie_sources.run_status_changed(context.db, context.run, previous, context.run.status)
        self._flush(context)
        return RunProgress(steps, evaluated)

    def finish_step(self, context: ExecutionContext, step: WorkflowRunStep,
                    result: NodeExecutionResult) -> RunProgress:
        """Record the outcome of a step's asynchronous work, then continue the run."""
        self._lock_run(context)
        self._record(context, step, result)
        return self.advance_run(context)

    def readiness(self, context: ExecutionContext) -> list[tuple[dict, NodeReadiness]]:
        """Pre-run checks per node: settings first, then required inputs, then the handler's own."""
        return [(node, self._readiness(context, node)) for node in context.graph["nodes"]]

    def _readiness(self, context, node) -> NodeReadiness:
        handler = self.registry.resolve(node["type"])
        try:
            handler.validate_config(node.get("config"))
        except ConfigError as exc:
            return NodeReadiness("invalid_settings", INVALID_CONFIG_DETAIL, code=exc.code, field=exc.field)
        if missing := unsatisfied_inputs(context.graph, self.registry, node):
            return NodeReadiness("missing_input", handler.missing_input_detail, code="missing_input",
                                 field=missing[0])
        return handler.readiness(context, node)

    def _inputs(self, context, node, states) -> NodeInputs:
        return resolve_node_inputs(node, context.graph, states, registry=self.registry, context=context)

    def _evaluate(self, context, node, inputs, *, starting: bool) -> NodeExecutionResult:
        if not inputs.ready:
            return NodeExecutionResult.waiting()
        handler = self.registry.resolve(node["type"])
        try:
            handler.validate_config(node.get("config"))
        except ConfigError as exc:
            return NodeExecutionResult.blocked(INVALID_CONFIG_DETAIL, NodeError(exc.code, exc.message),
                                               output={"invalid_setting": exc.field})
        try:
            if missing := handler.missing_inputs(context, inputs):
                return NodeExecutionResult.blocked(
                    handler.missing_input_detail, NodeError("missing_input", f"Needs input: {' or '.join(missing)}"),
                    output={"missing_inputs": missing})
            if context.run is not None:
                context.events.append(("workflow_step_started", logging.INFO, {
                    **self._run_fields(context), "step_id": context.steps[node["id"]].id if node["id"] in context.steps
                    else None, "node_id": node["id"], "node_type": node["type"]}))
            result = handler.execute(context, node, inputs)
            if not isinstance(result, NodeExecutionResult):
                raise TypeError(f"{type(handler).__name__} returned {type(result).__name__}")
            return result
        except RunRequestError as exc:
            if starting:
                raise
            # Nobody is waiting on an HTTP response any more; stop at this step instead.
            return NodeExecutionResult.blocked(exc.step_detail, NodeError(exc.code, exc.detail))
        except SQLAlchemyError:
            raise
        except Exception as exc:
            # A handler bug fails its own step; completed steps and the rest of the run stay intact.
            logger.exception("Node %s (%s) failed in run %s", node["id"], node["type"],
                             context.run.id if context.run else None)
            return NodeExecutionResult.failed(
                NodeError("handler_error", f"{type(exc).__name__} while executing {node['type']}"),
                detail=HANDLER_FAILED_DETAIL)

    def _record(self, context: ExecutionContext, step: WorkflowRunStep, result: NodeExecutionResult,
                node: Mapping[str, Any] | None = None) -> None:
        """Store a step's result, enqueue its job, and note what happened for the log."""
        self._apply(step, result, context)
        self._enqueue(context, step, result)
        event = STEP_EVENTS.get(result.status)
        if event is None:
            return
        fields = {**self._run_fields(context), "step_id": step.id, "node_id": step.node_id,
                  "node_type": step.node_type, "status": result.status}
        if result.error is not None:
            fields.update(error_code=result.error.code, retryable=result.error.retryable)
        if result.job is not None:
            # What will be sent to the provider, as resolved from the node's settings in the run snapshot.
            fields.update(job_id=result.job_id, job=payload_summary(result.job.payload))
        elif result.jobs:
            fields["jobs"] = [{"job_id": job_id, **payload_summary(job.payload)}
                              for job_id, job in zip(result.job_ids, result.jobs)]
        if result.all_jobs and node is not None:
            fields["settings"] = config_summary(self.registry.resolve(step.node_type), node.get("config"))
        level = logging.WARNING if result.status in (FAILED, NEEDS_ATTENTION) else logging.INFO
        context.events.append((event, level, fields))
        if result.metadata.get("credits_reserved"):
            references = result.metadata.get("credit_references")
            context.events.append(("credit_reserved", logging.INFO, {
                **self._run_fields(context), "step_id": step.id, "credits": result.metadata["credits_reserved"],
                **({"references": references} if references else
                   {"reference": result.metadata.get("credit_reference")})}))

    @staticmethod
    def _run_fields(context: ExecutionContext) -> dict[str, Any]:
        run = context.run
        return {"workspace_id": context.workspace.id if context.workspace else None,
                "workflow_id": run.workflow_id if run else None, "run_id": run.id if run else None}

    @staticmethod
    def _flush(context: ExecutionContext) -> None:
        for event, level, fields in context.events:
            log_event(logger, event, level=level, **fields)
        context.events.clear()

    @staticmethod
    def _apply(step: WorkflowRunStep, result: NodeExecutionResult, context: ExecutionContext) -> None:
        output = result.stored_output()
        step.status = result.status
        step.detail = result.detail
        step.output = json.dumps(output) if output is not None else None
        step.finished_at = None if result.status in OPEN_STATUSES else context.now

    @staticmethod
    def _enqueue(context: ExecutionContext, step: WorkflowRunStep, result: NodeExecutionResult) -> None:
        if not result.all_jobs:
            return
        context.db.flush()
        ids = []
        for request in result.all_jobs:
            job = jobs.enqueue_job(context.db, workspace_id=context.workspace.id, run_id=context.run.id,
                                   step_id=step.id,
                                   logical_key=request.logical_key or f"{request.kind}:{context.run.id}:{step.id}",
                                   payload=request.payload)
            ids.append(job.id)
        result.job_ids = tuple(ids)
        result.job_id = ids[0]

    @staticmethod
    def _lock_run(context: ExecutionContext) -> None:
        context.db.execute(select(WorkflowRun.id).where(WorkflowRun.id == context.run.id).with_for_update())

    @staticmethod
    def _settle(run: WorkflowRun, steps: list[WorkflowRunStep], context: ExecutionContext) -> None:
        run.status = derive_run_status(step.status for step in steps)
        run.finished_at = None if run.status in (RUNNING, AWAITING_REVIEW) else context.now


default_executor = WorkflowExecutor()
