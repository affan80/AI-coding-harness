"""Workstream C shared contract: verification, failure classes, finding states.

Self-contained by team rule: standard library only, no imports from other
contract modules. Consumers (recovery, audit, orchestrator, evidence) code
against these dataclasses and enums; cross-workstream wiring happens at
integration (issue #20). See PRD §§16, 17, 18 and FR-09, FR-10, FR-11.

Stability contract
------------------
Enum *values* are the wire format: ``StageName``, ``StageOutcome``,
``VerificationStatus``, ``FailureClass``, and ``FindingStatus`` values are
persisted in run directories (``verification.json``, ``recovery.json``,
``findings.json``) and referenced by recovery and audit decisions, so they
must never be renamed — new members may be appended. ``to_dict()`` /
``from_dict()`` round-trip without loss, and ``from_dict`` raises
:class:`VerificationContractError` on unknown enum values or missing fields
rather than guessing.
"""

from __future__ import annotations

import types as _types
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from typing import Any, Union, get_args, get_origin, get_type_hints

SCHEMA_VERSION = 1


class VerificationContractError(ValueError):
    """Raised when a verification contract payload cannot be validated or decoded."""


class StageName(StrEnum):
    """Verification ladder stages in PRD §16 order (cheapest first)."""

    SYNTAX = "syntax"
    BUILD = "build"
    REPRODUCER = "reproducer"
    TARGETED_TESTS = "targeted_tests"
    RELATED_TESTS = "related_tests"
    FULL_SUITE = "full_suite"
    DIFF_SCOPE = "diff_scope"


class StageOutcome(StrEnum):
    """What happened at one considered stage."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"  # not applicable to this goal/project
    UNAVAILABLE = "unavailable"  # applicable but no runnable command


class VerificationStatus(StrEnum):
    """Report-level outcome. INCONCLUSIVE exists so missing test evidence
    can never be reported as VERIFIED."""

    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class GoalKind(StrEnum):
    """Why a goal exists; audit goals originate from confirmed findings."""

    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    AUDIT = "audit"


class FailureClass(StrEnum):
    """The ten failure classes from PRD §17.

    ENVIRONMENT covers failures where the harness could not run the check
    at all (missing executable, OS-level error); such failures are runtime
    problems, never source-code defects.
    """

    SYNTAX = "syntax"
    BUILD = "build"
    TEST = "test"
    PATCH = "patch"
    TOOL = "tool"
    TIMEOUT = "timeout"
    PLAN = "plan"
    CONTEXT = "context"
    LOOP = "loop"
    ENVIRONMENT = "environment"


class FindingStatus(StrEnum):
    """Audit finding lifecycle from PRD §18, plus the REJECTED exit.

    Deterministic evidence (a reproducer run or tool result) is required to
    reach REPRODUCED and CONFIRMED; model observations alone stay SUSPECTED
    and can never authorize source changes.
    """

    SUSPECTED = "suspected"
    REPRODUCED = "reproduced"
    CONFIRMED = "confirmed"
    PATCHED = "patched"
    VERIFIED = "verified"
    REJECTED = "rejected"


DETERMINISTIC_EVIDENCE_KINDS = frozenset({"reproducer", "tool_result"})


class RecoveryDecision(StrEnum):
    """What the orchestrator does after recovery diagnosis (PRD §20)."""

    RETRY = "retry"
    REPLAN = "replan"
    ROLLBACK = "rollback"
    STOP = "stop"


@dataclass(frozen=True)
class StageResult:
    """One considered ladder stage and its deterministic outcome."""

    name: StageName
    outcome: StageOutcome
    command: str | None = None  # exact argv, when the stage ran
    exit_code: int | None = None
    duration_ms: int = 0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageResult:
        return _decode(cls, data)


@dataclass(frozen=True)
class VerificationReport:
    """Structured ladder outcome persistable as verification.json (PRD §21)."""

    goal_id: str
    status: VerificationStatus
    stages: tuple[StageResult, ...] = ()
    first_failure: StageResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationReport:
        return _decode(cls, data)


@dataclass(frozen=True)
class AuditFinding:
    """One audit finding moving through the PRD §18 lifecycle.

    ``evidence`` holds the deterministic references that justified each
    transition; a SUSPECTED finding with empty deterministic evidence
    cannot leave its state, and only CONFIRMED-or-later authorizes repair.
    """

    finding_id: str
    title: str
    description: str
    source: str  # "llm_review" | "baseline_check" | ...
    scope_path: str
    status: FindingStatus = FindingStatus.SUSPECTED
    evidence: tuple[EvidenceRef, ...] = ()
    rejection_reason: str | None = None
    round: int = 0

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditFinding:
        return _decode(cls, data)


@dataclass(frozen=True)
class EvidenceRef:
    """Locally duplicated small value type (team rule; reconciled at #20)."""

    kind: str  # open vocabulary; "reproducer"/"tool_result" are deterministic
    ref_id: str
    description: str = ""
    path: str | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRef:
        return _decode(cls, data)


# ---------------------------------------------------------------------------
# Self-contained codec (same semantics as the other contract modules)
# ---------------------------------------------------------------------------


def _encode(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _encode(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if isinstance(obj, dict):
        return {_encode(k): _encode(v) for k, v in obj.items()}
    if isinstance(obj, StrEnum):
        return obj.value
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise TypeError(f"cannot serialize value of type {type(obj)!r}")


def _decode(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v) for v in value)
        return tuple(_decode(a, v) for a, v in zip(args, value, strict=False))
    if origin is Union or origin is _types.UnionType:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _decode(non_none[0], value)
    if origin is dict:
        args = get_args(tp)
        value_type = args[1] if len(args) == 2 else Any
        return {k: _decode(value_type, v) for k, v in value.items()}
    if is_dataclass(tp):
        if not isinstance(value, dict):
            raise VerificationContractError(
                f"expected object for {tp.__name__}, got {type(value)!r}"
            )
        kwargs: dict[str, Any] = {}
        hints = get_type_hints(tp)
        for f in fields(tp):
            if f.name not in value:
                raise VerificationContractError(f"missing field {f.name!r} for {tp.__name__}")
            kwargs[f.name] = _decode(hints[f.name], value[f.name])
        return tp(**kwargs)
    if isinstance(tp, type) and issubclass(tp, StrEnum):
        try:
            return tp(value)
        except ValueError as exc:
            raise VerificationContractError(str(exc)) from exc
    return value
