"""Issue #62 — reproducers, regressions, diff/scope validation, VERIFIED gating."""

from __future__ import annotations

import json
from pathlib import Path

from harness.verification import GoalKind, StageName, StageOutcome, VerificationStatus


def test_out_of_scope_changes_fail_diff_scope(run_ladder, fake_runner, make_goal) -> None:
    runner = fake_runner()

    report = run_ladder(
        make_goal(allowed_scope=("backend/",)), runner
    )

    diff_scope = next(s for s in report.stages if s.name is StageName.DIFF_SCOPE)
    assert diff_scope.outcome is StageOutcome.FAILED
    assert "app/math.py" in diff_scope.summary  # named as out of scope
    assert report.status is VerificationStatus.FAILED
    assert report.first_failure is diff_scope


def test_in_scope_changes_pass_and_reach_verified(run_ladder, fake_runner, make_goal) -> None:
    report = run_ladder(make_goal(allowed_scope=("app/",)), fake_runner())

    diff_scope = next(s for s in report.stages if s.name is StageName.DIFF_SCOPE)
    assert diff_scope.outcome is StageOutcome.PASSED
    assert "app/" in diff_scope.summary
    assert report.status is VerificationStatus.VERIFIED


def test_bare_scope_names_match_files_at_that_path(run_ladder, fake_runner, make_goal) -> None:
    report = run_ladder(make_goal(allowed_scope=("app/math.py",)), fake_runner())

    diff_scope = next(s for s in report.stages if s.name is StageName.DIFF_SCOPE)
    assert diff_scope.outcome is StageOutcome.PASSED


def test_reproducer_is_required_and_rerun_for_bug_fix(
    run_ladder, fake_runner, make_goal
) -> None:
    runner = fake_runner(results={"repro.py": (1, " reproduction failed: add(1,2) != 4")})

    report = run_ladder(make_goal(goal_kind=GoalKind.BUG_FIX), runner)

    # The reproducer ran (before the targeted tests) and its failure
    # stopped the ladder, even though the tests themselves would pass.
    repro_calls = [c for c in runner.calls if "repro.py" in " ".join(c)]
    assert repro_calls == [("python", "repro.py")]
    pytest_calls = [c for c in runner.calls if "pytest" in " ".join(c)]
    assert not pytest_calls  # ladder stopped before any test command
    assert report.first_failure is not None
    assert report.first_failure.name is StageName.REPRODUCER
    assert "reproduction failed" in report.first_failure.summary
    assert report.status is VerificationStatus.FAILED


def test_missing_reproducer_is_skipped_not_failed(run_ladder, fake_runner, make_goal) -> None:
    goal = make_goal(goal_kind=GoalKind.BUG_FIX, reproducer_command=None)
    report = run_ladder(goal, fake_runner())

    reproducer = next(s for s in report.stages if s.name is StageName.REPRODUCER)
    assert reproducer.outcome is StageOutcome.SKIPPED
    assert "no reproducer available" in reproducer.summary
    assert report.status is VerificationStatus.VERIFIED  # other evidence carried it


def test_no_test_evidence_is_inconclusive(tmp_path, fake_runner, make_goal) -> None:
    from harness.adapters import NodeAdapter
    from harness.verification import run_verification_ladder

    root = tmp_path / "nodeless"
    root.mkdir()
    (root / "package.json").write_text('{"name": "bare"}')

    report = run_verification_ladder(
        make_goal(reproducer_command=None), root, runner=fake_runner(), adapter=NodeAdapter()
    )

    outcomes = {stage.name: stage.outcome for stage in report.stages}
    assert outcomes[StageName.FULL_SUITE] is StageOutcome.SKIPPED
    assert outcomes[StageName.TARGETED_TESTS] is StageOutcome.SKIPPED
    assert outcomes[StageName.DIFF_SCOPE] is StageOutcome.PASSED
    assert report.status is VerificationStatus.INCONCLUSIVE


def test_verified_requires_diff_scope_even_when_tests_pass(
    run_ladder, fake_runner, make_goal
) -> None:
    # Everything passes, but the diff touches a file outside the approved
    # scope: the goal must NOT reach VERIFIED.
    report = run_ladder(make_goal(allowed_scope=("services/",)), fake_runner())

    assert report.status is VerificationStatus.FAILED
    assert report.first_failure is not None
    assert report.first_failure.name is StageName.DIFF_SCOPE


def test_real_end_to_end_ladder_on_python_project(tmp_path: Path, make_file_fixture) -> None:
    """Full ladder with real adapter and real subprocesses, fail then pass."""
    from harness.adapters import select_adapter
    from harness.verification import VerificationInput, run_verification_ladder

    root = tmp_path / "repo"
    root.mkdir()
    make_file_fixture(root, "pyproject.toml", '[project]\nname = "demo"\n')
    # Empty root conftest puts the repo root on the inner pytest's sys.path.
    make_file_fixture(root, "conftest.py", "")
    make_file_fixture(root, "app/math.py", "def add(a, b):\n    return a - b\n")
    make_file_fixture(
        root,
        "tests/test_math.py",
        "from app.math import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    adapter = select_adapter(root)
    assert adapter is not None

    diff = (
        "diff --git a/app/math.py b/app/math.py\n"
        "--- a/app/math.py\n"
        "+++ b/app/math.py\n"
    )
    import sys

    reproducer = (
        f'"{sys.executable}" -c "from app.math import add; assert add(1, 2) == 3"'
    )

    failing = run_verification_ladder(
        VerificationInput(
            goal_id="G-BUG",
            goal_kind=GoalKind.BUG_FIX,
            reproducer_command=reproducer,
            targeted_tests=("tests/test_math.py::test_add",),
            diff_text=diff + "+++ b/app/math.py\n",
        ),
        root,
        timeout_seconds=60,
    )
    assert failing.status is VerificationStatus.FAILED
    assert failing.first_failure is not None
    assert "assert" in failing.first_failure.summary.lower()

    # The fix lands; the same ladder now verifies.
    make_file_fixture(root, "app/math.py", "def add(a, b):\n    return a + b\n")
    verified = run_verification_ladder(
        VerificationInput(
            goal_id="G-BUG",
            goal_kind=GoalKind.BUG_FIX,
            reproducer_command=reproducer,
            targeted_tests=("tests/test_math.py::test_add",),
            diff_text=diff + "+++ b/app/math.py\n",
        ),
        root,
        timeout_seconds=60,
    )

    assert verified.status is VerificationStatus.VERIFIED
    outcomes = {stage.name: stage.outcome for stage in verified.stages}
    assert outcomes[StageName.SYNTAX] is StageOutcome.PASSED
    assert outcomes[StageName.REPRODUCER] is StageOutcome.PASSED
    assert outcomes[StageName.FULL_SUITE] is StageOutcome.PASSED
    assert json.dumps(verified.to_dict())
