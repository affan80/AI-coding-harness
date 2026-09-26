"""Deterministic verification ladder (PRD §16; FR-09).

Stages run cheapest-first and the ladder stops at the first failed stage.
There is deliberately no model-verdict input anywhere in this API: a goal
reaches VERIFIED only through deterministic stage evidence (issue #14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from harness.telemetry.models import ArtifactRef


class StageName(StrEnum):
    """Verification ladder stages in PRD §16 order."""

    SYNTAX = "syntax"
    BUILD = "build"
    REPRODUCER = "reproducer"
    TARGETED_TESTS = "targeted_tests"
    RELATED_TESTS = "related_tests"
    FULL_SUITE = "full_suite"
    DIFF_SCOPE = "diff_scope"


LADDER_ORDER: tuple[StageName, ...] = (
    StageName.SYNTAX,
    StageName.BUILD,
    StageName.REPRODUCER,
    StageName.TARGETED_TESTS,
    StageName.RELATED_TESTS,
    StageName.FULL_SUITE,
    StageName.DIFF_SCOPE,
)

TEST_BEARING_STAGES: frozenset[StageName] = frozenset(
    {
        StageName.REPRODUCER,
        StageName.TARGETED_TESTS,
        StageName.RELATED_TESTS,
        StageName.FULL_SUITE,
    }
)


class StageOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"  # not applicable to this goal/project
    UNAVAILABLE = "unavailable"  # applicable but no runnable command


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"  # nothing failed, but no test evidence ran


class GoalKind(StrEnum):
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    AUDIT = "audit"


@dataclass(frozen=True)
class StageResult:
    """One considered ladder stage and its deterministic outcome."""

    name: StageName
    outcome: StageOutcome
    command: str | None = None  # exact argv, when the stage ran
    exit_code: int | None = None
    duration_ms: int = 0
    summary: str = ""
    artifact: ArtifactRef | None = None

    def to_dict(self) -> dict:
        data = {
            "name": self.name.value,
            "outcome": self.outcome.value,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "artifact": self.artifact.to_dict() if self.artifact else None,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> StageResult:
        artifact = data.get("artifact")
        return cls(
            name=StageName(data["name"]),
            outcome=StageOutcome(data["outcome"]),
            command=data["command"],
            exit_code=data["exit_code"],
            duration_ms=data["duration_ms"],
            summary=data["summary"],
            artifact=ArtifactRef.from_dict(artifact) if artifact else None,
        )


@dataclass(frozen=True)
class VerificationInput:
    """Everything the ladder needs to know about the goal under verification."""

    goal_id: str
    goal_kind: GoalKind = GoalKind.FEATURE
    reproducer_command: str | None = None  # original bug reproducer, if any
    targeted_tests: tuple[str, ...] = ()
    related_tests: tuple[str, ...] = ()
    diff_text: str | None = None  # accumulated patch evidence for this goal
    allowed_scope: tuple[str, ...] = ()  # empty means the whole repository


@dataclass
class VerificationReport:
    """Structured ladder outcome persistable as verification.json (PRD §21)."""

    goal_id: str
    status: VerificationStatus
    stages: list[StageResult] = field(default_factory=list)
    first_failure: StageResult | None = None

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "status": self.status.value,
            "stages": [stage.to_dict() for stage in self.stages],
            "first_failure": self.first_failure.to_dict() if self.first_failure else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VerificationReport:
        return cls(
            goal_id=data["goal_id"],
            status=VerificationStatus(data["status"]),
            stages=[StageResult.from_dict(stage) for stage in data["stages"]],
            first_failure=(
                StageResult.from_dict(data["first_failure"]) if data.get("first_failure") else None
            ),
        )
