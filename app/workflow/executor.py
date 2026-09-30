"""Evaluates a run's graph node by node through the handler registry.

A run is evaluated in passes over the snapshot, in topological order:

* ``start_run`` creates one step per node. A node whose parents all completed
  is handed to its handler; any other node stays ``skipped`` (pending).
* ``advance_run`` re-evaluates pending steps after an external event, such as
  a worker finishing a job (``finish_step``) or a reviewer approving.

A step that did not complete never lets its children run, and a pass never
rewrites a step that already left the pending state. Every change happens in
the caller's transaction.
"""
from dataclasses import dataclass
import json
import logging
from typing import Any, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import jobs
from app.models import WorkflowRun, WorkflowRunStep
from app.workflow.context import ExecutionContext, StepState, resolve_inputs
from app.workflow.graph import ordered_nodes
from app.workflow.registry import NodeRegistry, default_registry
from app.workflow.results import (AWAITING_REVIEW, OPEN_STATUSES, RUNNING, SKIPPED, NodeError,
                                  NodeExecutionResult, NodeReadiness, RunRequestError, derive_run_status)

logger = logging.getLogger(__name__)

HANDLER_FAILED_DETAIL = "Bước gặp lỗi khi thực thi."


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
        """Evaluate a new run and persist its steps, queued jobs and status."""
        states: dict[str, StepState] = {}
        evaluated = []
        for node, parent_ids in ordered_nodes(context.graph):
            result = self._evaluate(context, node, resolve_inputs(node, parent_ids, states))
            states[node["id"]] = StepState(node["id"], node["type"], result.status, result.stored_output())
            evaluated.append((node, result))
        steps = []
        for position, (node, result) in enumerate(evaluated):
            step = WorkflowRunStep(id=str(uuid.uuid4()), run_id=context.run.id, node_id=node["id"],
                                   node_type=node["type"], position=position)
            self._apply(step, result, context)
            steps.append(step)
        context.db.add_all(steps)
        context.db.flush()
        for step, (_, result) in zip(steps, evaluated):
            self._enqueue(context, step, result)
        self._settle(context.run, steps, context)
        return RunProgress(steps, {node["id"]: result for node, result in evaluated})

    def advance_run(self, context: ExecutionContext) -> RunProgress:
        """Evaluate pending steps whose parents have all completed, then update the run status."""
        steps = list(context.db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.run_id == context.run.id)
                                        .order_by(WorkflowRunStep.position)))
        by_node = {step.node_id: step for step in steps}
        states = {step.node_id: StepState(step.node_id, step.node_type, step.status, _stored_output(step))
                  for step in steps}
        evaluated = {}
        for node, parent_ids in ordered_nodes(context.graph):
            step = by_node.get(node["id"])
            if step is None or step.status != SKIPPED:
                continue
            inputs = resolve_inputs(node, parent_ids, states)
            if not inputs.ready:
                continue
            result = self._evaluate(context, node, inputs)
            self._apply(step, result, context)
            self._enqueue(context, step, result)
            states[node["id"]] = StepState(node["id"], node["type"], result.status, result.stored_output())
            evaluated[node["id"]] = result
        self._settle(context.run, steps, context)
        return RunProgress(steps, evaluated)

    def finish_step(self, context: ExecutionContext, step: WorkflowRunStep,
                    result: NodeExecutionResult) -> RunProgress:
        """Record the outcome of a step's asynchronous work, then continue the run."""
        self._apply(step, result, context)
        self._enqueue(context, step, result)
        return self.advance_run(context)

    def readiness(self, context: ExecutionContext) -> list[tuple[dict, NodeReadiness]]:
        return [(node, self.registry.resolve(node["type"]).readiness(context, node))
                for node in context.graph["nodes"]]

    def _evaluate(self, context, node, inputs) -> NodeExecutionResult:
        if not inputs.ready:
            return NodeExecutionResult.waiting()
        handler = self.registry.resolve(node["type"])
        try:
            result = handler.execute(context, node, inputs)
            if not isinstance(result, NodeExecutionResult):
                raise TypeError(f"{type(handler).__name__} returned {type(result).__name__}")
            return result
        except (RunRequestError, SQLAlchemyError):
            raise
        except Exception as exc:
            # A handler bug fails its own step; completed steps and the rest of the run stay intact.
            logger.exception("Node %s (%s) failed in run %s", node["id"], node["type"],
                             context.run.id if context.run else None)
            return NodeExecutionResult.failed(
                NodeError("handler_error", f"{type(exc).__name__} while executing {node['type']}"),
                detail=HANDLER_FAILED_DETAIL)

    @staticmethod
    def _apply(step: WorkflowRunStep, result: NodeExecutionResult, context: ExecutionContext) -> None:
        output = result.stored_output()
        step.status = result.status
        step.detail = result.detail
        step.output = json.dumps(output) if output is not None else None
        step.finished_at = None if result.status in OPEN_STATUSES else context.now

    @staticmethod
    def _enqueue(context: ExecutionContext, step: WorkflowRunStep, result: NodeExecutionResult) -> None:
        if result.job is None:
            return
        context.db.flush()
        job = jobs.enqueue_job(context.db, workspace_id=context.workspace.id, run_id=context.run.id,
                               step_id=step.id, logical_key=f"{result.job.kind}:{context.run.id}:{step.id}",
                               payload=result.job.payload)
        result.job_id = job.id

    @staticmethod
    def _settle(run: WorkflowRun, steps: list[WorkflowRunStep], context: ExecutionContext) -> None:
        run.status = derive_run_status(step.status for step in steps)
        run.finished_at = None if run.status in (RUNNING, AWAITING_REVIEW) else context.now


default_executor = WorkflowExecutor()
