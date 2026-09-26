"""Shared fixtures for verification-ladder tests: scripted runner, goal factory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from harness.adapters import PythonAdapter
from harness.execution.runner import CommandResult
from harness.verification import GoalKind, VerificationInput, run_verification_ladder

DIFF = """diff --git a/app/math.py b/app/math.py
--- a/app/math.py
+++ b/app/math.py
@@ -1 +1 @@
-def add(a, b):
+def add(a, b):
+    return a + b
"""


class FakeRunner:
    """Scripted runner keyed by a substring of the joined argv."""

    def __init__(
        self,
        results: dict[str, tuple[int | None, str]] | None = None,
        default_exit: int = 0,
        timeouts: tuple[str, ...] = (),
    ) -> None:
        self.results = results or {}
        self.default_exit = default_exit
        self.timeouts = timeouts
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...], cwd: Path, timeout_seconds: float) -> CommandResult:
        self.calls.append(argv)
        joined = " ".join(argv)
        if any(needle in joined for needle in self.timeouts):
            return CommandResult(
                argv=argv,
                exit_code=124,
                stdout="",
                stderr=f"command exceeded timeout of {timeout_seconds}s and was killed",
                duration_ms=int(timeout_seconds * 1000),
                timed_out=True,
            )
        for needle, (code, output) in self.results.items():
            if needle in joined:
                if code is None:
                    return CommandResult(
                        argv=argv, exit_code=None, stdout="", stderr="",
                        duration_ms=1, timed_out=False,
                        unavailable_reason=output or "executable not found: missing",
                    )
                if code == 0:
                    return CommandResult(
                        argv=argv, exit_code=0, stdout=output, stderr="",
                        duration_ms=7, timed_out=False,
                    )
                return CommandResult(
                    argv=argv, exit_code=code, stdout="", stderr=output,
                    duration_ms=7, timed_out=False,
                )
        return CommandResult(
            argv=argv, exit_code=self.default_exit, stdout="ok\n", stderr="",
            duration_ms=7, timed_out=False,
        )


@pytest.fixture
def fake_runner() -> Callable[..., FakeRunner]:
    def _make(
        results: dict[str, tuple[int | None, str]] | None = None,
        default_exit: int = 0,
        timeouts: tuple[str, ...] = (),
    ) -> FakeRunner:
        return FakeRunner(results=results, default_exit=default_exit, timeouts=timeouts)

    return _make


@pytest.fixture
def python_adapter() -> PythonAdapter:
    return PythonAdapter(python_executable="py")


@pytest.fixture
def make_goal() -> Callable[..., VerificationInput]:
    def _make(**overrides) -> VerificationInput:
        defaults: dict = {
            "goal_id": "G1",
            "goal_kind": GoalKind.BUG_FIX,
            "reproducer_command": "python repro.py",
            "targeted_tests": ("tests/test_math.py::test_add",),
            "related_tests": ("tests/test_math.py::test_sub",),
            "diff_text": DIFF,
            "allowed_scope": (),
        }
        defaults.update(overrides)
        return VerificationInput(**defaults)

    return _make


@pytest.fixture
def run_ladder(tmp_path: Path, python_adapter: PythonAdapter) -> Callable[..., object]:
    def _run(goal: VerificationInput, runner: FakeRunner, store: object | None = None):
        return run_verification_ladder(
            goal, tmp_path, runner=runner, adapter=python_adapter, store=store  # type: ignore[arg-type]
        )

    return _run
