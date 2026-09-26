"""Issue #63 — failure classification and focused evidence collection."""

from __future__ import annotations

from harness.execution.runner import CommandResult
from harness.recovery import (
    FailureClass,
    FailureInput,
    classify_failure,
    collect_failure_evidence,
    stage_result_to_input,
)
from harness.verification.models import StageName, StageOutcome, StageResult


def _command(
    exit_code: int | None = 1,
    timed_out: bool = False,
    output: str = "boom",
) -> CommandResult:
    return CommandResult(
        argv=("python", "-m", "pytest", "-q"),
        exit_code=exit_code,
        stdout="" if exit_code else output,
        stderr=output if exit_code else "",
        duration_ms=42,
        timed_out=timed_out,
        unavailable_reason=None if exit_code is not None else "executable not found: python",
    )


def test_missing_executable_is_environment_not_code_defect() -> None:
    failure = FailureInput(
        goal_id="G1", stage=StageName.TARGETED_TESTS, command_result=_command(exit_code=None)
    )

    assert classify_failure(failure) is FailureClass.ENVIRONMENT


def test_timeout_is_its_own_class() -> None:
    failure = FailureInput(
        goal_id="G1", stage=StageName.FULL_SUITE, command_result=_command(timed_out=True)
    )

    assert classify_failure(failure) is FailureClass.TIMEOUT


def test_stage_identity_drives_code_defect_classes() -> None:
    cases = [
        (StageName.SYNTAX, FailureClass.SYNTAX),
        (StageName.BUILD, FailureClass.BUILD),
        (StageName.REPRODUCER, FailureClass.TEST),
        (StageName.TARGETED_TESTS, FailureClass.TEST),
        (StageName.FULL_SUITE, FailureClass.TEST),
        (StageName.DIFF_SCOPE, FailureClass.PATCH),
    ]
    for stage, expected in cases:
        failure = FailureInput(goal_id="G1", stage=stage, command_result=_command())
        assert classify_failure(failure) is expected, stage


def test_patch_plan_and_loop_flags_classify_directly() -> None:
    assert classify_failure(FailureInput(goal_id="G", patch_failed=True)) is FailureClass.PATCH
    assert classify_failure(FailureInput(goal_id="G", plan_invalid=True)) is FailureClass.PLAN
    assert classify_failure(FailureInput(goal_id="G", loop_detected=True)) is FailureClass.LOOP


def test_evidence_is_focused_and_narrow() -> None:
    long_error = (
        "\n".join(f"noise line {i}" for i in range(200)) + "\nAssertionError: add(1, 2) != 4"
    )
    failure = FailureInput(
        goal_id="G1",
        stage=StageName.TARGETED_TESTS,
        command_result=_command(output=long_error),
        latest_diff="diff --git a/app/math.py b/app/math.py\n+++ b/app/math.py\n",
        relevant_tests=("tests/test_math.py::test_add",),
        error_text=long_error,
    )

    evidence = collect_failure_evidence(failure)

    assert evidence.failure_class is FailureClass.TEST
    assert len(evidence.concise_error) <= 300
    assert evidence.concise_error.endswith("AssertionError: add(1, 2) != 4")
    assert evidence.failing_command == "python -m pytest -q"
    assert evidence.exit_code == 1
    assert evidence.affected_paths == ("app/math.py",)
    assert evidence.relevant_tests == ("tests/test_math.py::test_add",)
    # The bundle is narrow by construction: no run-history field exists.
    assert "history" not in evidence.to_dict()
    assert set(evidence.to_dict()) == {
        "failure_class", "concise_error", "goal_id", "stage", "failing_command",
        "exit_code", "timed_out", "latest_diff", "affected_paths",
        "relevant_tests", "prior_attempts", "artifact",
    }


def test_environment_evidence_keeps_runtime_reason_visible() -> None:
    result = _command(exit_code=None)
    evidence = collect_failure_evidence(
        FailureInput(goal_id="G1", stage=StageName.SYNTAX, command_result=result)
    )

    assert evidence.failure_class is FailureClass.ENVIRONMENT
    assert evidence.failing_command is not None
    assert evidence.exit_code is None


def test_stage_result_to_input_bridge() -> None:
    stage = StageResult(
        name=StageName.TARGETED_TESTS,
        outcome=StageOutcome.FAILED,
        command="pytest -q tests/test_math.py",
        exit_code=1,
        duration_ms=931,
        summary="AssertionError: add(1, 2) != 4",
    )

    failure = stage_result_to_input("G9", stage)

    assert failure.goal_id == "G9"
    assert failure.stage is StageName.TARGETED_TESTS
    evidence = collect_failure_evidence(failure)
    assert evidence.stage == "targeted_tests"
    assert "AssertionError" in evidence.concise_error
