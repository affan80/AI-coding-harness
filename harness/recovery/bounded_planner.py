"""Bounded recovery planning on the shared model client (issue #64; PRD §§17, 20).

The planner proposes a repair for a classified failure and enforces the
governance rules the model cannot be trusted with:

* **materially different**: a proposal identical (by normalized hash) to a
  prior attempt is rejected — no blind retries;
* **within scope**: repair targets outside the approved scope are rejected;
* **cannot skip re-verification**: the plan's re-verification ladder always
  starts at the failed stage and widens from there — enforced in code;
* **budgeted**: attempts beyond the budget end in ESCALATE, not another try.

Environment failures are not code defects: they route to environment
setup/rollback strategies instead of a source repair.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.core.errors import HarnessError
from harness.model.errors import ModelError
from harness.model.types import Message, ModelClient, ResponseSchema
from harness.recovery.classify import ClassifiedFailure, FailureKind

SCHEMA = ResponseSchema(
    name="recovery_plan",
    schema={
        "type": "object",
        "required": ["strategy", "repair_description", "repair_targets"],
        "properties": {
            "strategy": {
                "type": "string",
                "enum": ["repair", "rollback", "environment_setup", "escalate"],
            },
            "repair_description": {"type": "string"},
            "repair_targets": {
                "type": "array", "items": {"type": "string"},
            },
        },
    },
)


class RecoveryError(HarnessError):
    """Recovery could not produce a usable plan."""

    code = "recovery_error"


class Strategy(StrEnum):
    REPAIR = "repair"
    ROLLBACK = "rollback"
    ENVIRONMENT_SETUP = "environment_setup"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class AttemptRecord:
    """One prior recovery attempt (kept verbatim in every new plan)."""

    attempt: int
    strategy: str
    repair_description: str
    fingerprint: str  # normalized hash of the proposal
    outcome: str  # "failed" | "regressed" | ...

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "strategy": self.strategy,
            "repair_description": self.repair_description,
            "fingerprint": self.fingerprint,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class RecoveryPlan:
    """The bounded recovery output consumed by the orchestrator."""

    strategy: Strategy
    failure: ClassifiedFailure
    repair_description: str
    repair_targets: tuple[str, ...] = ()
    reverify_stages: tuple[str, ...] = ()
    attempts: tuple[AttemptRecord, ...] = ()
    escalated: bool = False
    refusal_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "failure": self.failure.to_dict(),
            "repair_description": self.repair_description,
            "repair_targets": list(self.repair_targets),
            "reverify_stages": list(self.reverify_stages),
            "attempts": [a.to_dict() for a in self.attempts],
            "escalated": self.escalated,
            "refusal_reason": self.refusal_reason,
        }


def _fingerprint(description: str, targets: Sequence[str]) -> str:
    normalized = " ".join(description.lower().split()) + "|" + ",".join(
        sorted(t.lstrip("./") for t in targets)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def reverify_ladder(failed_stage: str, all_stages: Sequence[str]) -> tuple[str, ...]:
    """Failed stage first, then the remaining stages in their original order.

    Code-enforced: the model cannot ask to skip the failed stage or to run
    the full suite before the stage that failed.
    """
    if failed_stage not in all_stages:
        return tuple(all_stages)
    index = all_stages.index(failed_stage)
    return (failed_stage, *all_stages[index + 1 :])


class RecoveryPlanner:
    """Model-backed recovery plans with code-enforced governance."""

    def __init__(
        self,
        client: ModelClient,
        *,
        max_attempts: int = 3,
        register_retry: Callable[[str], bool] | None = None,
        all_stages: Sequence[str] = (
            "syntax", "build", "reproducer", "targeted_tests",
            "related_tests", "full_suite", "diff_scope",
        ),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._client = client
        self._max_attempts = max_attempts
        self._register_retry = register_retry
        self._all_stages = tuple(all_stages)

    async def plan_recovery(
        self,
        failure: ClassifiedFailure,
        *,
        attempt_history: Sequence[AttemptRecord] = (),
        allowed_scope: Sequence[str] = (),
        goal_text: str = "",
    ) -> RecoveryPlan:
        """Propose one bounded recovery step for the classified failure."""
        if len(attempt_history) >= self._max_attempts:
            return RecoveryPlan(
                strategy=Strategy.ESCALATE,
                failure=failure,
                repair_description=(
                    f"retry budget exhausted after {len(attempt_history)} "
                    "attempts; escalating to the operator"
                ),
                reverify_stages=reverify_ladder(failure.stage, self._all_stages),
                attempts=tuple(attempt_history),
                escalated=True,
                refusal_reason="retry budget exhausted",
            )

        # Environment failures are not code defects: repair is setup/rollback.
        if failure.kind is FailureKind.ENVIRONMENT:
            return RecoveryPlan(
                strategy=Strategy.ENVIRONMENT_SETUP,
                failure=failure,
                repair_description=(
                    f"set up the missing environment piece before any code "
                    f"repair: {failure.summary}"
                ),
                reverify_stages=reverify_ladder(failure.stage, self._all_stages),
                attempts=tuple(attempt_history),
            )

        if not self._spend_retry():
            return RecoveryPlan(
                strategy=Strategy.ESCALATE,
                failure=failure,
                repair_description="retry budget exhausted before proposal",
                reverify_stages=reverify_ladder(failure.stage, self._all_stages),
                attempts=tuple(attempt_history),
                escalated=True,
                refusal_reason="retry budget exhausted",
            )

        proposal = await self._propose(
            failure, attempt_history, allowed_scope, goal_text
        )
        if isinstance(proposal, RecoveryPlan):
            return proposal  # refused in-flight (material/scope guards)

        strategy, description, targets = proposal
        return RecoveryPlan(
            strategy=Strategy(strategy),
            failure=failure,
            repair_description=description,
            repair_targets=tuple(targets),
            reverify_stages=reverify_ladder(failure.stage, self._all_stages),
            attempts=tuple(attempt_history),
        )

    # -- internals -------------------------------------------------------------

    def _spend_retry(self) -> bool:
        if self._register_retry is None:
            return True
        return self._register_retry("recovery")

    async def _propose(
        self,
        failure: ClassifiedFailure,
        attempt_history: Sequence[AttemptRecord],
        allowed_scope: Sequence[str],
        goal_text: str,
    ) -> tuple[str, str, list[str]] | RecoveryPlan:
        messages = [
            Message(role="system", content=(
                "You propose ONE recovery step for a classified failure. The "
                "repair must be materially different from every prior attempt "
                "and touch only paths inside the approved scope. Return JSON "
                "with strategy (repair|rollback|environment_setup|escalate), "
                "repair_description, repair_targets."
            )),
            Message(role="user", content=json.dumps({
                "failure": failure.to_dict(),
                "prior_attempts": [a.to_dict() for a in attempt_history],
                "approved_scope": list(allowed_scope) or "entire repository",
                "objective": goal_text,
            })),
        ]
        try:
            response = await self._client.generate(
                messages, response_schema=SCHEMA
            )
        except ModelError as exc:
            # provider failures normalize into the recovery layer
            raise RecoveryError(
                f"recovery proposal failed: {exc.message}",
                details=exc.to_dict(),
            ) from None
        payload = response.structured
        if not isinstance(payload, Mapping):
            raise RecoveryError(
                "model returned no structured recovery proposal",
                details={"attempt_count": len(attempt_history)},
            )

        description = str(payload.get("repair_description", ""))
        targets = [str(t) for t in payload.get("repair_targets", [])]
        strategy = str(payload.get("strategy", "repair"))

        # Materially-different guard (no blind retries).
        fingerprint = _fingerprint(description, targets)
        if any(a.fingerprint == fingerprint for a in attempt_history):
            return RecoveryPlan(
                strategy=Strategy.ESCALATE,
                failure=failure,
                repair_description=description,
                repair_targets=tuple(targets),
                reverify_stages=reverify_ladder(failure.stage, self._all_stages),
                attempts=tuple(attempt_history),
                escalated=True,
                refusal_reason=(
                    "proposal is materially identical to a prior attempt; "
                    "forcing escalation instead of a blind retry"
                ),
            )

        # Scope guard: repair targets outside approved scope are rejected.
        if allowed_scope:
            out_of_scope = [
                t for t in targets
                if not any(
                    t.lstrip("./") == scope.strip("./")
                    or t.lstrip("./").startswith(scope.strip("./").rstrip("/") + "/")
                    for scope in allowed_scope
                )
            ]
            if out_of_scope:
                return RecoveryPlan(
                    strategy=Strategy.ROLLBACK,
                    failure=failure,
                    repair_description=description,
                    repair_targets=tuple(targets),
                    reverify_stages=reverify_ladder(failure.stage, self._all_stages),
                    attempts=tuple(attempt_history),
                    escalated=True,
                    refusal_reason=(
                        "repair targets outside approved scope: "
                        + ", ".join(out_of_scope)
                    ),
                )
        return strategy, description, targets
