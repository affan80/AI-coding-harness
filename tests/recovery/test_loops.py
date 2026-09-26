"""Issue #65 — fingerprints, loop blocking, budgets, rollback, persistence."""

from __future__ import annotations

import json

from harness.core.models import Budget, BudgetUsage, UserRequest
from harness.recovery import (
    FailureClass,
    FailureEvidence,
    LoopDetector,
    RecoveryDecision,
    RecoveryOutcome,
    RecoveryPlan,
    RecoveryRequest,
    action_fingerprint,
    evaluate_recovery,
    persist_recovery_outcome,
    result_digest,
    should_rollback,
)
from harness.telemetry import RunStore


def _plan(summary: str = "handle None password in login") -> RecoveryPlan:
    return RecoveryPlan(
        failure_class=FailureClass.TEST,
        root_cause="invalid password path raises instead of returning 401",
        repair_summary=summary,
        reverify_stages=("targeted_tests", "related_tests", "full_suite", "diff_scope"),
    )


def test_fingerprint_normalizes_argument_order_and_whitespace() -> None:
    first = action_fingerprint("apply_patch", {"path": "a.py", "hunk": "@@ -1 +1 @@"}, "out")
    second = action_fingerprint("apply_patch", {"hunk": "@@ -1 +1 @@", "path": "a.py"}, "out")

    assert first == second
    assert first != action_fingerprint(
        "apply_patch", {"path": "a.py", "hunk": "@@ -1 +1 @@"}, "different"
    )
    assert first != action_fingerprint(
        "run_command", {"path": "a.py", "hunk": "@@ -1 +1 @@"}, "out"
    )


def test_artifact_hash_takes_precedence_in_result_digest() -> None:
    from harness.telemetry.models import ArtifactRef

    artifact = ArtifactRef(path="artifacts/x.txt", sha256="abc123", size=10)

    assert result_digest("anything", artifact) == "abc123"
    assert result_digest("anything") != result_digest("anything else")


def test_loop_detector_blocks_at_threshold() -> None:
    detector = LoopDetector(threshold=3)
    fingerprint = action_fingerprint("run_command", {"cmd": "pytest -q"}, "fail")

    assert detector.record(fingerprint) is False
    assert detector.record(fingerprint) is False
    assert detector.record(fingerprint) is True  # third identical attempt: blocked
    assert detector.blocked(fingerprint)
    assert detector.count_for(fingerprint) == 3


def test_loop_detector_rejects_degenerate_threshold() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least 2"):
        LoopDetector(threshold=1)


def test_identical_attempts_force_replan_without_consuming_budget() -> None:
    budget = Budget(max_retries_per_goal=5)
    usage = BudgetUsage()
    detector = LoopDetector(threshold=3)
    fingerprint = action_fingerprint(
        "apply_patch", {"path": "app.py"}, "diff"
    )

    for _ in range(2):
        evaluate_recovery(
            RecoveryRequest(
                goal_id="G1", failure_class=FailureClass.TEST,
                fingerprint=fingerprint, plan=_plan(),
            ),
            budget, usage, detector,
        )

    outcome, usage_after = evaluate_recovery(
        RecoveryRequest(goal_id="G1", failure_class=FailureClass.TEST,
                        fingerprint=fingerprint, plan=_plan()),
        budget, usage, detector,
    )

    assert outcome.decision is RecoveryDecision.REPLAN
    assert "identical action" in outcome.reason
    assert outcome.blocked_fingerprint == fingerprint
    # Blocking does not burn retry budget.
    assert usage_after.retries_for("G1") == 0


def test_retries_consume_explicit_per_goal_budget() -> None:
    budget = Budget(max_retries_per_goal=2)
    usage = BudgetUsage()
    detector = LoopDetector()

    first, usage = evaluate_recovery(
        RecoveryRequest(goal_id="G1", failure_class=FailureClass.TEST,
                        fingerprint="fp-1", plan=_plan()),
        budget, usage, detector,
    )
    second, usage = evaluate_recovery(
        RecoveryRequest(goal_id="G1", failure_class=FailureClass.TEST,
                        fingerprint="fp-2", plan=_plan()),
        budget, usage, detector,
    )
    exhausted, usage_after = evaluate_recovery(
        RecoveryRequest(goal_id="G1", failure_class=FailureClass.TEST,
                        fingerprint="fp-3", plan=_plan()),
        budget, usage, detector,
    )

    assert first.decision is RecoveryDecision.RETRY
    assert first.attempts_used == 1
    assert second.decision is RecoveryDecision.RETRY
    assert usage.retries_for("G1") == 2
    assert exhausted.decision is RecoveryDecision.STOP
    assert "budget exhausted" in exhausted.reason
    # Other goals still have their own budget.
    assert usage_after.retries_for("G2") == 0


def test_environment_failures_stop_without_retries() -> None:
    budget = Budget(max_retries_per_goal=5)
    usage = BudgetUsage()

    outcome, usage_after = evaluate_recovery(
        RecoveryRequest(goal_id="G1", failure_class=FailureClass.ENVIRONMENT,
                        fingerprint="fp-env", plan=_plan()),
        budget, usage, LoopDetector(),
    )

    assert outcome.decision is RecoveryDecision.STOP
    assert "environment" in outcome.reason
    assert usage_after.retries_for("G1") == 0  # no retry burned on a non-code defect


def test_missing_plan_forces_replan() -> None:
    outcome, _usage = evaluate_recovery(
        RecoveryRequest(goal_id="G1", failure_class=FailureClass.TEST,
                        fingerprint="fp-1", plan=None),
        Budget(), BudgetUsage(), LoopDetector(),
    )

    assert outcome.decision is RecoveryDecision.REPLAN
    assert "no usable recovery plan" in outcome.reason


def test_rollback_after_repeated_consecutive_failures() -> None:
    assert not should_rollback(None, 2)
    assert should_rollback(None, 3)


def test_terminal_recovery_reasons_persist_with_evidence(tmp_path) -> None:
    store = RunStore.start(
        UserRequest(repository_path=str(tmp_path), objective="fix things"),
        runs_root=tmp_path / "runs",
    )
    evidence = FailureEvidence(
        failure_class=FailureClass.ENVIRONMENT,
        concise_error="executable not found: python",
        goal_id="G1",
    )
    outcome = RecoveryOutcome(
        decision=RecoveryDecision.STOP, reason="environment failure", attempts_used=0
    )

    persist_recovery_outcome(store, outcome, evidence)

    persisted = json.loads((store.run_dir / "recovery.json").read_text())
    assert persisted["outcome"]["decision"] == "stop"
    assert persisted["evidence"]["failure_class"] == "environment"
    events = [
        json.loads(line)
        for line in (store.run_dir / "events.jsonl").read_text().splitlines()
    ]
    assert events[-1]["kind"] == "recovery"
    assert "stop" in events[-1]["message"]
