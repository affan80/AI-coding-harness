"""Budget kinds and pure budget arithmetic (PRD §20, FR-14).

Budget checking is functional and side-effect free: :func:`consume` takes a
:class:`Budget` and the current :class:`BudgetUsage` and returns the next
usage, or raises a structured :class:`BudgetExhaustedError`. Keeping the
arithmetic pure makes it trivially testable and lets the orchestrator stay
the only place where usage actually changes.
"""

from __future__ import annotations

from enum import StrEnum

from harness.core.errors import BudgetExhaustedError
from harness.core.models import Budget, BudgetUsage


class BudgetKind(StrEnum):
    """The countable budgets the orchestrator enforces.

    ``command_timeout_seconds`` is not a counter; it is a per-command limit
    applied by the tool/execution layer, so it has no kind here.
    """

    MODEL_CALLS = "MODEL_CALLS"
    ITERATIONS = "ITERATIONS"
    RETRIES_PER_GOAL = "RETRIES_PER_GOAL"
    AUDIT_ROUNDS = "AUDIT_ROUNDS"


def limit_for(budget: Budget, kind: BudgetKind) -> int:
    """Return the configured limit for ``kind``."""
    field_by_kind = {
        BudgetKind.MODEL_CALLS: budget.max_model_calls,
        BudgetKind.ITERATIONS: budget.max_iterations,
        BudgetKind.RETRIES_PER_GOAL: budget.max_retries_per_goal,
        BudgetKind.AUDIT_ROUNDS: budget.max_audit_rounds,
    }
    return field_by_kind[kind]


def used_for(usage: BudgetUsage, kind: BudgetKind, goal_id: str | None = None) -> int:
    """Return the amount of ``kind`` already consumed."""
    if kind is BudgetKind.MODEL_CALLS:
        return usage.model_calls
    if kind is BudgetKind.ITERATIONS:
        return usage.iterations
    if kind is BudgetKind.AUDIT_ROUNDS:
        return usage.audit_rounds
    if kind is BudgetKind.RETRIES_PER_GOAL:
        if goal_id is None:
            raise ValueError("RETRIES_PER_GOAL requires a goal_id")
        return usage.retries_for(goal_id)
    raise ValueError(f"unknown budget kind: {kind!r}")


def has_headroom(
    budget: Budget, usage: BudgetUsage, kind: BudgetKind, goal_id: str | None = None
) -> bool:
    """Return True if one more unit of ``kind`` is allowed."""
    return used_for(usage, kind, goal_id) < limit_for(budget, kind)


def consume(
    budget: Budget, usage: BudgetUsage, kind: BudgetKind, goal_id: str | None = None
) -> BudgetUsage:
    """Return a new :class:`BudgetUsage` with one more unit of ``kind`` used.

    Raises :class:`BudgetExhaustedError` when the limit for ``kind`` is
    already reached, without changing anything.
    """
    used = used_for(usage, kind, goal_id)
    limit = limit_for(budget, kind)
    if used >= limit:
        raise BudgetExhaustedError.for_budget(kind.value, limit, used, goal_id=goal_id)

    if kind is BudgetKind.MODEL_CALLS:
        return BudgetUsage(
            model_calls=used + 1,
            iterations=usage.iterations,
            audit_rounds=usage.audit_rounds,
            retries_by_goal=usage.retries_by_goal,
        )
    if kind is BudgetKind.ITERATIONS:
        return BudgetUsage(
            model_calls=usage.model_calls,
            iterations=used + 1,
            audit_rounds=usage.audit_rounds,
            retries_by_goal=usage.retries_by_goal,
        )
    if kind is BudgetKind.AUDIT_ROUNDS:
        return BudgetUsage(
            model_calls=usage.model_calls,
            iterations=usage.iterations,
            audit_rounds=used + 1,
            retries_by_goal=usage.retries_by_goal,
        )
    # RETRIES_PER_GOAL
    updated_retries = dict(usage.retries_by_goal)
    updated_retries[goal_id] = used + 1  # goal_id validated by used_for above
    return BudgetUsage(
        model_calls=usage.model_calls,
        iterations=usage.iterations,
        audit_rounds=usage.audit_rounds,
        retries_by_goal=updated_retries,
    )
