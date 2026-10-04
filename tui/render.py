"""LazyGit-style colored TUI renderer for live state and evidence (PRD §5.2, §5.3).

Provides a rich, vibrant, terminal UI with borders, color-coded status panels,
and structured tool event logs inspired by lazydocker/lazygit.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TextIO

from harness.core.models import SessionStatus
from harness.telemetry.models import ToolCallRecord

LINE_WIDTH = 90

# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
GRAY = "\033[90m"
BG_BLUE = "\033[44m"


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
    """LazyGit-style colored TUI renderer; safe for non-TTY output."""

    def __init__(self, stdout: TextIO | None = None) -> None:
        self._out = stdout if stdout is not None else sys.stdout
        self._is_tty = self._out.isatty()

    def _c(self, color: str, text: str) -> str:
        if not self._is_tty:
            return text
        return f"{color}{text}{RESET}"

    def _emit(self, text: str = "") -> None:
        print(text[:LINE_WIDTH], file=self._out)

    def show_state(self, state: str, symbol: str = "✓") -> None:
        state_upper = state.upper()
        if symbol == "✓" or "SUCCESS" in state_upper or "COMPLETE" in state_upper:
            sym_col = self._c(GREEN, "✓")
        elif "FAIL" in symbol or "ERROR" in state_upper:
            sym_col = self._c(RED, "✗")
        else:
            sym_col = self._c(CYAN, "⏳")

        box = self._c(GRAY, "┌─")
        title = self._c(BOLD + CYAN, f"{state:<20}")
        self._emit(f"{box} {title} {sym_col} {self._c(DIM, '───────────────────────')}")

    def show_info(self, message: str) -> None:
        prefix = self._c(BLUE, "│ ")
        self._emit(f"{prefix}{message}")

    def show_tool_event(self, record: ToolCallRecord) -> None:
        name_col = self._c(YELLOW, f"{record.name:<12}")
        args_col = self._c(DIM, record.args_summary)
        status_col = (
            self._c(GREEN, "[ok]")
            if record.status == "ok"
            else self._c(RED, f"[{record.status}]")
        )
        border = self._c(GRAY, "├─")
        self._emit(f"{border} {name_col} {args_col} {status_col}")
        if record.artifact is not None:
            art_prefix = self._c(GRAY, "│    └─ evidence:")
            path_col = self._c(CYAN, record.artifact.path)
            sha_col = self._c(DIM, f"sha256:{record.artifact.sha256[:10]}")
            self._emit(f"{art_prefix} {path_col} {sha_col}")

    def show_final(self, result: SessionResult) -> None:
        self._emit()
        top_bar = self._c(CYAN, "┌──────────────────────────────────────────────────────────┐")
        bottom_bar = self._c(CYAN, "└──────────────────────────────────────────────────────────┘")
        self._emit(top_bar)

        status_val = result.status.value.upper()
        if status_val == "VERIFIED":
            status_str = self._c(GREEN + BOLD, f"  STATUS: {status_val} ")
        elif status_val == "PARTIAL":
            status_str = self._c(YELLOW + BOLD, f"  STATUS: {status_val} ")
        else:
            status_str = self._c(RED + BOLD, f"  STATUS: {status_val} ")

        self._emit(f"{self._c(CYAN, '│')}{status_str:<60}{self._c(CYAN, '│')}")
        self._emit(self._c(CYAN, '├──────────────────────────────────────────────────────────┤'))

        if result.goals_total is not None:
            goals_str = f"  Goals Completed: {result.goals_completed}/{result.goals_total}"
            self._emit(f"{self._c(CYAN, '│')} {goals_str:<56} {self._c(CYAN, '│')}")

        files_hdr = f"  Files Changed ({len(result.files_changed)}):"
        self._emit(f"{self._c(CYAN, '│')} {files_hdr:<56} {self._c(CYAN, '│')}")
        for path in result.files_changed[:5]:
            p_str = self._c(GREEN, f"    + M {path}")
            self._emit(f"{self._c(CYAN, '│')} {p_str:<56} {self._c(CYAN, '│')}")

        for check in result.checks:
            c_str = self._c(MAGENTA, f"  Check: {check}")
            self._emit(f"{self._c(CYAN, '│')} {c_str:<56} {self._c(CYAN, '│')}")

        if result.retries is not None:
            r_str = f"  Recovery attempts: {result.retries}"
            self._emit(f"{self._c(CYAN, '│')} {r_str:<56} {self._c(CYAN, '│')}")

        for note in result.limitations:
            n_str = self._c(YELLOW, f"  Limitation: {note}")
            self._emit(f"{self._c(CYAN, '│')} {n_str:<56} {self._c(CYAN, '│')}")

        if result.report_path is not None:
            rep_str = f"  Report: {result.report_path}"
            self._emit(f"{self._c(CYAN, '│')} {rep_str:<56} {self._c(CYAN, '│')}")

        self._emit(bottom_bar)
