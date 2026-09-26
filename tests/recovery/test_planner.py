"""Issue #64 — bounded recovery plans through the shared model client."""

from __future__ import annotations

import asyncio
import json

import pytest

from harness.core.budgets import BudgetKind, consume
from harness.core.models import Budget, BudgetUsage
from harness.recovery import (
    AttemptRecord,
    FailureClass,
    FailureEvidence,
    LoopDetector,
    RecoveryDecision,
    RecoveryPlanError,
    RecoveryRequest,
    evaluate_recovery,
    propose_recovery_plan,
    reverification_sequence,
)
from harness.verification.models import StageName

EXPECTED_REVERIFY = ["targeted_tests", "related_tests", "full_suite", "diff_scope"]


class FakeRecoveryClient:
    def __init__(self, response: str | object) -> None:
        self._response = response
        self.prompts: list[str] = []

    async def generate(self, messages: list[dict], *, response_schema=None) -> str:
        self.prompts.append(messages[0]["content"])
        if callable(self._response):
            return self._response(self.prompts[-1])
        return str(self._response)


def _evidence(**overrides) -> FailureEvidence:
    defaults = dict(
        failure_class=FailureClass.TEST,
        concise_error="AssertionError: add(1, 2) != 4",
        goal_id="G1",
        stage="targeted_tests",
        failing_command="python -m pytest -q tests/test_math.py::test_add",
        exit_code=1,
        affected_paths=("app/math.py",),
        relevant_tests=("tests/test_math.py::test_add",),
        prior_attempts=(
            AttemptRecord(
                attempt=1, action="reordered imports", fingerprint="fp-a", outcome="failed"
            ),
        ),
    )
    defaults.update(overrides)
    return FailureEvidence(**defaults)


def _plan_json(**overrides) -> str:
    plan = {
        "failure_class": "test",
        "root_cause": "add() subtracts instead of adding",
        "repair_summary": "return a + b in app/math.py",
        "repair_actions": [{"kind": "patch", "target": "app/math.py", "detail": "fix operator"}],
        "reverify_stages": EXPECTED_REVERIFY,
    }
    plan.update(overrides)
    return json.dumps(plan)


def test_prompt_contains_only_the_focused_evidence() -> None:
    client = FakeRecoveryClient(_plan_json())

    asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("app/",)))

    prompt = client.prompts[0]
    assert "AssertionError: add(1, 2) != 4" in prompt
    assert '"goal_id": "G1"' in prompt
    assert "prior_attempts" in prompt
    assert "reordered imports" in prompt  # prior attempts are retained
    assert '"app/"' in prompt  # scope is included
    # No run history: nothing beyond the evidence bundle is serialized.
    assert "events.jsonl" not in prompt
    assert "run_history" not in prompt


def test_valid_plan_parses_with_failed_stage_first() -> None:
    client = FakeRecoveryClient(_plan_json())

    plan = asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("app/",)))

    assert plan.failure_class is FailureClass.TEST
    assert plan.repair_actions[0].target == "app/math.py"
    assert list(plan.reverify_stages) == EXPECTED_REVERIFY
    assert list(plan.reverify_stages) == [
        stage.value for stage in reverification_sequence(StageName.TARGETED_TESTS)
    ]


def test_reverify_must_rerun_failed_stage_first() -> None:
    client = FakeRecoveryClient(_plan_json(reverify_stages=["full_suite", "diff_scope"]))

    with pytest.raises(RecoveryPlanError, match="failed stage first"):
        asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("app/",)))


def test_invalid_json_is_rejected_as_plan_failure() -> None:
    client = FakeRecoveryClient("not json at all")

    with pytest.raises(RecoveryPlanError, match="not valid JSON"):
        asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("app/",)))


def test_out_of_scope_repair_targets_are_rejected() -> None:
    client = FakeRecoveryClient(
        _plan_json(
            repair_actions=[{"kind": "patch", "target": "frontend/app.tsx", "detail": "nope"}]
        )
    )

    with pytest.raises(RecoveryPlanError, match="outside the allowed scope"):
        asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("backend/",)))


def test_unknown_reverify_stage_is_rejected() -> None:
    client = FakeRecoveryClient(_plan_json(reverify_stages=["targeted_tests", "vibes"]))

    with pytest.raises(RecoveryPlanError, match="unknown reverify stages"):
        asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("app/",)))


def test_empty_root_cause_is_rejected() -> None:
    client = FakeRecoveryClient(_plan_json(root_cause="  "))

    with pytest.raises(RecoveryPlanError, match="empty root cause"):
        asyncio.run(propose_recovery_plan(_evidence(), client, allowed_scope=("app/",)))


def test_recovery_flow_consumes_model_call_and_retry_budgets() -> None:
    budget = Budget(max_model_calls=2, max_retries_per_goal=2)
    usage = BudgetUsage()
    client = FakeRecoveryClient(_plan_json())

    evidence = _evidence()
    # The orchestrator consumes a model call around each generate().
    usage = consume(budget, usage, BudgetKind.MODEL_CALLS)
    plan = asyncio.run(propose_recovery_plan(evidence, client, allowed_scope=("app/",)))
    outcome, usage = evaluate_recovery(
        RecoveryRequest(
            goal_id="G1",
            failure_class=evidence.failure_class,
            fingerprint="fp-1",
            plan=plan,
        ),
        budget,
        usage,
        LoopDetector(),
    )

    assert outcome.decision is RecoveryDecision.RETRY
    assert usage.model_calls == 1
    assert usage.retries_for("G1") == 1
    assert outcome.plan is not None
    # Re-verification cannot be skipped: the plan reruns the failed stage.
    assert outcome.plan.reverify_stages[0] == "targeted_tests"
