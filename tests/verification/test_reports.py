"""Issue #61 — structured verification reports and evidence persistence."""

from __future__ import annotations

import hashlib
import json

from harness.verification import (
    GoalKind,
    StageName,
    StageOutcome,
    VerificationStatus,
)


def test_report_round_trips_through_serialization(run_ladder, fake_runner, make_goal) -> None:
    report = run_ladder(make_goal(), fake_runner())

    restored = type(report).from_dict(report.to_dict())

    assert restored.to_dict() == report.to_dict()
    assert restored.status is VerificationStatus.VERIFIED


def test_reports_identify_which_stages_ran_or_were_skipped(
    run_ladder, fake_runner, make_goal
) -> None:
    # Feature goal without reproducer and without related tests.
    report = run_ladder(
        make_goal(goal_kind=GoalKind.FEATURE, reproducer_command=None, related_tests=()),
        fake_runner(),
    )

    by_name = {stage.name: stage for stage in report.stages}
    assert by_name[StageName.REPRODUCER].outcome is StageOutcome.SKIPPED
    assert "no reproducer" in by_name[StageName.REPRODUCER].summary
    assert by_name[StageName.RELATED_TESTS].outcome is StageOutcome.SKIPPED
    assert by_name[StageName.SYNTAX].outcome is StageOutcome.PASSED
    assert by_name[StageName.TARGETED_TESTS].outcome is StageOutcome.PASSED
    # Stages that ran record their exact command; skipped ones do not.
    assert by_name[StageName.SYNTAX].command is not None
    assert "ast.parse" in by_name[StageName.SYNTAX].command
    assert by_name[StageName.REPRODUCER].command is None


def test_stage_output_over_limit_becomes_hashed_artifact(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    from harness.core.models import UserRequest
    from harness.telemetry import RunStore

    store = RunStore.start(
        UserRequest(repository_path=str(tmp_path), objective="verify goals"),
        runs_root=tmp_path / "runs",
    )
    big_output = "E" * 9000
    runner = fake_runner(results={"::test_add": (1, big_output)})

    report = run_ladder(make_goal(), runner, store=store)

    failing = report.first_failure
    assert failing is not None and failing.artifact is not None
    artifact_path = store.run_dir / failing.artifact.path
    content = artifact_path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == failing.artifact.sha256
    # The full output is not inlined into the report.
    assert big_output not in json.dumps(report.to_dict())


def test_persisted_verification_json_reconstructs_outcome(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    from harness.core.models import UserRequest
    from harness.telemetry import RunStore

    store = RunStore.start(
        UserRequest(repository_path=str(tmp_path), objective="verify goals"),
        runs_root=tmp_path / "runs",
    )

    report = run_ladder(make_goal(), fake_runner(), store=store)

    persisted = json.loads((store.run_dir / "verification.json").read_text())
    assert persisted["goal_id"] == "G1"
    assert persisted["status"] == "verified"
    assert persisted["first_failure"] is None
    names = [stage["name"] for stage in persisted["stages"]]
    assert names == [stage.name.value for stage in report.stages]
    for stage in persisted["stages"]:
        assert stage["outcome"] in {"passed", "failed", "skipped", "unavailable"}
        assert isinstance(stage["duration_ms"], int)


def test_first_failure_carries_focused_evidence_in_report(
    tmp_path, run_ladder, fake_runner, make_goal
) -> None:
    from harness.core.models import UserRequest
    from harness.telemetry import RunStore

    store = RunStore.start(
        UserRequest(repository_path=str(tmp_path), objective="verify goals"),
        runs_root=tmp_path / "runs",
    )
    runner = fake_runner(
        results={"::test_add": (1, "line1\n" * 200 + "AssertionError: add(1,2) != 4")}
    )

    report = run_ladder(make_goal(), runner, store=store)

    assert report.first_failure is not None
    assert len(report.first_failure.summary) <= 320
    assert report.first_failure.summary.endswith("AssertionError: add(1,2) != 4")
