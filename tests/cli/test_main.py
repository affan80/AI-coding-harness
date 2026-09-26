"""Issue #29: scripted runs determine the outcome from stdout and exit status."""

import json
from pathlib import Path

import pytest

from harness.cli.input import build_parser
from harness.cli.main import (
    ExitCode,
    SessionSummary,
    main,
    run_session,
)
from harness.telemetry.report import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_VERIFIED,
    CheckResult,
)


def _args(tmp_path, extra: list[str] | None = None):
    argv = [str(tmp_path), "demo objective", "--runs-root",
            str(tmp_path / "runs")]
    return build_parser().parse_args(argv + (extra or []))


def _run(tmp_path, runner, extra: list[str] | None = None):
    lines: list[str] = []
    code = run_session(
        _args(tmp_path, extra), runner, prompt=lambda _label: "", out=lines.append
    )
    return code, "\n".join(lines)


def _summary(status: str) -> SessionSummary:
    return SessionSummary(
        status=status,
        goals_completed=1 if status == STATUS_VERIFIED else 0,
        goals_total=1,
        changed_files=["app.py"] if status != "CANCELLED" else [],
        checks=[CheckResult(name="suite", command="pytest -q", passed=True,
                            exit_code=0, output_summary="1 passed")],
        retries=1,
        limitations=["fixture scope"],
        detail={"CANCELLED": "interrupted by user"}.get(status, ""),
    )


@pytest.mark.parametrize("status,expected", [
    (STATUS_VERIFIED, ExitCode.VERIFIED),
    (STATUS_PARTIAL, ExitCode.PARTIAL),
    (STATUS_FAILED, ExitCode.FAILED),
    ("CANCELLED", ExitCode.CANCELLED),
])
def test_every_terminal_status_has_a_distinct_exit_code(tmp_path, status, expected):
    code, output = _run(tmp_path, lambda *a: _summary(status))
    assert code == expected.value
    # scripted consumers can parse the outcome from stdout alone
    assert f"Status: {status}" in output


def test_final_output_lists_goals_files_checks_retries_report(tmp_path):
    code, output = _run(tmp_path, lambda *a: _summary(STATUS_VERIFIED))
    assert code == 0
    assert "Goals: 1/1" in output
    assert "Files changed: 1" in output
    assert "Checks: 1/1" in output
    assert "Recovery attempts: 1" in output
    report_line = next(line for line in output.splitlines()
                       if line.startswith("Report: "))
    report_path = Path(report_line.split("Report: ")[1])
    assert report_path.is_file()
    report = report_path.read_text(encoding="utf-8")
    assert "**Status: VERIFIED**" in report
    assert "`app.py`" in report


def test_keyboard_interrupt_maps_to_cancelled(tmp_path):
    def interrupted(*_a):
        raise KeyboardInterrupt

    code, output = _run(tmp_path, interrupted)
    assert code == ExitCode.CANCELLED.value
    assert "Status: CANCELLED" in output
    assert "interrupted by user" in output


def test_verification_and_changed_files_land_in_the_run_dir(tmp_path):
    code, _ = _run(tmp_path, lambda *a: _summary(STATUS_VERIFIED))
    assert code == 0
    # runs/ also holds the auto-written .gitignore file; pick the session dir.
    run_dir = next(p for p in (tmp_path / "runs").iterdir() if p.is_dir())
    verification = json.loads(
        (run_dir / "verification.json").read_text(encoding="utf-8")
    )
    assert verification["status"] == "pass"
    assert verification["checks"][0]["name"] == "suite"
    changed = json.loads((run_dir / "changed-files.json").read_text("utf-8"))
    assert changed["files"] == ["app.py"]


def test_main_self_check_exits_zero_and_is_scriptable(tmp_path):
    code = main([
        str(tmp_path), "self check", "--self-check",
        "--runs-root", str(tmp_path / "runs"),
    ])
    assert code == ExitCode.VERIFIED.value


def test_default_runner_is_honest_about_no_engine(tmp_path):
    # without --self-check (or a wired engine) the session fails honestly
    code = main([str(tmp_path), "obj", "--runs-root", str(tmp_path / "r2")])
    assert code == ExitCode.FAILED.value
