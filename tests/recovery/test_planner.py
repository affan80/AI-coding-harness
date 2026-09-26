"""Issue #64: bounded recovery plans, material-difference, re-verification gate."""

import asyncio

import pytest

from harness.model.fake import FakeModelClient, ScriptedTurn
from harness.recovery.classify import FailureKind, classify_failure
from harness.recovery.planner import (
    AttemptRecord,
    RecoveryError,
    RecoveryPlanner,
    Strategy,
    reverify_ladder,
)


def _failure(kind: FailureKind = FailureKind.TEST, **kwargs) -> object:
    return classify_failure(
        command="pytest tests/test_auth.py -q",
        exit_code=1,
        output="FAILED tests/test_auth.py::test_bad_password - AssertionError: 500 != 401",
        stage="targeted_tests",
        **kwargs,
    )


def _proposal(description="handle InvalidCredentialsError in the login route",
              targets=("src/routes/login.py",), strategy="repair"):
    return ScriptedTurn(structured={
        "strategy": strategy,
        "repair_description": description,
        "repair_targets": list(targets),
    })


ALL_STAGES = (
    "syntax", "build", "reproducer", "targeted_tests",
    "related_tests", "full_suite", "diff_scope",
)


def test_recovery_plan_starts_at_failed_stage_and_widens():
    client = FakeModelClient(script=[_proposal()])
    plan = asyncio.run(RecoveryPlanner(client).plan_recovery(
        _failure(), allowed_scope=("src/",), goal_text="fix the login bug",
    ))

    assert plan.strategy is Strategy.REPAIR
    # code-enforced: the failed stage is first, wider stages follow
    assert plan.reverify_stages == (
        "targeted_tests", "related_tests", "full_suite", "diff_scope",
    )
    assert plan.reverify_stages == reverify_ladder(
        "targeted_tests", ALL_STAGES
    )
    assert not plan.escalated


def test_reverify_ladder_cannot_skip_the_failed_stage():
    assert reverify_ladder("full_suite", ALL_STAGES) == (
        "full_suite", "diff_scope",
    )
    assert reverify_ladder("syntax", ALL_STAGES) == ALL_STAGES
    # an unknown stage falls back to the full ladder
    assert reverify_ladder("nonsense", ALL_STAGES) == ALL_STAGES


def test_identical_proposal_is_rejected_as_not_materially_different():
    prior = AttemptRecord(
        attempt=1, strategy="repair",
        repair_description="Handle InvalidCredentialsError in the login route",
        fingerprint=None, outcome="failed",
    )
    from harness.recovery.planner import _fingerprint

    prior = AttemptRecord(
        attempt=1, strategy="repair",
        repair_description="handle invalidcredentialserror in the login route",
        fingerprint=_fingerprint(
            "Handle InvalidCredentialsError in the login route",
            ("src/routes/login.py",),
        ),
        outcome="failed",
    )
    client = FakeModelClient(script=[_proposal()])  # same repair, reworded case
    plan = asyncio.run(RecoveryPlanner(client).plan_recovery(
        _failure(), attempt_history=[prior], allowed_scope=("src/",),
    ))

    assert plan.escalated
    assert plan.strategy is Strategy.ESCALATE
    assert "materially identical" in plan.refusal_reason


def test_out_of_scope_repair_is_rejected_and_rolls_back():
    client = FakeModelClient(script=[
        _proposal(targets=("frontend/app.tsx",))
    ])
    plan = asyncio.run(RecoveryPlanner(client).plan_recovery(
        _failure(), allowed_scope=("src/", "tests/"),
    ))

    assert plan.strategy is Strategy.ROLLBACK
    assert plan.escalated
    assert "outside approved scope" in plan.refusal_reason
    assert "frontend/app.tsx" in plan.refusal_reason
    # re-verification is still enforced on the rollback plan
    assert plan.reverify_stages[0] == "targeted_tests"


def test_retry_budget_exhaustion_escalates_without_another_model_call():
    history = [
        AttemptRecord(attempt=1, strategy="repair", repair_description="a",
                      fingerprint="f1", outcome="failed"),
        AttemptRecord(attempt=2, strategy="repair", repair_description="b",
                      fingerprint="f2", outcome="failed"),
    ]
    client = FakeModelClient(script=[])  # would raise if called
    plan = asyncio.run(RecoveryPlanner(
        client, max_attempts=2,
    ).plan_recovery(_failure(), attempt_history=history))

    assert plan.strategy is Strategy.ESCALATE
    assert plan.escalated
    assert "retry budget exhausted" in plan.refusal_reason
    assert len(plan.attempts) == 2  # prior attempts retained verbatim
    assert client.calls == ()


def test_retry_budget_hook_is_honored():
    client = FakeModelClient(script=[_proposal()])
    plan = asyncio.run(RecoveryPlanner(
        client, register_retry=lambda _k: False,
    ).plan_recovery(_failure()))
    assert plan.strategy is Strategy.ESCALATE
    assert client.calls == ()


def test_environment_failures_route_to_setup_not_code_repair():
    failure = classify_failure(
        command="pytest -q", exit_code=1,
        output="ModuleNotFoundError: No module named 'httpx'",
    )
    client = FakeModelClient(script=[_proposal()])
    plan = asyncio.run(RecoveryPlanner(client).plan_recovery(failure))

    assert plan.strategy is Strategy.ENVIRONMENT_SETUP
    assert plan.failure.kind is FailureKind.ENVIRONMENT
    assert plan.failure.is_environment
    # the model was never asked to repair an environment problem as code
    assert client.calls == ()


def test_max_attempts_must_be_positive():
    with pytest.raises(ValueError):
        RecoveryPlanner(FakeModelClient(), max_attempts=0)


def test_recovery_plan_serializes_for_the_run_directory():
    client = FakeModelClient(script=[_proposal()])
    plan = asyncio.run(RecoveryPlanner(client).plan_recovery(
        _failure(), allowed_scope=("src/",), goal_text="fix the login bug",
    ))
    import json

    payload = plan.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["strategy"] == "repair"


def test_unstructured_model_output_is_a_structured_error():
    client = FakeModelClient(script=[ScriptedTurn(text="I think you should...")
                                     ])
    with pytest.raises(RecoveryError):
        asyncio.run(RecoveryPlanner(client).plan_recovery(_failure()))
