"""Typed contracts for failure diagnosis and bounded recovery.

``FailureEvidence`` is the *only* input recovery code reads: it is a narrow,
purpose-built bundle (PRD §17), so the entire run history can never reach a
model prompt or a recovery decision by accident — the field does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from harness.telemetry.models import ArtifactRef


class FailureClass(StrEnum):
    """The ten failure classes from PRD §17."""

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


class RecoveryDecision(StrEnum):
    """What the orchestrator should do after diagnosis."""

    RETRY = "retry"  # execute the proposed repair, then re-verify
    REPLAN = "replan"  # forced replanning (repeated attempts / bad plan)
    ROLLBACK = "rollback"  # working state no longer useful
    STOP = "stop"  # budgets exhausted or unrepairable


@dataclass(frozen=True)
class AttemptRecord:
    """One prior repair attempt, summarized for comparison."""

    attempt: int
    action: str  # human-readable repair summary
    fingerprint: str  # action fingerprint (tool + normalized args + result)
    outcome: str  # e.g. "failed: targeted_tests"

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "action": self.action,
            "fingerprint": self.fingerprint,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AttemptRecord:
        return cls(
            attempt=data["attempt"],
            action=data["action"],
            fingerprint=data["fingerprint"],
            outcome=data["outcome"],
        )


@dataclass(frozen=True)
class FailureEvidence:
    """Narrow, focused evidence for one failure (PRD §17 recovery input)."""

    failure_class: FailureClass
    concise_error: str  # focused tail of the failing output
    goal_id: str
    stage: str | None = None  # verification stage that failed, if any
    failing_command: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    latest_diff: str | None = None
    affected_paths: tuple[str, ...] = ()
    relevant_tests: tuple[str, ...] = ()
    prior_attempts: tuple[AttemptRecord, ...] = ()
    artifact: ArtifactRef | None = None

    def to_dict(self) -> dict:
        return {
            "failure_class": self.failure_class.value,
            "concise_error": self.concise_error,
            "goal_id": self.goal_id,
            "stage": self.stage,
            "failing_command": self.failing_command,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "latest_diff": self.latest_diff,
            "affected_paths": list(self.affected_paths),
            "relevant_tests": list(self.relevant_tests),
            "prior_attempts": [a.to_dict() for a in self.prior_attempts],
            "artifact": self.artifact.to_dict() if self.artifact else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureEvidence:
        artifact = data.get("artifact")
        return cls(
            failure_class=FailureClass(data["failure_class"]),
            concise_error=data["concise_error"],
            goal_id=data["goal_id"],
            stage=data.get("stage"),
            failing_command=data.get("failing_command"),
            exit_code=data.get("exit_code"),
            timed_out=data.get("timed_out", False),
            latest_diff=data.get("latest_diff"),
            affected_paths=tuple(data.get("affected_paths", ())),
            relevant_tests=tuple(data.get("relevant_tests", ())),
            prior_attempts=tuple(
                AttemptRecord.from_dict(a) for a in data.get("prior_attempts", ())
            ),
            artifact=ArtifactRef.from_dict(artifact) if artifact else None,
        )


@dataclass(frozen=True)
class RepairAction:
    """One bounded step of a recovery plan."""

    kind: str  # "patch" | "command" | "inspect"
    target: str  # file path or command target; must be within allowed scope
    detail: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict) -> RepairAction:
        return cls(kind=data["kind"], target=data["target"], detail=data["detail"])


@dataclass(frozen=True)
class RecoveryPlan:
    """A bounded repair proposal produced through the shared model client."""

    failure_class: FailureClass
    root_cause: str
    repair_summary: str
    repair_actions: tuple[RepairAction, ...] = ()
    reverify_stages: tuple[str, ...] = ()  # StageName values, failed stage first
    within_scope: bool = True

    def to_dict(self) -> dict:
        return {
            "failure_class": self.failure_class.value,
            "root_cause": self.root_cause,
            "repair_summary": self.repair_summary,
            "repair_actions": [a.to_dict() for a in self.repair_actions],
            "reverify_stages": list(self.reverify_stages),
            "within_scope": self.within_scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RecoveryPlan:
        return cls(
            failure_class=FailureClass(data["failure_class"]),
            root_cause=data["root_cause"],
            repair_summary=data["repair_summary"],
            repair_actions=tuple(RepairAction.from_dict(a) for a in data["repair_actions"]),
            reverify_stages=tuple(data["reverify_stages"]),
            within_scope=data.get("within_scope", True),
        )


@dataclass
class RecoveryOutcome:
    """The orchestrator-facing result of a recovery evaluation."""

    decision: RecoveryDecision
    reason: str
    plan: RecoveryPlan | None = None
    attempts_used: int = 0
    blocked_fingerprint: str | None = None

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "plan": self.plan.to_dict() if self.plan else None,
            "attempts_used": self.attempts_used,
            "blocked_fingerprint": self.blocked_fingerprint,
        }
