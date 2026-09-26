"""Controlled command execution (the #12 slice the verification ladder needs).

Commands run without a shell (argv lists only), with a hard timeout and an
output cap so a runaway command cannot flood memory. Missing executables map
to an unavailable result rather than an exception, letting the ladder
distinguish failed checks from un-runnable ones.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Conventional exit status for "killed by timeout".
TIMEOUT_EXIT_CODE = 124

# Upper bound on captured output per stream; larger output is truncated here
# and full raw output belongs in the evidence store's artifacts.
MAX_OUTPUT_CHARS = 200_000


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one controlled command execution."""

    argv: tuple[str, ...]
    exit_code: int | None  # None when the executable could not be started
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    unavailable_reason: str | None = None

    @property
    def ran(self) -> bool:
        return self.exit_code is not None

    @property
    def combined_output(self) -> str:
        return self.stdout + self.stderr


def run_command(
    command: str | Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    max_output_chars: int = MAX_OUTPUT_CHARS,
) -> CommandResult:
    """Execute one command under the controlled policy."""
    argv = tuple(shlex.split(command)) if isinstance(command, str) else tuple(command)
    if not argv:
        raise ValueError("command must not be empty")
    started = time.monotonic()
    try:
        # Bytecode writes are disabled so verification commands never leave
        # stale __pycache__ state in the target repository (a same-second
        # source edit would otherwise keep serving the old bytecode).
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            argv=argv,
            exit_code=TIMEOUT_EXIT_CODE,
            stdout="",
            stderr=f"command exceeded timeout of {timeout_seconds}s and was killed",
            duration_ms=duration_ms,
            timed_out=True,
        )
    except FileNotFoundError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            argv=argv,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=duration_ms,
            timed_out=False,
            unavailable_reason=f"executable not found: {exc.filename or argv[0]}",
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            argv=argv,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=duration_ms,
            timed_out=False,
            unavailable_reason=f"could not start command: {exc}",
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        argv=argv,
        exit_code=completed.returncode,
        stdout=_cap(completed.stdout or "", max_output_chars),
        stderr=_cap(completed.stderr or "", max_output_chars),
        duration_ms=duration_ms,
        timed_out=False,
    )


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"
