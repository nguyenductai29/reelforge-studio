"""What a node handler can see: the run's context and each node's resolved inputs."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AITool, Asset, CreditAccount, Project, WorkflowRun, WorkflowRunStep, Workspace, WorkspaceSetting
from app.workflow.graph import count_nodes, parse_graph
from app.workflow.ports import PROJECT_TOPIC, bind_edges, combine, is_empty, valid_value
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
        # The run's steps by node ID, set by the executor before it calls handlers.
        self.steps: dict[str, WorkflowRunStep] = {}

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

    def step_for(self, node: Mapping[str, Any]) -> WorkflowRunStep:
        return self.steps[node["id"]]

    def find_tool(self, task: str, providers, tool_id: str | None = None) -> AITool | None:
        """The first enabled tool for ``task`` with a supported provider, or the one with ``tool_id``."""
        return next((tool for tool in self.enabled_tools
                     if tool.task == task and tool.provider in providers
                     and (tool_id is None or tool.id == tool_id)), None)


@dataclass(frozen=True)
class StepState:
    """A node's current outcome, as its children see it."""

    node_id: str
    node_type: str
    status: str
    output: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class InputSource:
    """Where an input value came from, for debugging and the inspector."""

    port: str
    origin: str  # "edge", "config" or "context"
    node_id: str | None = None
    output: str | None = None


@dataclass(frozen=True)
class NodeInputs:
    """A node's config, its parents' states, and the value of each input port."""

    config: Mapping[str, Any] = field(default_factory=dict)
    parents: tuple[StepState, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[InputSource, ...] = ()

    def get(self, port: str, default: Any = None) -> Any:
        """The resolved value of an input port."""
        return self.values.get(port, default)

    def has(self, port: str) -> bool:
        return port in self.values

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
    """Config and parent states only, without port values; a parent without a known state counts as pending."""
    config = node.get("config")
    return NodeInputs(config=dict(config) if isinstance(config, Mapping) else {},
                      parents=tuple(states.get(parent_id, StepState(parent_id, "", SKIPPED))
                                    for parent_id in parent_ids))


def context_value(name: str | None, context: "ExecutionContext | None") -> Any:
    if name == PROJECT_TOPIC and context is not None and context.project is not None:
        return (context.project.topic or "").strip() or (context.project.title or "").strip() or None
    return None


def resolve_node_inputs(node: Mapping[str, Any], graph: Mapping[str, Any], states: Mapping[str, StepState], *,
                        registry, context: "ExecutionContext | None" = None) -> NodeInputs:
    """Everything ``node`` receives: config, parent states, and one value per input port.

    Port values come from completed parents through the graph's edges, then from
    the node's config, then from the run context (see ``app/workflow/ports.py``).
    """
    # Two nodes may be joined by several edges (one per port pair); each parent counts once.
    parent_ids = list(dict.fromkeys(edge["source"] for edge in graph["edges"] if edge["target"] == node["id"]))
    base = resolve_inputs(node, parent_ids, states)
    handler = registry.resolve(node["type"])
    collected: dict[str, list[Any]] = {}
    sources = []
    for binding in bind_edges(graph, registry):
        if binding is None or binding.target != node["id"]:
            continue
        state = states.get(binding.source)
        if state is None or state.status != COMPLETED:
            continue
        value = binding.output.read(state.output)
        if is_empty(value) or not valid_value(binding.output.type, value):
            continue
        collected.setdefault(binding.input.name, []).append(value)
        sources.append(InputSource(binding.input.name, "edge", binding.source, binding.output.name))
    values = {}
    for port in handler.inputs:
        if port.name in collected:
            values[port.name] = combine(port, collected[port.name])
        elif port.config_key and not is_empty(base.config.get(port.config_key)):
            values[port.name] = base.config[port.config_key]
            sources.append(InputSource(port.name, "config"))
        elif not is_empty(fallback := context_value(port.context, context)):
            values[port.name] = fallback
            sources.append(InputSource(port.name, "context"))
    return NodeInputs(config=base.config, parents=base.parents, values=values, sources=tuple(sources))
