"""Issue #60 — real controlled command execution behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.execution.runner import TIMEOUT_EXIT_CODE, run_command


def test_successful_command_captures_output(tmp_path: Path) -> None:
    result = run_command(["sh", "-c", "echo out-line; echo err-line >&2"], tmp_path, 10)

    assert result.ran
    assert result.exit_code == 0
    assert "out-line" in result.stdout
    assert "err-line" in result.stderr
    assert not result.timed_out
    assert result.duration_ms >= 0


def test_failing_command_reports_exit_code(tmp_path: Path) -> None:
    result = run_command(["sh", "-c", "exit 3"], tmp_path, 10)

    assert result.exit_code == 3


def test_string_commands_are_split_without_a_shell(tmp_path: Path) -> None:
    result = run_command("echo hello-world", tmp_path, 10)

    assert result.argv == ("echo", "hello-world")
    assert "hello-world" in result.stdout


def test_timeout_kills_the_command(tmp_path: Path) -> None:
    result = run_command(["sleep", "5"], tmp_path, 0.2)

    assert result.timed_out
    assert result.exit_code == TIMEOUT_EXIT_CODE
    assert "timeout" in result.stderr
    assert result.duration_ms < 5000


def test_missing_executable_is_unavailable_not_failed(tmp_path: Path) -> None:
    result = run_command(["definitely-not-a-real-binary-xyz"], tmp_path, 10)

    assert result.exit_code is None
    assert not result.ran
    assert result.unavailable_reason is not None
    assert "not found" in result.unavailable_reason


def test_output_is_capped(tmp_path: Path) -> None:
    result = run_command(
        ["sh", "-c", "yes x | head -c 300000"], tmp_path, 30, max_output_chars=1000
    )

    assert len(result.stdout) < 1200
    assert "truncated" in result.stdout


def test_empty_command_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        run_command([], tmp_path, 10)


def test_runner_never_writes_bytecode_caches(tmp_path: Path) -> None:
    import sys

    (tmp_path / "mod.py").write_text("value = 1\n")

    result = run_command(
        [sys.executable, "-c", "import mod; assert mod.value == 1"], tmp_path, 10
    )

    assert result.exit_code == 0
    assert not (tmp_path / "__pycache__").exists()
