"""Shared by the image, video and voice handlers: one paid provider operation per scene or file.

A step that makes several files queues one durable job per operation, with its
own logical key (``<kind>:<step>:<operation>``) and its own credit references:

* ``<kind>-reserve:<step>:<operation>``: held when the step is queued;
* ``<kind>:<step>:<operation>``: the usage event when the file is stored;
* ``<kind>-refund:<step>:<operation>``: when the provider definitely did not accept it.

The operation is ``scene:<n>`` in scene mode, ``image:<n>`` for the n-th image
of one prompt, or ``single`` for one video clip or narration. Credits for every operation are
reserved together, after a check that the balance covers all of them, so a step
never holds part of its cost and the balance never goes negative. The workers
(app/media_jobs.py) settle the step once every job has finished.
"""
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select

from app import usage
from app.models import CreditAccount
from app.workflow.nodes.base import INSUFFICIENT_CREDITS_DETAIL
from app.workflow.ports import SCENES, bind_edges
from app.workflow.results import JobRequest, NodeExecutionResult, RunRequestError

# The scene splitter makes at most 20 scenes; the same bound applies to any list of scenes.
MAX_OPERATIONS = 20
MAX_PROMPT_CHARS = 1000


@dataclass(frozen=True)
class Operation:
    key: str
    prompt: str
    scene_index: int | None = None


def clip(text) -> str:
    """Text as a provider prompt, cut at a word boundary to 1,000 characters (Runway's limit)."""
    if not isinstance(text, str) or not text.strip():
        return ""
    text = " ".join(text.split())
    return text if len(text) <= MAX_PROMPT_CHARS else text[:MAX_PROMPT_CHARS].rsplit(" ", 1)[0]


def normalize(text) -> str:
    """Text with its whitespace collapsed, never cut (a narration must not lose words)."""
    return " ".join(text.split()) if isinstance(text, str) else ""


def scene_operations(scenes, keys=("visual_prompt", "text"), shorten=clip) -> list[Operation]:
    """One operation per scene with a prompt: the first of ``keys`` it has (``visual_prompt``, else ``text``).

    The scene's own index is kept; a missing or repeated index (for example when
    two scene lists are joined) falls back to the scene's position.
    """
    operations, used = [], set()
    for position, scene in enumerate(scenes if isinstance(scenes, list) else [], 1):
        if not isinstance(scene, Mapping):
            continue
        prompt = next((text for text in (shorten(scene.get(key)) for key in keys) if text), "")
        if not prompt:
            continue
        index = scene.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 1 or index in used:
            index = position
            while index in used:
                index += 1
        used.add(index)
        operations.append(Operation(f"scene:{index}", prompt, index))
    return operations


def references(kind: str, step_id: str, operation: str) -> dict[str, str]:
    return {"reserve_reference": f"{kind}-reserve:{step_id}:{operation}",
            "usage_reference": f"{kind}:{step_id}:{operation}",
            "refund_reference": f"{kind}-refund:{step_id}:{operation}"}


def connected_inputs(context, node) -> set[str]:
    """The input ports of this node that an edge feeds."""
    return {binding.input.name for binding in bind_edges(context.graph, context_registry())
            if binding is not None and binding.target == node["id"]}


def scene_mode_expected(context, node) -> bool:
    """Whether a ``scenes`` edge feeds this node (readiness cannot know the scene count yet)."""
    return any(binding is not None and binding.target == node["id"] and binding.input.name == "scenes"
               and binding.output.type == SCENES for binding in bind_edges(context.graph, context_registry()))


def context_registry():
    from app.workflow.registry import default_registry
    return default_registry


def queue_operations(context, node, *, kind: str, mode: str, operations: list[Operation], cost: int,
                     payload: Mapping[str, Any], detail: str,
                     output: Mapping[str, Any] | None = None) -> NodeExecutionResult:
    """Reserve every operation's credits and return one queued job per operation."""
    step = context.step_for(node)
    total = cost * len(operations)
    # Lock the account first, then check the whole cost before reserving anything.
    account = context.db.scalar(select(CreditAccount).where(CreditAccount.workspace_id == context.workspace.id)
                                .with_for_update())
    if account is None or account.balance < total:
        raise RunRequestError(402, "Not enough credits for this step", code="insufficient_credits",
                              step_detail=INSUFFICIENT_CREDITS_DETAIL)
    jobs, reserved = [], []
    for index, operation in enumerate(operations, 1):
        refs = references(kind, step.id, operation.key)
        usage.post_credit(context.db, context.workspace.id, -cost, f"{kind}_reserve", refs["reserve_reference"])
        reserved.append(refs["reserve_reference"])
        jobs.append(JobRequest(kind, {
            **payload, "kind": f"{kind}.generate", "node_id": node["id"], "mode": mode,
            "operation": operation.key, "scene_index": operation.scene_index, "index": index,
            "prompt": operation.prompt, "credits": cost, **refs,
        }, logical_key=f"{kind}:{step.id}:{operation.key}"))
    output = {**(output or {}), "mode": mode, "expected": len(operations), "provider": payload.get("provider"),
              "model": payload.get("model") or payload.get("model_id"),
              "operations": [{"operation": operation.key, "scene_index": operation.scene_index}
                             for operation in operations]}
    return NodeExecutionResult.queued(detail, jobs, output,
                                      metadata={"credits_reserved": total, "credit_references": reserved})
