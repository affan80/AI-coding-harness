"""Unit checks for budget enforcement (issue #23, FR-14)."""

from __future__ import annotations

import pytest

from harness.core.budgets import BudgetKind, consume, has_headroom, used_for
from harness.core.errors import BudgetExhaustedError
from harness.core.models import Budget, BudgetUsage

KIND = BudgetKind


class TestModelCallBudget:
    def test_consume_increments(self):
        budget = Budget(max_model_calls=2)
        usage = consume(budget, BudgetUsage(), KIND.MODEL_CALLS)
        usage = consume(budget, usage, KIND.MODEL_CALLS)
        assert usage.model_calls == 2
        assert has_headroom(budget, usage, KIND.MODEL_CALLS) is False

    def test_exhaustion_raises_structured_error_without_mutation(self):
        budget = Budget(max_model_calls=1)
        usage = consume(budget, BudgetUsage(), KIND.MODEL_CALLS)
        with pytest.raises(BudgetExhaustedError) as excinfo:
            consume(budget, usage, KIND.MODEL_CALLS)
        payload = excinfo.value.to_dict()
        assert payload["budget_kind"] == "MODEL_CALLS"
        assert payload["limit"] == 1
        assert payload["used"] == 1
        # original usage unchanged
        assert usage.model_calls == 1

    def test_zero_limit_exhausts_immediately(self):
        with pytest.raises(BudgetExhaustedError):
            consume(Budget(max_model_calls=0), BudgetUsage(), KIND.MODEL_CALLS)


class TestRetryBudgetPerGoal:
    def test_counters_are_independent_per_goal(self):
        budget = Budget(max_retries_per_goal=2)
        usage = BudgetUsage()
        usage = consume(budget, usage, KIND.RETRIES_PER_GOAL, goal_id="g1")
        usage = consume(budget, usage, KIND.RETRIES_PER_GOAL, goal_id="g2")
        assert usage.retries_by_goal == {"g1": 1, "g2": 1}

    def test_retry_exhaustion_names_the_goal(self):
        budget = Budget(max_retries_per_goal=1)
        usage = consume(budget, BudgetUsage(), KIND.RETRIES_PER_GOAL, goal_id="g1")
        with pytest.raises(BudgetExhaustedError) as excinfo:
            consume(budget, usage, KIND.RETRIES_PER_GOAL, goal_id="g1")
        assert excinfo.value.to_dict()["goal_id"] == "g1"

    def test_retry_requires_goal_id(self):
        with pytest.raises(ValueError, match="goal_id"):
            used_for(BudgetUsage(), KIND.RETRIES_PER_GOAL, goal_id=None)


class TestIterationAndAuditBudgets:
    def test_iteration_exhaustion(self):
        budget = Budget(max_iterations=2)
        usage = consume(budget, BudgetUsage(), KIND.ITERATIONS)
        usage = consume(budget, usage, KIND.ITERATIONS)
        with pytest.raises(BudgetExhaustedError):
            consume(budget, usage, KIND.ITERATIONS)

    def test_audit_round_exhaustion(self):
        budget = Budget(max_audit_rounds=1)
        usage = consume(budget, BudgetUsage(), KIND.AUDIT_ROUNDS)
        assert has_headroom(budget, usage, KIND.AUDIT_ROUNDS) is False
        with pytest.raises(BudgetExhaustedError):
            consume(budget, usage, KIND.AUDIT_ROUNDS)

    def test_command_timeout_is_carried_for_the_tool_layer(self):
        # The command timeout is a per-command limit enforced by the
        # execution layer (Person C); the orchestrator only carries it.
        assert Budget(command_timeout_seconds=30).command_timeout_seconds == 30
