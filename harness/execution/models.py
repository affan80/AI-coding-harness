"""Executor result models.

``StepOutcome`` is the executor's structured report for one plan step. It
follows the core model conventions (frozen value object, ``to_dict`` /
``from_dict``) so the orchestrator can persist it as evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.core.errors import SerializationError
from harness.core.models import EvidenceRef

SCHEMA_VERSION = 1


class FailureKind(StrEnum):
    """Distinct, machine-checkable failure classes for a step outcome."""

    NONE = "NONE"
    TOOL = "TOOL"
    PATCH_CONFLICT = "PATCH_CONFLICT"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"
    TEST = "TEST"
    PLAN = "PLAN"
    ENVIRONMENT = "ENVIRONMENT"


@dataclass(frozen=True)
class EditRequest:
    """Typed tool-request arguments for one EDIT step.

    The planner/model layer produces the diff; the executor only applies it
    through the patch tool. ``expected_old_hash`` pins the patch to the exact
    file content it was computed against — a stale hash refuses the write.
    """

    path: str
    diff: str
    expected_old_hash: str | None = None
    """When None, the executor hashes the file at execution time."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "diff": self.diff,
            "expected_old_hash": self.expected_old_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditRequest:
        return cls(
            path=data["path"],
            diff=data["diff"],
            expected_old_hash=data.get("expected_old_hash"),
        )


@dataclass(frozen=True)
class StepOutcome:
    """Structured result of executing one plan step."""

    step_id: str
    goal_id: str
    ok: bool
    summary: str
    failure_kind: FailureKind = FailureKind.NONE
    changed_files: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    detail: str = ""

    @property
    def failed(self) -> bool:
        return not self.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "step_id": self.step_id,
            "goal_id": self.goal_id,
            "ok": self.ok,
            "summary": self.summary,
            "failure_kind": self.failure_kind.value,
            "changed_files": list(self.changed_files),
            "evidence": [ref.to_dict() for ref in self.evidence],
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StepOutcome:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise SerializationError(
                "unsupported step outcome schema_version",
                details={"field": "schema_version", "value": repr(data.get("schema_version"))},
            )
        try:
            failure_kind = FailureKind(data.get("failure_kind", FailureKind.NONE.value))
        except ValueError:
            raise SerializationError(
                f"unknown FailureKind {data.get('failure_kind')!r}",
                details={"field": "failure_kind"},
            ) from None
        return cls(
            step_id=data["step_id"],
            goal_id=data["goal_id"],
            ok=data["ok"],
            summary=data["summary"],
            failure_kind=failure_kind,
            changed_files=tuple(data.get("changed_files", ())),
            evidence=tuple(EvidenceRef.from_dict(item) for item in data.get("evidence", ())),
            detail=data.get("detail", ""),
        )
