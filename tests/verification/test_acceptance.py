"""Issue #62 acceptance evidence: reproducers, regression widening, VERIFIED gate.

The ladder itself landed with the verification workstream; this module maps
the issue's acceptance bullets 1:1 — a goal reaches VERIFIED only after
every required stage (including the original reproducer for bug fixes and
the final diff/scope validation) passes.
"""

from harness.verification import (
    GoalKind,
    StageName,
    StageOutcome,
    VerificationStatus,
)


def test_verified_requires_every_stage_not_just_tests(
    run_ladder, fake_runner, make_goal
) -> None:
    report = run_ladder(make_goal(), fake_runner())
    assert report.status is VerificationStatus.VERIFIED
    # every required stage passed; not-applicable stages were SKIPPED, and
    # nothing FAILED or was UNAVAILABLE
    assert all(
        stage.outcome is StageOutcome.PASSED or stage.outcome is StageOutcome.SKIPPED
        for stage in report.stages
    )
    # all seven PRD stages are present in the report, in PRD order
    assert [s.name for s in report.stages] == list(StageName)


def test_regressions_widen_from_targeted_to_related_to_full(
    run_ladder, fake_runner, make_goal
) -> None:
    runner = fake_runner()
    report = run_ladder(
        make_goal(
            goal_kind=GoalKind.FEATURE,
            reproducer_command=None,
            targeted_tests=("tests/test_math.py::test_add",),
            related_tests=("tests/test_math.py",),
        ),
        runner,
    )

    joined_calls = [" ".join(call) for call in runner.calls]
    assert any("tests/test_math.py::test_add" in call for call in joined_calls)
    assert any(
        "tests/test_math.py" in call and "::test_add" not in call
        for call in joined_calls
    )
    assert any("pytest" in call and "test_math" not in call for call in joined_calls)

    # and they ran in widening order
    order = [
        stage.name
        for stage in report.stages
        if stage.name
        in (
            StageName.TARGETED_TESTS,
            StageName.RELATED_TESTS,
            StageName.FULL_SUITE,
        )
    ]
    assert order == [
        StageName.TARGETED_TESTS,
        StageName.RELATED_TESTS,
        StageName.FULL_SUITE,
    ]
    assert report.status is VerificationStatus.VERIFIED


def test_regression_failure_after_targeted_pass_fails_the_goal(
    run_ladder, fake_runner, make_goal
) -> None:
    # "test_sub" appears only in the widened related-tests run
    runner = fake_runner(results={"test_sub": (1, "1 failed regression")})
    report = run_ladder(
        make_goal(
            goal_kind=GoalKind.FEATURE,
            reproducer_command=None,
            targeted_tests=("tests/test_math.py::test_add",),
            related_tests=("tests/test_math.py::test_sub",),
        ),
        runner,
    )

    assert report.status is VerificationStatus.FAILED
    assert report.first_failure is not None
    assert report.first_failure.name in (
        StageName.RELATED_TESTS,
        StageName.FULL_SUITE,
    )
    # targeted passing is not enough: nothing reaches VERIFIED
    targeted = next(
        s for s in report.stages if s.name is StageName.TARGETED_TESTS
    )
    assert targeted.outcome is StageOutcome.PASSED
    assert report.status is not VerificationStatus.VERIFIED
