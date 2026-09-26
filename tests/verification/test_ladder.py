"""Issue #60 — ordered verification ladder with first-failure stop."""

from __future__ import annotations

from harness.verification import (
    LADDER_ORDER,
    StageName,
    StageOutcome,
    VerificationStatus,
    changed_files_from_diff,
)


def test_all_passing_reaches_verified_in_prd_order(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    report = run_ladder(make_goal(), fake_runner())

    assert report.status is VerificationStatus.VERIFIED
    assert [stage.name for stage in report.stages] == list(LADDER_ORDER)
    outcomes = {stage.name: stage.outcome for stage in report.stages}
    assert outcomes[StageName.SYNTAX] is StageOutcome.PASSED
    assert outcomes[StageName.REPRODUCER] is StageOutcome.PASSED
    assert outcomes[StageName.TARGETED_TESTS] is StageOutcome.PASSED
    assert outcomes[StageName.RELATED_TESTS] is StageOutcome.PASSED
    assert outcomes[StageName.FULL_SUITE] is StageOutcome.PASSED
    assert outcomes[StageName.DIFF_SCOPE] is StageOutcome.PASSED
    assert report.first_failure is None


def test_ladder_stops_at_first_failure_and_records_focus(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    runner = fake_runner(results={"ast.parse": (1, "SyntaxError: invalid syntax (app/math.py)")})

    report = run_ladder(make_goal(), runner)

    assert report.status is VerificationStatus.FAILED
    assert report.first_failure is not None
    assert report.first_failure.name is StageName.SYNTAX
    assert "SyntaxError" in report.first_failure.summary
    by_name = {stage.name: stage for stage in report.stages}
    assert by_name[StageName.BUILD].outcome is StageOutcome.SKIPPED
    assert by_name[StageName.REPRODUCER].outcome is StageOutcome.SKIPPED
    assert by_name[StageName.DIFF_SCOPE].outcome is StageOutcome.SKIPPED
    assert "not reached" in by_name[StageName.DIFF_SCOPE].summary
    # Nothing ran after the failure.
    assert all("ast.parse" in " ".join(call) for call in runner.calls)


def test_failing_test_evidence_cannot_be_overridden(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    # There is no model-verdict input anywhere in the API; the only way a
    # stage passes is exit code 0. A failing targeted test fails the report
    # even when every other stage passes.
    runner = fake_runner(results={"::test_add": (1, "AssertionError: add(1, 2) != 4")})

    report = run_ladder(make_goal(), runner)

    assert report.status is VerificationStatus.FAILED
    assert report.first_failure is not None
    assert report.first_failure.name is StageName.TARGETED_TESTS
    assert "AssertionError" in report.first_failure.summary


def test_unavailable_stage_does_not_stop_the_ladder(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    runner = fake_runner(results={"ast.parse": (None, "executable not found: py")})

    report = run_ladder(make_goal(), runner)

    outcomes = {stage.name: stage.outcome for stage in report.stages}
    assert outcomes[StageName.SYNTAX] is StageOutcome.UNAVAILABLE
    assert outcomes[StageName.REPRODUCER] is StageOutcome.PASSED
    assert outcomes[StageName.DIFF_SCOPE] is StageOutcome.PASSED
    assert report.status is VerificationStatus.VERIFIED


def test_timeout_is_a_failure(tmp_path, run_ladder, fake_runner, make_goal) -> None:
    runner = fake_runner(timeouts=("repro.py",))

    report = run_ladder(make_goal(), runner)

    assert report.status is VerificationStatus.FAILED
    assert report.first_failure is not None
    assert report.first_failure.name is StageName.REPRODUCER
    assert "timed out" in report.first_failure.summary


def test_no_adapter_is_inconclusive_never_verified(tmp_path, fake_runner, make_goal) -> None:
    from harness.verification import run_verification_ladder

    report = run_verification_ladder(make_goal(), tmp_path, runner=fake_runner(), adapter=None)

    outcomes = {stage.name: stage.outcome for stage in report.stages}
    assert outcomes[StageName.SYNTAX] is StageOutcome.UNAVAILABLE
    assert outcomes[StageName.FULL_SUITE] is StageOutcome.UNAVAILABLE
    assert outcomes[StageName.DIFF_SCOPE] is StageOutcome.PASSED
    assert report.status is VerificationStatus.INCONCLUSIVE


def test_changed_files_from_diff_parses_and_dedupes() -> None:
    diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "+++ b/app/x.py\n"
        "@@ -1 +1 @@\n"
        "diff --git a/app/x.py b/app/x.py\n"
        "+++ b/app/x.py\n"
        "diff --git a/notes.md b/notes.md\n"
        "+++ b/notes.md\n"
        "diff --git a/deleted.py b/deleted.py\n"
        "+++ /dev/null\n"
    )

    assert changed_files_from_diff(diff) == ["app/x.py", "notes.md"]
    assert changed_files_from_diff(None) == []
