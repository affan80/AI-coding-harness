"""Live state and evidence rendering (PRD §5.2, §5.3).

The renderer shows concise state transitions and tool events only — never
raw chain-of-thought or unbounded command output. Every tool event line maps
to stored evidence via the tool-call id / artifact reference.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TextIO

from harness.core.models import SessionStatus
from harness.telemetry.models import ToolCallRecord

LINE_WIDTH = 100


@dataclass
class SessionResult:
    """What the user sees summarized at the end of a session."""

    status: SessionStatus
    goals_completed: int | None = None
    goals_total: int | None = None
    files_changed: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    retries: int | None = None
    limitations: list[str] = field(default_factory=list)
    report_path: str | None = None


class Renderer:
    """Plain-text rendering of session state; safe for non-TTY output."""

    def __init__(self, stdout: TextIO | None = None) -> None:
        self._out = stdout if stdout is not None else sys.stdout

    def _emit(self, text: str = "") -> None:
        print(text[:LINE_WIDTH], file=self._out)

    def show_state(self, state: str, symbol: str = "✓") -> None:
        self._emit(f"{state:<12} {symbol}")

    def show_info(self, message: str) -> None:
        self._emit(message)

    def show_tool_event(self, record: ToolCallRecord) -> None:
        line = f"{record.name:<10}{record.args_summary}"
        if record.status != "ok":
            line += f" [{record.status}]"
        self._emit(line)
        if record.artifact is not None:
            self._emit(
                f"          evidence: {record.artifact.path} "
                f"sha256:{record.artifact.sha256[:12]}"
            )

    def show_final(self, result: SessionResult) -> None:
        self._emit()
        self._emit(f"Status: {result.status.value.upper()}")
        if result.goals_total is not None:
            self._emit(f"Goals: {result.goals_completed}/{result.goals_total}")
        self._emit(f"Files changed: {len(result.files_changed)}")
        for path in result.files_changed:
            self._emit(f"  M {path}")
        for check in result.checks:
            self._emit(f"Check: {check}")
        if result.retries is not None:
            self._emit(f"Recovery attempts: {result.retries}")
        for note in result.limitations:
            self._emit(f"Limitation: {note}")
        if result.report_path is not None:
            self._emit(f"Report: {result.report_path}")
