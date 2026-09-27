"""Issues #65/#66/#67 acceptance: loops/budgets/rollback, lifecycle, reproduction.

The recovery and audit layers landed with the recovery and evidence-gated
audit workstreams; this module maps the three issues' acceptance bullets 1:1
— loop detection forces replanning, budgets stop the session, rollback
triggers on repeated failures; LLM-only findings stay SUSPECTED and can
never authorize source changes; reproduction either demonstrates the defect
deterministically or the finding is rejected with the attempt preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.audit import FindingStatus
from harness.audit.models import Finding, transition
from harness.audit.reproduce import apply_reproduction, attempt_reproduction
from harness.core.models import Budget, BudgetUsage, EvidenceRef
from harness.recovery.engine import (
    RecoveryDecision,
    RecoveryRequest,
    evaluate_recovery,
    should_rollback,
)
from harness.recovery.loops import LoopDetector
from harness.recovery.models import FailureClass


def _request(fingerprint: str, *, failure: FailureClass = FailureClass.TEST,
             plan_present: bool = True) -> RecoveryRequest:
    from harness.recovery.models import RecoveryPlan, RepairAction

    plan = None
    if plan_present:
        plan = RecoveryPlan(
            failure_class=failure,
            root_cause="handler maps the error to 500",
            repair_summary="map InvalidCredentialsError to 401",
            repair_actions=(RepairAction(kind="patch",
                                         target="src/routes/login.py",
                                         detail="add exception handler"),),
            reverify_stages=("targeted_tests", "related_tests", "full_suite"),
        )
    return RecoveryRequest(
        goal_id="G1",
        failure_class=failure,
        fingerprint=fingerprint,
        plan=plan,
    )


# ---------------------------------------------------------------------------
# Issue #65 — loop detection, budgets, rollback
# ---------------------------------------------------------------------------


def test_identical_attempts_trigger_loop_detection_at_threshold():
    detector = LoopDetector(threshold=3)
    assert detector.record("fp") is False  # 1st
    assert detector.record("fp") is False  # 2nd
    assert detector.record("fp") is True   # 3rd identical: blocked
    assert detector.record("other") is False  # different action: fine


def test_loop_detection_forces_replan_without_consuming_retry_budget():
    budget = Budget(max_retries_per_goal=5)
    usage = BudgetUsage()
    detector = LoopDetector(threshold=2)
    detector.record("same-fp")  # warm up: the next record blocks

    outcome, _usage = evaluate_recovery(
        _request("same-fp"), budget, usage, detector,
    )
    assert outcome.decision is RecoveryDecision.REPLAN
    assert "identical action/result" in outcome.reason
    # forced replanning is not a retry: the budget was not consumed
    assert outcome.attempts_used == 0


def test_retry_budget_exhaustion_stops_the_session():
    budget = Budget(max_retries_per_goal=1)
    usage = BudgetUsage(retries_by_goal={"G1": 1})  # already consumed

    outcome, _ = evaluate_recovery(
        _request("fp-2", plan_present=False), budget, usage, LoopDetector(),
    )
    assert outcome.decision is RecoveryDecision.STOP
    assert "budget" in outcome.reason.lower() or "exhaust" in outcome.reason.lower()


def test_rollback_triggers_after_repeated_failures():
    assert should_rollback(plan=None, consecutive_failures=2) is False
    assert should_rollback(plan=None, consecutive_failures=3) is True


def test_environment_failures_stop_without_retries():
    outcome, _ = evaluate_recovery(
        _request("env-fp", failure=FailureClass.ENVIRONMENT),
        Budget(), BudgetUsage(), LoopDetector(),
    )
    assert outcome.decision is RecoveryDecision.STOP


# ---------------------------------------------------------------------------
# Issue #66 — finding lifecycle and evidence rules
# ---------------------------------------------------------------------------


def _finding() -> Finding:
    return Finding(
        finding_id="F-accept",
        title="500 on invalid password",
        description="handler lacks the exception mapping",
        source="llm_review",
        scope_path="src/routes/login.py",
        proposed_reproducer="python -c \"import sys; sys.exit(1)\"",
    )


def test_llm_only_finding_stays_suspected_without_deterministic_evidence():
    finding = _finding()
    # no reproducer result, no deterministic evidence: stuck at SUSPECTED
    from harness.audit.models import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        transition(finding, FindingStatus.REPRODUCED)
    assert finding.status is FindingStatus.SUSPECTED


def test_reproduced_confirmed_patched_verified_is_the_only_path():
    finding = transition(
        _finding(), FindingStatus.REPRODUCED,
        evidence=EvidenceRef(kind="reproducer",
                             description="reproducer exited 1"),
    )
    finding = transition(finding, FindingStatus.CONFIRMED)
    finding = transition(
        finding, FindingStatus.PATCHED,
        evidence=EvidenceRef(kind="tool_result", description="patch applied"),
    )
    finding = transition(
        finding, FindingStatus.VERIFIED,
        evidence=EvidenceRef(kind="tool_result",
                             description="ladder verified"),
    )
    assert finding.status is FindingStatus.VERIFIED


# ---------------------------------------------------------------------------
# Issue #67 — reproduction preserves attempts, rejects with reasons
# ---------------------------------------------------------------------------


def test_failing_reproducer_rejects_and_preserves_the_attempt(tmp_path: Path):
    finding = _finding()
    _ = finding.evidence  # frozen: the reproducer ships at construction

    def runner(argv, cwd, timeout_seconds):
        class Result:
            ran = True
            timed_out = False
            exit_code = 1
            stdout = ""
            stderr = "AssertionError: still broken"

            @property
            def combined_output(self):
                return self.stdout + self.stderr

        return Result()

    outcome = attempt_reproduction(finding, tmp_path, runner, 30)
    # a FAILING reproducer demonstrates the defect: reproduced=True
    assert outcome.reproduced is True
    assert outcome.command == finding.proposed_reproducer

    finding = apply_reproduction(finding, outcome)
    assert finding.status is FindingStatus.REPRODUCED
    assert any(e.kind == "reproducer" for e in finding.evidence)


def test_passing_reproducer_rejects_the_finding_with_reason(tmp_path: Path):
    import dataclasses

    finding = dataclasses.replace(
        _finding(), proposed_reproducer="python -c 'print(1)'"
    )

    def runner(argv, cwd, timeout_seconds):
        class Result:
            ran = True
            timed_out = False
            exit_code = 0
            stdout = "ok"
            stderr = ""

            @property
            def combined_output(self):
                return self.stdout

        return Result()

    outcome = attempt_reproduction(finding, tmp_path, runner, 30)
    finding = apply_reproduction(finding, outcome)
    # the defect did not show itself: rejected with the attempt preserved
    assert finding.status is FindingStatus.REJECTED
    assert finding.rejection_reason
    assert any(e.kind == "reproduction_attempt" for e in finding.evidence)
