"""Core session contracts.

This module is the minimal shared-models slice that the product-surface
issues (#2 evidence store, #3 CLI) consume: user requests with constraints,
session status, and session identifiers. Issue #1 (session models, budgets,
and the orchestrator state machine) extends these types and owns the state
machine; nothing here mutates session state.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class WritePolicy(StrEnum):
    """Where the harness may write in the target repository."""

    SCOPED = "scoped"  # writes restricted to allowed_scope paths
    ALL = "all"


class VerificationDepth(StrEnum):
    """How much of the verification ladder to run."""

    SYNTAX = "syntax"
    TARGETED = "targeted"
    FULL = "full"


class CheckpointPolicy(StrEnum):
    WHEN = "when"  # checkpoint before risky edits
    ALWAYS = "always"
    NEVER = "never"


class SessionStatus(StrEnum):
    """Terminal and in-flight session outcomes reported by the CLI."""

    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


def new_session_id(now: datetime | None = None) -> str:
    """Sortable session id: s-<UTC timestamp>-<random suffix>."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"s-{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Budget:
    """Session resource constraints (PRD §20)."""

    max_model_calls: int = 20
    max_iterations: int = 30
    max_retries_per_goal: int = 3
    max_audit_rounds: int = 2
    command_timeout_seconds: int = 120


@dataclass(frozen=True)
class UserRequest:
    """Everything the user asked the harness to do, in one typed value.

    Both interactive and scripted invocations produce this exact shape.
    """

    repository: str
    objective: str
    allowed_scope: tuple[str, ...] = ()
    write_policy: WritePolicy = WritePolicy.SCOPED
    verification_depth: VerificationDepth = VerificationDepth.TARGETED
    audit_enabled: bool = True
    checkpoint_policy: CheckpointPolicy = CheckpointPolicy.WHEN
    budget: Budget = field(default_factory=Budget)

    def validate(self) -> None:
        """Raise ValueError with an actionable message on invalid input."""
        if not self.repository or not self.repository.strip():
            raise ValueError("repository path must not be empty")
        if not self.objective or not self.objective.strip():
            raise ValueError("objective must not be empty; describe what to do")
        for scope in self.allowed_scope:
            if scope.startswith("/") or ".." in scope.split("/"):
                raise ValueError(
                    f"invalid allowed scope {scope!r}: use relative paths inside the repository"
                )
        b = self.budget
        for name in ("max_model_calls", "max_iterations", "max_retries_per_goal"):
            if getattr(b, name) < 1:
                raise ValueError(f"budget.{name} must be at least 1 (got {getattr(b, name)})")
        if b.max_audit_rounds < 0:
            raise ValueError(
                f"budget.max_audit_rounds must be at least 0 (got {b.max_audit_rounds})"
            )
        if b.command_timeout_seconds < 1:
            raise ValueError(
                f"budget.command_timeout_seconds must be at least 1 "
                f"(got {b.command_timeout_seconds})"
            )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["write_policy"] = self.write_policy.value
        data["verification_depth"] = self.verification_depth.value
        data["checkpoint_policy"] = self.checkpoint_policy.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> UserRequest:
        budget = Budget(**data["budget"])
        return cls(
            repository=data["repository"],
            objective=data["objective"],
            allowed_scope=tuple(data["allowed_scope"]),
            write_policy=WritePolicy(data["write_policy"]),
            verification_depth=VerificationDepth(data["verification_depth"]),
            audit_enabled=data["audit_enabled"],
            checkpoint_policy=CheckpointPolicy(data["checkpoint_policy"]),
            budget=budget,
        )
