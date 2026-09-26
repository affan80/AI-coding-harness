"""Issue #67 — reproduction attempts and preserved rejection evidence."""

from __future__ import annotations

from pathlib import Path

from harness.audit import apply_reproduction, attempt_reproduction
from harness.audit.models import Finding, FindingStatus
from harness.execution.runner import CommandResult


def _finding(reproducer: str | None = 'python -c "import app.auth; assert False"') -> Finding:
    return Finding(
        finding_id="F7",
        title="login accepts empty password",
        description="empty password bypasses the credential check",
        source="llm_review",
        scope_path="app/auth.py",
        proposed_reproducer=reproducer,
    )


def _runner(exit_code: int | None = 1, timed_out: bool = False, output: str = "AssertionError"):
    def _run(argv, cwd, timeout_seconds):
        return CommandResult(
            argv=argv,
            exit_code=None if exit_code is None else (124 if timed_out else exit_code),
            stdout="" if exit_code else "",
            stderr=output if (exit_code or timed_out) else "",
            duration_ms=9,
            timed_out=timed_out,
            unavailable_reason=None if exit_code is not None else "executable not found: python",
        )

    return _run


def test_failing_reproducer_reproduces_the_defect(tmp_path: Path) -> None:
    finding = _finding()

    result = attempt_reproduction(finding, tmp_path, runner=_runner(exit_code=1))
    updated = apply_reproduction(finding, result)

    assert result.reproduced is True
    assert result.exit_code == 1
    assert updated.status is FindingStatus.REPRODUCED
    evidence = updated.evidence[-1]
    assert evidence.kind == "reproducer"
    assert evidence.metadata["exit_code"] == 1


def test_passing_reproducer_rejects_with_preserved_attempt(tmp_path: Path) -> None:
    finding = _finding()

    result = attempt_reproduction(finding, tmp_path, runner=_runner(exit_code=0))
    updated = apply_reproduction(finding, result)

    assert result.attempted is True
    assert result.reproduced is False
    assert "did not reproduce" in result.reason
    assert updated.status is FindingStatus.REJECTED
    assert updated.rejection_reason == result.reason
    # The failed attempt is preserved as evidence, not discarded.
    assert updated.evidence[-1].kind == "reproduction_attempt"
    assert updated.evidence[-1].metadata["exit_code"] == 0


def test_missing_reproducer_rejects_without_an_attempt(tmp_path: Path) -> None:
    finding = _finding(reproducer=None)

    result = attempt_reproduction(finding, tmp_path, runner=_runner())
    updated = apply_reproduction(finding, result)

    assert result.attempted is False
    assert updated.status is FindingStatus.REJECTED
    assert "no reproducer proposed" in updated.rejection_reason
    assert updated.evidence == ()


def test_unrunnable_reproducer_rejects_with_environment_reason(tmp_path: Path) -> None:
    finding = _finding()

    result = attempt_reproduction(finding, tmp_path, runner=_runner(exit_code=None))
    updated = apply_reproduction(finding, result)

    assert result.reproduced is False
    assert "could not run" in result.reason
    assert updated.status is FindingStatus.REJECTED
    assert "could not run" in updated.rejection_reason


def test_timed_out_reproducer_rejects(tmp_path: Path) -> None:
    finding = _finding()

    result = attempt_reproduction(finding, tmp_path, runner=_runner(timed_out=True))
    updated = apply_reproduction(finding, result)

    assert result.reproduced is False
    assert "timed out" in result.reason
    assert updated.status is FindingStatus.REJECTED


def test_large_reproducer_output_becomes_artifact(tmp_path: Path) -> None:
    from harness.core.models import UserRequest
    from harness.telemetry import RunStore

    store = RunStore.start(
        UserRequest(repository_path=str(tmp_path), objective="audit"),
        runs_root=tmp_path / "runs",
    )
    finding = _finding()
    big = "E" * 9000

    def _run(argv, cwd, timeout_seconds):
        return CommandResult(
            argv=argv, exit_code=1, stdout="", stderr=big, duration_ms=5, timed_out=False
        )

    result = attempt_reproduction(finding, tmp_path, runner=_run, store=store)

    assert result.artifact is not None
    artifact_path = store.run_dir / result.artifact.path
    assert len(artifact_path.read_bytes()) == 9000
    # Output stays in the artifact, only the tail rides on the result.
    assert big not in result.output_tail
    assert result.output_tail.endswith("E" * 10)
