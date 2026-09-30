"""Workflow execution: graph → executor → registry → one handler per node type.

To add a node type, subclass ``NodeHandler`` in ``app/workflow/nodes/`` and
register it in ``build_default_registry``; the API accepts every registered type.
"""
from app.workflow.context import (ExecutionContext, InputSource, NodeInputs, RunOptions, StepState, resolve_inputs,
                                  resolve_node_inputs)
from app.workflow.executor import RunProgress, WorkflowExecutor, default_executor
from app.workflow.graph import ordered_nodes, parse_graph
from app.workflow.nodes import NodeHandler
from app.workflow.registry import NodeRegistry, build_default_registry, default_registry
from app.workflow.results import (JobRequest, NodeError, NodeExecutionResult, NodeReadiness, RunRequestError,
                                  derive_run_status)

__all__ = ["ExecutionContext", "InputSource", "JobRequest", "NodeError", "NodeExecutionResult", "NodeHandler",
           "NodeInputs", "NodeReadiness", "NodeRegistry", "RunOptions", "RunProgress", "RunRequestError", "StepState",
           "WorkflowExecutor", "build_default_registry", "default_executor", "default_registry",
           "derive_run_status", "ordered_nodes", "parse_graph", "resolve_inputs", "resolve_node_inputs"]
