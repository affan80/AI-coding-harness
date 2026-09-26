"""Controlled command execution with policy, timeout, and output caps (#54).

Distinct structured outcomes: ok / nonzero_exit / timeout / denied /
spawn_error, with the full output kept as an on-disk artifact whenever it is
truncated, so model context only ever sees a bounded summary. Commands run
under the policy's environment: sanitized by default, so harness secrets do
not leak into model-suggested processes.
"""

from __future__ import annotations

import shlex
import subprocess
import time
import uuid
from pathlib import Path

from .policy import CommandPolicy
from .result import (
    DENIED,
    NONZERO_EXIT,
    SPAWN_ERROR,
    TIMEOUT,
    ToolResult,
)


def run_command(
    command: str,
    *,
    policy: CommandPolicy,
    working_dir: Path,
    timeout_seconds: float | None = None,
    artifacts_dir: Path | None = None,
) -> ToolResult:
    """Run an allow-listed, non-destructive command and report its outcome."""
    allowed, reason = policy.check(command)
    if not allowed:
        return ToolResult.failure(DENIED, f"denied: {reason}")

    if (
        timeout_seconds is not None
        and timeout_seconds > policy.max_timeout_seconds
    ):
        return ToolResult.failure(
            DENIED,
            f"denied: requested timeout {timeout_seconds}s exceeds the "
            f"policy maximum of {policy.max_timeout_seconds}s",
        )

    timeout = timeout_seconds or policy.default_timeout_seconds
    started = time.monotonic()
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return ToolResult.failure(SPAWN_ERROR, f"unparsable command: {exc}")

    try:
        completed = subprocess.run(
            argv,
            cwd=str(working_dir),
            env=policy.build_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - started) * 1000)
        return ToolResult(
            ok=False,
            summary=f"command timed out after {timeout}s and was terminated: "
            f"{command}",
            error_kind=TIMEOUT,
            duration_ms=duration_ms,
        )
    except FileNotFoundError:
        return ToolResult.failure(
            SPAWN_ERROR, f"executable not found: {argv[0]}"
        )
    except PermissionError:
        return ToolResult.failure(
            SPAWN_ERROR, f"executable is not runnable: {argv[0]}"
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    output = completed.stdout + completed.stderr
    truncated = len(output.encode("utf-8")) > policy.max_output_bytes
    artifacts: list[str] = []
    if truncated:
        artifacts_dir = artifacts_dir or Path("/tmp")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifacts_dir / f"cmd-{uuid.uuid4().hex[:8]}.log"
        artifact.write_text(output, encoding="utf-8")
        artifacts.append(str(artifact))

    if completed.returncode == 0:
        return ToolResult(
            ok=True,
            summary=_concise(command, output, exit=0, truncated=truncated),
            data={"exit_code": 0, "command": command},
            artifacts=artifacts,
            truncated=truncated,
            duration_ms=duration_ms,
        )
    return ToolResult(
        ok=False,
        summary=_concise(
            command, output, exit=completed.returncode, truncated=truncated
        ),
        error_kind=NONZERO_EXIT,
        data={"exit_code": completed.returncode, "command": command},
        artifacts=artifacts,
        truncated=truncated,
        duration_ms=duration_ms,
    )


def _concise(command: str, output: str, *, exit: int, truncated: bool) -> str:
    tail = " …(full output in artifact)" if truncated else ""
    head = " ".join(output.strip().split())[:300]
    return f"`{command}` exited {exit}: {head}{tail}"
