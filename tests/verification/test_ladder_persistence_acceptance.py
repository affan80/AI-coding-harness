"""Issues #60/#61 acceptance: ordered ladder, persistence, staged evidence.

The ladder and report layers landed with the verification workstream; this
module maps the two issues' acceptance bullets 1:1 — stages run and stop in
the PRD §16 order, and every report persists to verification.json with
per-stage evidence a reviewer can reconstruct.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.verification import (
    StageName,
    StageOutcome,
    VerificationStatus,
)


def test_ladder_runs_all_stages_in_prd_order_and_stops_at_first_failure(
    run_ladder, fake_runner, make_goal
) -> None:
    runner = fake_runner(results={"repro.py": (1, "reproduction failed")})
    report = run_ladder(make_goal(), runner)

    names = [s.name for s in report.stages]
    assert names == list(StageName)  # every stage appears, in PRD §16 order
    failed = [s for s in report.stages if s.outcome is StageOutcome.FAILED]
    assert len(failed) == 1  # stops at the first failure
    after_failure = [
        s for s in report.stages[report.stages.index(failed[0]) + 1:]
    ]
    assert all(s.outcome is StageOutcome.SKIPPED for s in after_failure)


def test_report_persists_to_verification_json_with_staged_evidence(
    run_ladder, fake_runner, make_goal, tmp_path
) -> None:
    from harness.telemetry.store import RunStore

    request = __import__(
        "harness.core.models", fromlist=["UserRequest"]
    ).UserRequest(objective="obj", repository_path=str(tmp_path))
    store = RunStore.start(request, runs_root=tmp_path / "runs")

    report = run_ladder(make_goal(), fake_runner(), store=store)

    verification = json.loads(
        (Path(store.run_dir) / "verification.json").read_text()
    )
    assert verification["goal_id"] == report.goal_id
    stage_names = [s["name"] for s in verification["stages"]]
    assert stage_names == [s.name for s in report.stages]
    # each stage records its outcome and command so a reviewer reconstructs
    # exactly what ran without re-running anything
    for stage_doc, stage in zip(verification["stages"], report.stages, strict=True):
        assert stage_doc["outcome"] == stage.outcome.value
        assert stage_doc["command"] == stage.command


def test_full_passing_report_persists_verified_status(
    run_ladder, fake_runner, make_goal, tmp_path
) -> None:
    from harness.telemetry.store import RunStore

    request = __import__(
        "harness.core.models", fromlist=["UserRequest"]
    ).UserRequest(objective="obj", repository_path=str(tmp_path))
    store = RunStore.start(request, runs_root=tmp_path / "runs")

    report = run_ladder(make_goal(), fake_runner(), store=store)

    verification = json.loads(
        (Path(store.run_dir) / "verification.json").read_text()
    )
    assert verification["status"] == report.status.value
    assert report.status is VerificationStatus.VERIFIED
