"""Unit checks for orchestrator transitions, budgets, and restore (issues #22/#23).

Covers the parent issue's acceptance criteria:
* success path, verification failure with recovery, retry exhaustion,
  invalid transitions;
* budget exhaustion produces a non-success terminal result with evidence;
* session state serializes, restores, and continues.
"""

from __future__ import annotations

import dataclasses

import pytest

from harness.core.budgets import BudgetKind
from harness.core.errors import (
    BudgetExhaustedError,
    InvalidTransitionError,
    NotFoundError,
    SerializationError,
)
from harness.core.models import (
    Budget,
    EvidenceRef,
    Goal,
    GoalStatus,
    OrchestrationState,
    PlanStep,
    StepKind,
    StepStatus,
    TerminalOutcome,
    TerminalReason,
    UserRequest,
)
from harness.core.orchestrator import Orchestrator

S = OrchestrationState


def make_request(**overrides) -> UserRequest:
    defaults: dict = {
        "objective": "Fix the 500 on POST /login and add a regression test",
        "repository_path": "/work/target-repo",
        "request_id": "req-1",
    }
    defaults.update(overrides)
    return UserRequest(**defaults)


def make_goal(goal_id: str = "g1") -> Goal:
    return Goal(
        goal_id=goal_id, title="Fix login 500", acceptance_criteria=("401 on bad password",)
    )


def make_step(step_id: str = "s1", goal_id: str = "g1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        goal_id=goal_id,
        title="Patch auth route",
        kind=StepKind.EDIT,
    )


def walk_to(orch: Orchestrator, *states: OrchestrationState) -> None:
    for state in states:
        orch.transition(state)


class TestSuccessPath:
    def test_full_pipeline_reaches_completed(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_model_calls=10), session_id="sess-1", clock=fake_clock
        )
        orch.transition(S.UNDERSTAND)
        orch.consume_model_call()  # intent extraction
        orch.transition(S.INSPECT_REPOSITORY)
        orch.transition(S.BASELINE)
        orch.transition(S.PLAN)
        orch.consume_model_call()  # planning call
        orch.record_goals([make_goal()])
        orch.record_plan([make_step()])
        orch.transition(S.EXECUTE)
        orch.consume_iteration()
        orch.update_step_status("s1", StepStatus.COMPLETED)
        orch.update_goal_status("g1", GoalStatus.COMPLETED)
        orch.transition(S.VERIFY)
        orch.transition(S.AUDIT)  # verification passed
        orch.consume_audit_round()
        orch.transition(S.FINALIZE)
        orch.complete(detail="1 of 1 goals verified")

        session = orch.session
        assert session.state is S.COMPLETED
        assert session.terminal is not None
        assert session.terminal.outcome is TerminalOutcome.COMPLETED
        assert session.terminal.reason is TerminalReason.ALL_GOALS_VERIFIED
        assert session.terminal.detail == "1 of 1 goals verified"

        assert [record.to_state for record in session.history] == [
            S.UNDERSTAND,
            S.INSPECT_REPOSITORY,
            S.BASELINE,
            S.PLAN,
            S.EXECUTE,
            S.VERIFY,
            S.AUDIT,
            S.FINALIZE,
            S.COMPLETED,
        ]
        assert [record.seq for record in session.history] == list(range(1, 10))
        assert session.usage.model_calls == 2
        assert session.usage.iterations == 1
        assert session.usage.audit_rounds == 1
        assert session.goals[0].status is GoalStatus.COMPLETED

    def test_complete_requires_finalize(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        orch.transition(S.UNDERSTAND)
        with pytest.raises(InvalidTransitionError):
            orch.complete()

    def test_transition_to_terminal_state_is_rejected(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        with pytest.raises(InvalidTransitionError, match="complete\\(\\)/fail\\(\\)/cancel\\(\\)"):
            orch.transition(S.COMPLETED)


class TestVerificationFailureAndRecovery:
    def test_failed_verification_routes_through_diagnose_replan_execute(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_model_calls=20), session_id="sess-1", clock=fake_clock
        )
        walk_to(orch, S.UNDERSTAND, S.INSPECT_REPOSITORY, S.BASELINE, S.PLAN, S.EXECUTE)
        orch.consume_iteration()
        orch.record_goals([make_goal()])
        orch.record_plan([make_step()])
        orch.transition(S.VERIFY)
        orch.update_step_status("s1", StepStatus.FAILED)

        verif_evidence = EvidenceRef(
            kind="verification_report", description="pytest: 1 failed, 12 passed"
        )
        orch.transition(S.DIAGNOSE, reason="target test failed", evidence=(verif_evidence,))
        orch.consume_model_call()  # diagnosis call
        orch.transition(S.REPLAN)
        orch.record_plan([make_step("s2")])
        orch.transition(S.EXECUTE)
        orch.consume_iteration()
        orch.consume_retry("g1")
        orch.transition(S.VERIFY)
        orch.transition(S.AUDIT)
        orch.transition(S.FINALIZE)
        orch.complete()

        session = orch.session
        assert session.state is S.COMPLETED
        assert [record.to_state for record in session.history].count(S.EXECUTE) == 2
        recovery_record = session.history[6]  # VERIFY -> DIAGNOSE
        assert recovery_record.from_state is S.VERIFY
        assert recovery_record.to_state is S.DIAGNOSE
        assert recovery_record.evidence[0].kind == "verification_report"
        assert session.usage.retries_by_goal == {"g1": 1}
        assert session.usage.iterations == 2

    def test_unrecoverable_failure_persists_reason_and_evidence(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        orch.transition(S.UNDERSTAND)
        evidence = EvidenceRef(kind="internal_error", description="model client unreachable")
        orch.fail(
            TerminalReason.INTERNAL_ERROR, "provider down after 3 attempts", evidence=(evidence,)
        )

        session = orch.session
        assert session.state is S.FAILED
        assert session.terminal.outcome is TerminalOutcome.FAILED
        assert session.terminal.reason is TerminalReason.INTERNAL_ERROR
        assert session.terminal.evidence[0] == evidence
        assert session.history[-1].to_state is S.FAILED

    def test_first_terminal_result_wins(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        orch.transition(S.UNDERSTAND)
        orch.fail(TerminalReason.INTERNAL_ERROR, "first failure")
        with pytest.raises(InvalidTransitionError, match="terminal"):
            orch.fail(TerminalReason.PLAN_FAILED, "second failure")
        assert orch.session.terminal.detail == "first failure"


class TestAuditLoop:
    def test_audit_can_route_back_to_plan_within_budget(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_audit_rounds=2), session_id="sess-1", clock=fake_clock
        )
        walk_to(orch, S.UNDERSTAND, S.INSPECT_REPOSITORY, S.BASELINE, S.PLAN, S.EXECUTE)
        orch.record_goals([make_goal()])
        orch.record_plan([make_step()])

        # First audit finds more confirmed work and loops back to PLAN.
        orch.transition(S.VERIFY)
        orch.transition(S.AUDIT)
        orch.consume_audit_round()
        orch.transition(S.PLAN)
        orch.record_plan([make_step("s2")])
        orch.transition(S.EXECUTE)
        orch.transition(S.VERIFY)
        orch.transition(S.AUDIT)
        orch.consume_audit_round()
        orch.transition(S.FINALIZE)
        orch.complete(reason=TerminalReason.NO_CONFIRMED_WORK_REMAINING)

        assert orch.session.state is S.COMPLETED
        assert orch.session.terminal.reason is TerminalReason.NO_CONFIRMED_WORK_REMAINING
        assert orch.session.usage.audit_rounds == 2


class TestInvalidTransitionsKeepSessionIntact:
    def test_illegal_edge_is_rejected_and_session_unchanged(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        orch.transition(S.UNDERSTAND)
        before = orch.session

        with pytest.raises(InvalidTransitionError) as excinfo:
            orch.transition(S.EXECUTE)

        payload = excinfo.value.to_dict()
        assert payload["from_state"] == "UNDERSTAND"
        assert payload["to_state"] == "EXECUTE"
        assert payload["session_id"] == "sess-1"
        assert orch.session == before

    def test_no_operation_mutates_state_behind_the_orchestrator(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        with pytest.raises(dataclasses.FrozenInstanceError):
            orch.session.state = S.PLAN  # type: ignore[misc]


class TestBudgetExhaustionTerminatesWithEvidence:
    def test_model_call_exhaustion_fails_session_with_budget_evidence(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_model_calls=2), session_id="sess-1", clock=fake_clock
        )
        orch.consume_model_call()
        orch.consume_model_call()

        with pytest.raises(BudgetExhaustedError) as excinfo:
            orch.consume_model_call()
        assert excinfo.value.to_dict()["budget_kind"] == "MODEL_CALLS"

        session = orch.session
        assert session.state is S.FAILED
        assert session.terminal is not None
        assert session.terminal.outcome is TerminalOutcome.FAILED
        assert session.terminal.reason is TerminalReason.BUDGET_EXHAUSTED
        evidence = session.terminal.evidence
        assert len(evidence) == 1
        assert evidence[0].kind == "budget_snapshot"
        assert evidence[0].metadata["budget_kind"] == "MODEL_CALLS"
        assert evidence[0].metadata["limit"] == 2
        assert evidence[0].metadata["used"] == 2
        assert session.usage.model_calls == 2  # not incremented past the limit

    def test_retry_exhaustion_fails_session(self, fake_clock):
        orch = Orchestrator.start(
            make_request(),
            budget=Budget(max_retries_per_goal=1),
            session_id="sess-1",
            clock=fake_clock,
        )
        orch.consume_retry("g1")
        with pytest.raises(BudgetExhaustedError) as excinfo:
            orch.consume_retry("g1")
        assert excinfo.value.to_dict()["goal_id"] == "g1"
        assert orch.session.state is S.FAILED
        assert orch.session.terminal.reason is TerminalReason.BUDGET_EXHAUSTED

    def test_iteration_exhaustion_fails_session(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_iterations=1), session_id="sess-1", clock=fake_clock
        )
        orch.consume_iteration()
        with pytest.raises(BudgetExhaustedError):
            orch.consume_iteration()
        assert orch.session.state is S.FAILED

    def test_headroom_check_does_not_consume_or_terminate(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_model_calls=1), session_id="sess-1", clock=fake_clock
        )
        orch.consume_model_call()
        assert orch.has_budget_headroom(BudgetKind.MODEL_CALLS) is False
        assert orch.session.state is not S.FAILED  # checking alone never terminates


class TestContentUpdates:
    def test_record_and_update_goals_and_steps(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        orch.transition(S.UNDERSTAND)
        orch.record_goals([make_goal("g1"), make_goal("g2")])
        orch.record_plan([make_step("s1", "g1")])

        orch.update_goal_status("g1", GoalStatus.IN_PROGRESS)
        orch.update_step_status("s1", StepStatus.IN_PROGRESS)
        assert orch.session.goals[0].status is GoalStatus.IN_PROGRESS
        assert orch.session.steps[0].status is StepStatus.IN_PROGRESS

        with pytest.raises(NotFoundError):
            orch.update_goal_status("missing", GoalStatus.COMPLETED)
        with pytest.raises(NotFoundError):
            orch.update_step_status("missing", StepStatus.COMPLETED)

    def test_duplicate_goal_ids_rejected(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        with pytest.raises(ValueError, match="duplicate goal_id"):
            orch.record_goals([make_goal("g1"), make_goal("g1")])

    def test_content_updates_rejected_on_terminal_session(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        orch.transition(S.UNDERSTAND)
        orch.cancel(detail="user aborted")
        with pytest.raises(InvalidTransitionError, match="terminal"):
            orch.record_goals([make_goal()])
        with pytest.raises(InvalidTransitionError, match="terminal"):
            orch.consume_model_call()
        with pytest.raises(InvalidTransitionError, match="terminal"):
            orch.transition(S.INSPECT_REPOSITORY)
        assert orch.session.state is S.CANCELLED
        assert orch.session.terminal.reason is TerminalReason.USER_CANCELLED


class TestSerializationAndRestore:
    def test_restore_mid_session_and_continue(self, fake_clock):
        orch = Orchestrator.start(
            make_request(), budget=Budget(max_model_calls=5), session_id="sess-1", clock=fake_clock
        )
        orch.transition(S.UNDERSTAND)
        orch.consume_model_call()
        orch.transition(S.INSPECT_REPOSITORY)
        orch.record_goals([make_goal()])

        restored = Orchestrator.restore(orch.session.to_dict(), clock=fake_clock)
        assert restored.session == orch.session
        assert restored.session.state is S.INSPECT_REPOSITORY
        assert restored.session.usage.model_calls == 1

        restored.transition(S.BASELINE)
        assert restored.session.state is S.BASELINE
        assert len(restored.session.history) == 3

    def test_restore_completed_session(self, fake_clock):
        orch = Orchestrator.start(make_request(), session_id="sess-1", clock=fake_clock)
        walk_to(orch, S.UNDERSTAND, S.INSPECT_REPOSITORY, S.BASELINE, S.PLAN, S.EXECUTE, S.VERIFY)
        orch.transition(S.AUDIT)
        orch.transition(S.FINALIZE)
        orch.complete(detail="done")

        restored = Orchestrator.restore(orch.session.to_dict(), clock=fake_clock)
        assert restored.session == orch.session
        assert restored.session.terminal.reason is TerminalReason.ALL_GOALS_VERIFIED
        with pytest.raises(InvalidTransitionError, match="terminal"):
            restored.transition(S.PLAN)

    def test_restore_rejects_corrupt_payload(self):
        data = Orchestrator.start(make_request(), session_id="sess-1").session.to_dict()
        data["schema_version"] = 0
        with pytest.raises(SerializationError):
            Orchestrator.restore(data)
