"""Recovery engine: budgets, loop blocking, and terminal decisions (PRD §20).

Pure decision logic between diagnosis (#63), planning (#64), and the
orchestrator. It consumes the upstream core budget arithmetic
(``RETRIES_PER_GOAL`` per goal) and the loop detector, and returns the
decision the orchestrator executes — including rollback when the working
state is no longer useful (executed via #12 checkpoints once available).
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.core.budgets import BudgetKind, consume, has_headroom
from harness.core.errors import BudgetExhaustedError
from harness.core.models import Budget, BudgetUsage
from harness.recovery.loops import LoopDetector
from harness.recovery.models import (
    AttemptRecord,
    FailureClass,
    RecoveryDecision,
    RecoveryOutcome,
    RecoveryPlan,
)


@dataclass(frozen=True)
class RecoveryRequest:
    """Everything the recovery engine decides on; no run history."""

    goal_id: str
    failure_class: FailureClass
    fingerprint: str  # action fingerprint of the attempt that just failed
    plan: RecoveryPlan | None = None  # proposed repair, if one exists
    prior_attempts: tuple[AttemptRecord, ...] = ()


def evaluate_recovery(
    request: RecoveryRequest,
    budget: Budget,
    usage: BudgetUsage,
    loop_detector: LoopDetector,
) -> tuple[RecoveryOutcome, BudgetUsage]:
    """Decide retry/replan/rollback/stop and return updated budget usage.

    Ordering of gates:
    1. Loop detector: an attempt identical to a previously blocked one is
       rejected and forces REPLAN without consuming retry budget.
    2. Retry budget: exhausting ``RETRIES_PER_GOAL`` for the goal stops the
       session; unrepairable classes (environment) stop without retries.
    3. Otherwise RETRY with the proposed plan; the caller re-verifies the
       failed stage first (``plan.reverify_stages``).
    """
    attempts_used = len(request.prior_attempts)

    if loop_detector.record(request.fingerprint):
        return (
            RecoveryOutcome(
                decision=RecoveryDecision.REPLAN,
                reason=(
                    "forced replan: identical action/result repeated at the "
                    f"configured threshold ({loop_detector.threshold})"
                ),
                attempts_used=attempts_used,
                blocked_fingerprint=request.fingerprint,
            ),
            usage,
        )

    if request.failure_class is FailureClass.ENVIRONMENT:
        return (
            RecoveryOutcome(
                decision=RecoveryDecision.STOP,
                reason=(
                    "environment failure (not a source defect); repair the "
                    "runtime and restart the session"
                ),
                attempts_used=attempts_used,
            ),
            usage,
        )

    if not has_headroom(budget, usage, BudgetKind.RETRIES_PER_GOAL, request.goal_id):
        return (
            RecoveryOutcome(
                decision=RecoveryDecision.STOP,
                reason=(
                    f"retry budget exhausted for goal {request.goal_id} "
                    f"after {attempts_used} attempt(s)"
                ),
                attempts_used=attempts_used,
            ),
            usage,
        )

    if request.plan is None:
        return (
            RecoveryOutcome(
                decision=RecoveryDecision.REPLAN,
                reason="no usable recovery plan was produced",
                attempts_used=attempts_used,
            ),
            usage,
        )

    try:
        updated_usage = consume(
            budget, usage, BudgetKind.RETRIES_PER_GOAL, request.goal_id
        )
    except BudgetExhaustedError as exc:
        return (
            RecoveryOutcome(
                decision=RecoveryDecision.STOP,
                reason=str(exc),
                attempts_used=attempts_used,
            ),
            usage,
        )

    return (
        RecoveryOutcome(
            decision=RecoveryDecision.RETRY,
            reason=request.plan.repair_summary,
            plan=request.plan,
            attempts_used=attempts_used + 1,
        ),
        updated_usage,
    )


def should_rollback(plan: RecoveryPlan | None, consecutive_failures: int) -> bool:
    """Roll back when the working state stopped being useful.

    Triggered when repairs keep failing the same stage — the orchestrator
    then restores the last good checkpoint (#12) instead of piling more
    changes onto a broken tree.
    """
    return consecutive_failures >= 3


def persist_recovery_outcome(
    store,
    outcome: RecoveryOutcome,
    evidence,
) -> None:
    """Persist the recovery decision and its evidence (PRD §21; FR-10).

    Terminal recovery reasons are part of the run directory, so a reviewer
    can see not just that the session stopped but exactly why.
    """
    store.write_document(
        "recovery.json",
        {
            "outcome": outcome.to_dict(),
            "evidence": evidence.to_dict(),
        },
    )
    store.append_event(
        "recovery",
        f"{outcome.decision.value}: {outcome.reason}",
        data={"decision": outcome.decision.value, "failure_class": evidence.failure_class.value},
    )
