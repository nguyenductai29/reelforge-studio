"""What a node handler can see: the run's context and each node's resolved inputs."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AITool, Asset, CreditAccount, Project, WorkflowRun, Workspace, WorkspaceSetting
from app.workflow.graph import count_nodes, parse_graph
from app.workflow.results import COMPLETED, SKIPPED, produced_asset_ids


@dataclass(frozen=True)
class RunOptions:
    """Inputs of the start or retry request, as opposed to the saved graph."""

    prompt_override: str | None = None
    tool_id: str | None = None
    # Job payload of the video step being retried, so a retry repeats the same request.
    frozen_video: Mapping[str, Any] | None = None


class ExecutionContext:
    """One run's workspace, project, graph and database session.

    ``run`` and ``project`` are ``None`` only for readiness checks, which
    evaluate a saved workflow before any run exists. Lookups are loaded on
    first use and cached for the rest of the evaluation.
    """

    def __init__(self, db: Session, *, workspace: Workspace, graph: dict,
                 run: WorkflowRun | None = None, project: Project | None = None,
                 options: RunOptions | None = None, now: datetime | None = None):
        self.db = db
        self.workspace = workspace
        self.graph = graph
        self.run = run
        self.project = project
        self.options = options or RunOptions()
        self.now = now or datetime.now(timezone.utc)

    @classmethod
    def for_run(cls, db: Session, run: WorkflowRun, **kwargs) -> "ExecutionContext":
        """Context for continuing an existing run from its stored snapshot."""
        return cls(db, workspace=db.get(Workspace, run.workspace_id), graph=parse_graph(run.graph_snapshot),
                   run=run, project=db.get(Project, run.project_id), **kwargs)

    @cached_property
    def enabled_tools(self) -> list[AITool]:
        return list(self.db.scalars(select(AITool).where(AITool.workspace_id == self.workspace.id,
                                                         AITool.is_enabled.is_(True))
                                    .order_by(AITool.created_at, AITool.id)))

    @cached_property
    def enabled_tasks(self) -> frozenset[str]:
        return frozenset(tool.task for tool in self.enabled_tools)

    @cached_property
    def assets(self) -> list[Asset]:
        return list(self.db.scalars(select(Asset).where(Asset.workspace_id == self.workspace.id)
                                    .order_by(Asset.created_at, Asset.id)))

    @cached_property
    def workspace_settings(self) -> dict[str, Any]:
        rows = self.db.scalars(select(WorkspaceSetting).where(WorkspaceSetting.workspace_id == self.workspace.id))
        return {row.key: json.loads(row.value) for row in rows}

    @cached_property
    def credit_balance(self) -> int:
        account = self.db.get(CreditAccount, self.workspace.id)
        return account.balance if account else 0

    def count_nodes(self, node_type: str) -> int:
        return count_nodes(self.graph, node_type)


@dataclass(frozen=True)
class StepState:
    """A node's current outcome, as its children see it."""

    node_id: str
    node_type: str
    status: str
    output: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class NodeInputs:
    """A node's own config plus what its direct parents produced."""

    config: Mapping[str, Any] = field(default_factory=dict)
    parents: tuple[StepState, ...] = ()

    @property
    def ready(self) -> bool:
        """Every parent completed; nodes without parents are always ready."""
        return all(parent.status == COMPLETED for parent in self.parents)

    def outputs(self, node_type: str | None = None) -> list[Mapping[str, Any]]:
        """Outputs of completed parents, optionally of one node type, in edge order."""
        return [parent.output for parent in self.parents
                if parent.status == COMPLETED and parent.output is not None
                and (node_type is None or parent.node_type == node_type)]

    def value(self, key: str, default: Any = None) -> Any:
        """The first parent output that has ``key``."""
        return next((output[key] for output in self.outputs() if key in output), default)

    @property
    def asset_ids(self) -> tuple[str, ...]:
        """Assets created by completed parents."""
        return tuple(asset_id for output in self.outputs() for asset_id in produced_asset_ids(output))


def resolve_inputs(node: Mapping[str, Any], parent_ids: list[str], states: Mapping[str, StepState]) -> NodeInputs:
    """Inputs for ``node``; a parent without a known state counts as still pending."""
    config = node.get("config")
    return NodeInputs(config=dict(config) if isinstance(config, Mapping) else {},
                      parents=tuple(states.get(parent_id, StepState(parent_id, "", SKIPPED))
                                    for parent_id in parent_ids))
