"""Issue #29 — final status rendering and reliable process exit codes."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from harness.core.models import UserRequest
from harness.telemetry.models import RunStatus
from tui.cli import ExitCode, main
from tui.render import SessionResult
from tui.session_runner import run_session


@pytest.mark.parametrize(
    "status, expected",
    [
        (RunStatus.VERIFIED, 0),
        (RunStatus.PARTIAL, 1),
        (RunStatus.FAILED, 2),
        (RunStatus.CANCELLED, 3),
    ],
)
def test_status_to_exit_code_mapping(status, expected) -> None:
    from tui.cli import exit_code_for

    assert exit_code_for(status) == expected


def test_scripted_session_against_local_repository(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "requirements.txt").write_text("flask==2.3.0\n")
    runs_dir = tmp_path / "runs"
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path), "Fix the login bug", "--runs-dir", str(runs_dir),
         "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == int(ExitCode.PARTIAL)
    lines = out.getvalue().splitlines()
    assert "Status: PARTIAL" in lines
    assert any(line.startswith("Report: ") for line in lines)
    assert any(line.startswith("Limitation: ") for line in lines)
    assert err.getvalue() == ""

    run_dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    session = json.loads((run_dir / "session.json").read_text())
    assert session["status"] == "partial"
    profile = json.loads((run_dir / "repository.json").read_text())
    assert profile["total_files"] == 2
    assert (run_dir / "events.jsonl").exists()
    assert json.loads((run_dir / "verification.json").read_text())["status"] == "not_run"


def test_usage_error_exit_code_and_message(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path / "missing"), "objective", "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == int(ExitCode.USAGE_ERROR)
    assert "error:" in err.getvalue()


def test_non_interactive_requires_objective(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path), "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == int(ExitCode.USAGE_ERROR)
    assert "objective is required" in err.getvalue()


def test_unknown_flag_is_usage_error(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path), "obj", "--mode", "audit", "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == int(ExitCode.USAGE_ERROR)
    # The user never selects a mode; --mode is not a real flag.
    assert "--mode" in err.getvalue()


def test_session_failure_maps_to_non_zero_exit(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "app.py").write_text("x\n")
    runs_dir = tmp_path / "runs"

    def broken_profiler(repository: str):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr("tui.session_runner.profile_repository", broken_profiler)
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path), "objective", "--runs-dir", str(runs_dir), "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == int(ExitCode.FAILED)
    assert "Status: FAILED" in out.getvalue()
    run_dir = next(p for p in runs_dir.iterdir() if p.is_dir())
    session = json.loads((run_dir / "session.json").read_text())
    assert session["status"] == "failed"
    assert "disk exploded" in session["stop_reason"]


@pytest.mark.parametrize(
    "status, expected",
    [
        (RunStatus.VERIFIED, int(ExitCode.VERIFIED)),
        (RunStatus.CANCELLED, int(ExitCode.CANCELLED)),
    ],
)
def test_runner_result_status_drives_exit_code(
    tmp_path: Path, monkeypatch, status, expected
) -> None:
    (tmp_path / "app.py").write_text("x\n")

    def fake_run_session(request, budget=None, runs_root="runs", renderer=None):
        return SessionResult(status=status, report_path=str(runs_root))

    monkeypatch.setattr("tui.cli.run_session", fake_run_session)
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path), "objective", "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == expected
    assert f"Status: {status.value.upper()}" in out.getvalue()


def test_keyboard_interrupt_outside_runner_maps_to_cancelled(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "app.py").write_text("x\n")

    def interrupted_session(request, budget=None, runs_root="runs", renderer=None):
        raise KeyboardInterrupt

    monkeypatch.setattr("tui.cli.run_session", interrupted_session)
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [str(tmp_path), "objective", "--non-interactive"],
        stdout=out,
        stderr=err,
    )

    assert code == int(ExitCode.CANCELLED)


def test_runner_itself_finalizes_cancelled_on_interrupt(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"

    class InterruptingProfiler:
        def __call__(self, repository: str):
            raise KeyboardInterrupt

    result = run_session(
        UserRequest(repository_path=str(tmp_path), objective="obj"),
        runs_root=runs_dir,
        profiler=InterruptingProfiler(),
    )

    assert result.status == RunStatus.CANCELLED
    assert result.report_path is not None
    session = json.loads((Path(result.report_path) / "session.json").read_text())
    assert session["status"] == "cancelled"
