"""Unit checks for the serializable session and domain models (issue #21)."""

from __future__ import annotations

import dataclasses

import pytest

from harness.core.errors import SerializationError
from harness.core.models import (
    SCHEMA_VERSION,
    Budget,
    BudgetUsage,
    EvidenceRef,
    Goal,
    OrchestrationState,
    PlanStep,
    Session,
    StepKind,
    TerminalInfo,
    TerminalOutcome,
    TerminalReason,
    TransitionRecord,
    UserRequest,
)


def make_request(**overrides) -> UserRequest:
    defaults: dict = {
        "objective": "Fix the 500 on POST /login and add a regression test",
        "repository_path": "/work/target-repo",
        "request_id": "req-1",
        "constraints": ("no new dependencies",),
        "scope_paths": ("src/auth/",),
    }
    defaults.update(overrides)
    return UserRequest(**defaults)


def make_session() -> Session:
    return Session.create(
        make_request(),
        budget=Budget(max_model_calls=5),
        session_id="sess-1",
        at="2026-01-01T00:00:00+00:00",
    )


class TestModelRoundTrips:
    def test_user_request_round_trip(self):
        request = make_request()
        assert UserRequest.from_dict(request.to_dict()) == request

    def test_evidence_ref_round_trip_keeps_metadata(self):
        ref = EvidenceRef(
            kind="verification_report",
            description="pytest -q exit 1",
            ref_id="ev-1",
            path="runs/sess-1/verification.json",
            sha256="abc123",
            metadata={"failed": 1, "passed": 12},
        )
        assert EvidenceRef.from_dict(ref.to_dict()) == ref

    def test_goal_and_step_round_trips(self):
        goal = Goal(
            goal_id="g1",
            title="Fix login 500",
            description="InvalidCredentials must map to 401",
            acceptance_criteria=("POST /login returns 401 for bad password",),
        )
        step = PlanStep(
            step_id="s1",
            goal_id="g1",
            title="Handle InvalidCredentials in auth route",
            kind=StepKind.EDIT,
            depends_on=("s0",),
        )
        assert Goal.from_dict(goal.to_dict()) == goal
        assert PlanStep.from_dict(step.to_dict()) == step

    def test_budget_and_usage_round_trips(self):
        budget = Budget(
            max_model_calls=7,
            max_iterations=9,
            max_retries_per_goal=2,
            max_audit_rounds=1,
            command_timeout_seconds=45.5,
        )
        usage = BudgetUsage(model_calls=1, iterations=2, audit_rounds=0, retries_by_goal={"g1": 1})
        assert Budget.from_dict(budget.to_dict()) == budget
        assert BudgetUsage.from_dict(usage.to_dict()) == usage

    def test_full_session_round_trip(self):
        session = make_session()
        updated = Session(
            **{
                **session.__dict__,
                "state": OrchestrationState.PLAN,
                "updated_at": "2026-01-01T00:00:05+00:00",
                "goals": (Goal(goal_id="g1", title="Fix login 500"),),
                "steps": (
                    PlanStep(
                        step_id="s1", goal_id="g1", title="Patch auth route", kind=StepKind.EDIT
                    ),
                ),
                "history": (
                    TransitionRecord(
                        seq=1,
                        from_state=OrchestrationState.INITIALIZE,
                        to_state=OrchestrationState.UNDERSTAND,
                        at="2026-01-01T00:00:01+00:00",
                        reason="start",
                        evidence=(EvidenceRef(kind="log", description="boot"),),
                    ),
                ),
                "terminal": TerminalInfo(
                    outcome=TerminalOutcome.FAILED,
                    reason=TerminalReason.BUDGET_EXHAUSTED,
                    detail="model calls exhausted",
                ),
            }
        )
        assert Session.from_dict(updated.to_dict()) == updated


class TestSessionImmutability:
    def test_session_is_frozen(self):
        session = make_session()
        with pytest.raises(dataclasses.FrozenInstanceError):
            session.state = OrchestrationState.PLAN  # type: ignore[misc]

    def test_goal_is_frozen(self):
        goal = Goal(goal_id="g1", title="t")
        with pytest.raises(dataclasses.FrozenInstanceError):
            goal.status = "COMPLETED"  # type: ignore[misc]


class TestRestoreValidation:
    def test_rejects_unknown_schema_version(self):
        data = make_session().to_dict()
        data["schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(SerializationError) as excinfo:
            Session.from_dict(data)
        assert excinfo.value.details["field"] == "schema_version"

    def test_rejects_unknown_state_value(self):
        data = make_session().to_dict()
        data["state"] = "WARP_SPEED"
        with pytest.raises(SerializationError) as excinfo:
            Session.from_dict(data)
        assert excinfo.value.details["field"] == "state"

    def test_rejects_missing_required_field(self):
        data = make_session().to_dict()
        del data["session_id"]
        with pytest.raises(SerializationError) as excinfo:
            Session.from_dict(data)
        assert excinfo.value.details["field"] == "session_id"


class TestBudgetValidation:
    def test_negative_counters_rejected(self):
        with pytest.raises(ValueError, match="max_model_calls"):
            Budget(max_model_calls=-1)

    def test_nonpositive_command_timeout_rejected(self):
        with pytest.raises(ValueError, match="command_timeout_seconds"):
            Budget(command_timeout_seconds=0)

    def test_prd_default_budget_is_valid(self):
        budget = Budget()  # PRD §20 example values
        assert budget.max_model_calls == 20
        assert budget.max_iterations == 30
        assert budget.max_retries_per_goal == 3
        assert budget.max_audit_rounds == 2
        assert budget.command_timeout_seconds == 120.0
