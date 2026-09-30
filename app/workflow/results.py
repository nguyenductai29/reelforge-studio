"""Node results and the step/run status vocabulary.

Statuses are the values already stored in ``workflow_run_steps.status`` and
``workflow_runs.status``; there is no separate engine status system. Two
names differ from generic workflow terms:

* ``skipped`` means *pending*: the step waits for its parents and is evaluated
  again whenever an upstream step completes.
* ``awaiting_review`` means *needs review*.
"""
from dataclasses import dataclass, field
from typing import Any, Mapping

SKIPPED = "skipped"
QUEUED = "queued"
SUBMITTING = "submitting"  # set by workers while a provider request is in flight
RUNNING = "running"
COMPLETED = "completed"
BLOCKED = "blocked"
AWAITING_REVIEW = "awaiting_review"
FAILED = "failed"
NEEDS_ATTENTION = "needs_attention"

STEP_STATUSES = frozenset({SKIPPED, QUEUED, SUBMITTING, RUNNING, COMPLETED, BLOCKED,
                           AWAITING_REVIEW, FAILED, NEEDS_ATTENTION})
ACTIVE_STATUSES = frozenset({QUEUED, SUBMITTING, RUNNING})
# Steps in these states have no finished_at yet.
OPEN_STATUSES = ACTIVE_STATUSES | {SKIPPED, AWAITING_REVIEW}

WAITING_DETAIL = "Chờ bước phía trước hoàn thành."


class RunRequestError(Exception):
    """The run request itself is unacceptable; the API rolls back and returns this status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class NodeError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class JobRequest:
    """Durable work to enqueue once the step exists; ``kind`` prefixes the job key (``video:<run>:<step>``)."""

    kind: str
    payload: Mapping[str, Any]


@dataclass
class NodeExecutionResult:
    """What a handler decided for one node.

    ``detail`` is the user-facing sentence stored on the step. ``metadata`` is
    for the caller and is not persisted. ``job_id`` is filled in by the executor
    after it enqueues ``job``.
    """

    status: str
    detail: str = ""
    output: Mapping[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: NodeError | None = None
    job: JobRequest | None = None
    job_id: str | None = None
    asset_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if self.status not in STEP_STATUSES:
            raise ValueError(f"Unknown step status: {self.status}")
        if (self.status == QUEUED) != (self.job is not None):
            raise ValueError("Queued results, and only queued results, carry a job")
        if self.status == FAILED and self.error is None:
            raise ValueError("Failed results need an error")
        self.asset_ids = tuple(self.asset_ids)

    @classmethod
    def completed(cls, detail: str, output: Mapping[str, Any] | None = None, **extra) -> "NodeExecutionResult":
        return cls(COMPLETED, detail, output, **extra)

    @classmethod
    def blocked(cls, detail: str, error: NodeError | None = None, **extra) -> "NodeExecutionResult":
        return cls(BLOCKED, detail, error=error, **extra)

    @classmethod
    def queued(cls, detail: str, job: JobRequest, output: Mapping[str, Any] | None = None, **extra) -> "NodeExecutionResult":
        return cls(QUEUED, detail, output, job=job, **extra)

    @classmethod
    def awaiting_review(cls, detail: str, **extra) -> "NodeExecutionResult":
        return cls(AWAITING_REVIEW, detail, **extra)

    @classmethod
    def failed(cls, error: NodeError, detail: str | None = None, **extra) -> "NodeExecutionResult":
        return cls(FAILED, detail if detail is not None else error.message, error=error, **extra)

    @classmethod
    def waiting(cls) -> "NodeExecutionResult":
        return cls(SKIPPED, WAITING_DETAIL)

    def stored_output(self) -> dict[str, Any] | None:
        """The JSON saved in ``workflow_run_steps.output``."""
        output = dict(self.output) if self.output is not None else None
        if self.asset_ids:
            output = {**(output or {}), "asset_ids": list(self.asset_ids)}
        if self.error is not None:
            output = {**(output or {}), "error": {"code": self.error.code, "retryable": self.error.retryable}}
        return output


@dataclass(frozen=True)
class NodeReadiness:
    """Pre-run check for one node, as returned by ``GET /api/workflows/{id}/readiness``."""

    status: str
    detail: str
    credits: int = 0


def produced_asset_ids(output: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Assets a step created (not ones it merely listed)."""
    if not isinstance(output, Mapping):
        return ()
    ids = output.get("asset_ids")
    if isinstance(ids, list):
        return tuple(value for value in ids if isinstance(value, str) and value)
    single = output.get("asset_id")
    return (single,) if isinstance(single, str) and single else ()


def derive_run_status(statuses) -> str:
    """Run status from its step statuses; an unfinished step keeps the run open."""
    present = set(statuses)
    if present & ACTIVE_STATUSES:
        return RUNNING
    if AWAITING_REVIEW in present:
        return AWAITING_REVIEW
    if NEEDS_ATTENTION in present:
        return NEEDS_ATTENTION
    if FAILED in present:
        return FAILED
    if present == {COMPLETED}:
        return COMPLETED
    return BLOCKED
