"""Serializable session and domain models.

These dataclasses are the shared contracts between workstreams (see
``docs/TEAM_PLAN.md``). The orchestrator owns the authoritative
:class:`Session`; agents, tools, and the product surface only read it.

Design rules:

* every model is an immutable (frozen) value object, so it is safe to share
  with any component without defensive copies;
* every model round-trips through plain JSON-compatible dicts via
  ``to_dict`` / ``from_dict``, so a session can be persisted to
  ``runs/<session-id>/session.json`` and restored exactly;
* enums serialize as their string values, keeping the persisted format
  stable and diffable.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from harness.core.errors import SerializationError

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Status types
# ---------------------------------------------------------------------------


class OrchestrationState(StrEnum):
    """States of the orchestrator state machine (PRD §15).

    ``COMPLETED``, ``FAILED`` and ``CANCELLED`` are terminal; every other
    state is active.
    """

    INITIALIZE = "INITIALIZE"
    UNDERSTAND = "UNDERSTAND"
    INSPECT_REPOSITORY = "INSPECT_REPOSITORY"
    BASELINE = "BASELINE"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    DIAGNOSE = "DIAGNOSE"
    REPLAN = "REPLAN"
    AUDIT = "AUDIT"
    FINALIZE = "FINALIZE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GoalStatus(StrEnum):
    """Lifecycle of a single goal in the goal graph."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class StepKind(StrEnum):
    """What an executor is expected to do for a plan step."""

    INSPECT = "INSPECT"
    EDIT = "EDIT"
    SHELL = "SHELL"
    VERIFY = "VERIFY"


class StepStatus(StrEnum):
    """Lifecycle of a single plan step."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TerminalOutcome(StrEnum):
    """Whether a finished session succeeded, failed, or was cancelled."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TerminalReason(StrEnum):
    """Why a session stopped. Persisted on the session (PRD §20)."""

    ALL_GOALS_VERIFIED = "ALL_GOALS_VERIFIED"
    NO_CONFIRMED_WORK_REMAINING = "NO_CONFIRMED_WORK_REMAINING"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PLAN_FAILED = "PLAN_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    USER_CANCELLED = "USER_CANCELLED"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class UserRequest:
    """What the user asked the harness to do (PRD FR-01..FR-03)."""

    objective: str
    repository_path: str
    request_id: str = field(default_factory=_new_id)
    constraints: tuple[str, ...] = ()
    """Free-form restrictions the user placed on the work."""
    scope_paths: tuple[str, ...] = ()
    """Paths the user approved for modification; empty means the whole repo."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "objective": self.objective,
            "repository_path": self.repository_path,
            "constraints": list(self.constraints),
            "scope_paths": list(self.scope_paths),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UserRequest:
        return cls(
            request_id=_require(data, "request_id", "UserRequest"),
            objective=_require(data, "objective", "UserRequest"),
            repository_path=_require(data, "repository_path", "UserRequest"),
            constraints=tuple(data.get("constraints", ())),
            scope_paths=tuple(data.get("scope_paths", ())),
        )


@dataclass(frozen=True)
class EvidenceRef:
    """Pointer to a persisted artifact that backs a claim (PRD §21).

    ``kind`` is an open vocabulary (``"budget_snapshot"``,
    ``"verification_report"``, ``"tool_call"``, ...) because every
    workstream produces its own evidence. ``path``/``sha256`` reference the
    artifact inside the run directory when one exists.
    """

    kind: str
    description: str = ""
    ref_id: str = field(default_factory=_new_id)
    path: str | None = None
    sha256: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "kind": self.kind,
            "description": self.description,
            "path": self.path,
            "sha256": self.sha256,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceRef:
        return cls(
            ref_id=_require(data, "ref_id", "EvidenceRef"),
            kind=_require(data, "kind", "EvidenceRef"),
            description=data.get("description", ""),
            path=data.get("path"),
            sha256=data.get("sha256"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class Goal:
    """One unit of user-visible outcome tracked by the goal graph."""

    goal_id: str
    title: str
    description: str = ""
    status: GoalStatus = GoalStatus.PENDING
    acceptance_criteria: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "acceptance_criteria": list(self.acceptance_criteria),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Goal:
        return cls(
            goal_id=_require(data, "goal_id", "Goal"),
            title=_require(data, "title", "Goal"),
            description=data.get("description", ""),
            status=_enum_from_dict(
                GoalStatus, "status", data.get("status", GoalStatus.PENDING.value)
            ),
            acceptance_criteria=tuple(data.get("acceptance_criteria", ())),
        )


@dataclass(frozen=True)
class PlanStep:
    """One ordered, verifiable step of an execution plan (PRD FR-06)."""

    step_id: str
    goal_id: str
    title: str
    kind: StepKind = StepKind.INSPECT
    detail: str = ""
    depends_on: tuple[str, ...] = ()
    status: StepStatus = StepStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "goal_id": self.goal_id,
            "title": self.title,
            "kind": self.kind.value,
            "detail": self.detail,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanStep:
        return cls(
            step_id=_require(data, "step_id", "PlanStep"),
            goal_id=_require(data, "goal_id", "PlanStep"),
            title=_require(data, "title", "PlanStep"),
            kind=_enum_from_dict(StepKind, "kind", data.get("kind", StepKind.INSPECT.value)),
            detail=data.get("detail", ""),
            depends_on=tuple(data.get("depends_on", ())),
            status=_enum_from_dict(
                StepStatus, "status", data.get("status", StepStatus.PENDING.value)
            ),
        )


@dataclass(frozen=True)
class Budget:
    """Configurable session constraints (PRD §20, FR-14).

    ``command_timeout_seconds`` is a limit value enforced by the tool layer
    when it runs commands; the other four fields are counters enforced by
    the orchestrator.
    """

    max_model_calls: int = 20
    max_iterations: int = 30
    max_retries_per_goal: int = 3
    max_audit_rounds: int = 2
    command_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        counters = ("max_model_calls", "max_iterations", "max_retries_per_goal", "max_audit_rounds")
        for name in counters:
            if getattr(self, name) < 0:
                raise ValueError(f"Budget.{name} must be >= 0, got {getattr(self, name)}")
        if self.command_timeout_seconds <= 0:
            raise ValueError(
                f"Budget.command_timeout_seconds must be > 0, got {self.command_timeout_seconds}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_iterations": self.max_iterations,
            "max_retries_per_goal": self.max_retries_per_goal,
            "max_audit_rounds": self.max_audit_rounds,
            "command_timeout_seconds": self.command_timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Budget:
        try:
            return cls(
                max_model_calls=data.get("max_model_calls", 20),
                max_iterations=data.get("max_iterations", 30),
                max_retries_per_goal=data.get("max_retries_per_goal", 3),
                max_audit_rounds=data.get("max_audit_rounds", 2),
                command_timeout_seconds=data.get("command_timeout_seconds", 120.0),
            )
        except ValueError as exc:
            raise SerializationError(str(exc), details={"field": "budget"}) from exc


@dataclass(frozen=True)
class BudgetUsage:
    """Counters consumed against a :class:`Budget` so far.

    ``retries_by_goal`` maps goal id to retry count; it is treated as
    immutable — the orchestrator always builds a new mapping instead of
    mutating one.
    """

    model_calls: int = 0
    iterations: int = 0
    audit_rounds: int = 0
    retries_by_goal: Mapping[str, int] = field(default_factory=dict)

    def retries_for(self, goal_id: str) -> int:
        return self.retries_by_goal.get(goal_id, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "iterations": self.iterations,
            "audit_rounds": self.audit_rounds,
            "retries_by_goal": dict(self.retries_by_goal),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BudgetUsage:
        return cls(
            model_calls=data.get("model_calls", 0),
            iterations=data.get("iterations", 0),
            audit_rounds=data.get("audit_rounds", 0),
            retries_by_goal=dict(data.get("retries_by_goal", {})),
        )


@dataclass(frozen=True)
class TransitionRecord:
    """One append-only entry in the session history: a state change."""

    seq: int
    from_state: OrchestrationState
    to_state: OrchestrationState
    at: str
    """ISO-8601 UTC timestamp of the transition."""
    reason: str = ""
    evidence: tuple[EvidenceRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "at": self.at,
            "reason": self.reason,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TransitionRecord:
        return cls(
            seq=_require(data, "seq", "TransitionRecord"),
            from_state=_enum_from_dict(
                OrchestrationState, "from_state", _require(data, "from_state", "TransitionRecord")
            ),
            to_state=_enum_from_dict(
                OrchestrationState, "to_state", _require(data, "to_state", "TransitionRecord")
            ),
            at=_require(data, "at", "TransitionRecord"),
            reason=data.get("reason", ""),
            evidence=tuple(EvidenceRef.from_dict(item) for item in data.get("evidence", ())),
        )


@dataclass(frozen=True)
class TerminalInfo:
    """Why a session stopped, with the evidence that justifies it."""

    outcome: TerminalOutcome
    reason: TerminalReason
    detail: str = ""
    evidence: tuple[EvidenceRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason.value,
            "detail": self.detail,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TerminalInfo:
        return cls(
            outcome=_enum_from_dict(
                TerminalOutcome, "outcome", _require(data, "outcome", "TerminalInfo")
            ),
            reason=_enum_from_dict(
                TerminalReason, "reason", _require(data, "reason", "TerminalInfo")
            ),
            detail=data.get("detail", ""),
            evidence=tuple(EvidenceRef.from_dict(item) for item in data.get("evidence", ())),
        )


@dataclass(frozen=True)
class Session:
    """Authoritative state of one harness run.

    The class is frozen: nobody can mutate a session in place. The
    orchestrator (:class:`harness.core.orchestrator.Orchestrator`) is the
    only component that produces a new session for the next state.
    """

    session_id: str
    request: UserRequest
    state: OrchestrationState
    created_at: str
    updated_at: str
    budget: Budget = field(default_factory=Budget)
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    goals: tuple[Goal, ...] = ()
    steps: tuple[PlanStep, ...] = ()
    history: tuple[TransitionRecord, ...] = ()
    terminal: TerminalInfo | None = None

    @classmethod
    def create(
        cls,
        request: UserRequest,
        *,
        budget: Budget | None = None,
        session_id: str | None = None,
        at: str | None = None,
    ) -> Session:
        """Create a fresh session in the INITIALIZE state."""
        now = at or _utc_now_iso()
        return cls(
            session_id=session_id or _new_id(),
            request=request,
            state=OrchestrationState.INITIALIZE,
            created_at=now,
            updated_at=now,
            budget=budget or Budget(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state.value,
            "request": self.request.to_dict(),
            "budget": self.budget.to_dict(),
            "usage": self.usage.to_dict(),
            "goals": [goal.to_dict() for goal in self.goals],
            "steps": [step.to_dict() for step in self.steps],
            "history": [record.to_dict() for record in self.history],
            "terminal": self.terminal.to_dict() if self.terminal else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Session:
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise SerializationError(
                f"unsupported session schema_version {version!r}, expected {SCHEMA_VERSION}",
                details={"field": "schema_version", "value": repr(version)},
            )
        terminal_raw = data.get("terminal")
        return cls(
            session_id=_require(data, "session_id", "Session"),
            request=UserRequest.from_dict(_require(data, "request", "Session")),
            state=_enum_from_dict(
                OrchestrationState, "state", _require(data, "state", "Session")
            ),
            created_at=_require(data, "created_at", "Session"),
            updated_at=_require(data, "updated_at", "Session"),
            budget=Budget.from_dict(data.get("budget", {})),
            usage=BudgetUsage.from_dict(data.get("usage", {})),
            goals=tuple(Goal.from_dict(item) for item in data.get("goals", ())),
            steps=tuple(PlanStep.from_dict(item) for item in data.get("steps", ())),
            history=tuple(TransitionRecord.from_dict(item) for item in data.get("history", ())),
            terminal=TerminalInfo.from_dict(terminal_raw) if terminal_raw else None,
        )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _require(data: Mapping[str, Any], key: str, model: str) -> Any:
    if key not in data:
        raise SerializationError(
            f"missing required field {key!r} on {model}",
            details={"field": key, "model": model},
        )
    return data[key]


def _enum_from_dict(enum_cls: type[StrEnum], field_name: str, raw: Any) -> StrEnum:
    try:
        return enum_cls(raw)
    except ValueError:
        raise SerializationError(
            f"unknown {enum_cls.__name__} value {raw!r}",
            details={"field": field_name, "value": repr(raw)},
        ) from None
