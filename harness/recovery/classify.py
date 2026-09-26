"""Failure classification from narrow evidence (issue #63; PRD §17; FR-10).

Rules, in priority order:

* a command that never ran or hit its timeout is an environment/timeout
  event, never a code defect;
* module-not-found for a *third-party* package is an environment failure;
* syntax/patch-conflict/test-failure patterns classify the rest;
* anything else is UNKNOWN — honestly unclassified.

The classified failure carries **focused** output: only the lines that
carry the signal (error lines and their neighbours), bounded, so recovery
context never includes unrelated history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from harness.core.errors import HarnessError

FOCUSED_LINES = 12
FOCUSED_LINE_CHARS = 240

_SYNTAX_PATTERNS = (
    re.compile(r"SyntaxError"),
    re.compile(r"IndentationError"),
    re.compile(r"TabError"),
)
_PATCH_PATTERNS = (
    re.compile(r"patch does not apply", re.IGNORECASE),
    re.compile(r"conflict", re.IGNORECASE),
    re.compile(r"hunk .* FAILED", re.IGNORECASE),
)
_TEST_PATTERNS = (
    re.compile(r"\bFAILED\b"),
    re.compile(r"\bAssertionError\b"),
    re.compile(r"\d+ failed"),
    re.compile(r"AssertionError:"),
)
_MISSING_MODULE = re.compile(
    r"(?:ModuleNotFoundError|ImportError).*No module named ['\"]?([\w.]+)"
)
_THIRD_PARTY_HINTS = ("requests", "numpy", "flask", "django", "fastapi", "pytest",
                     "sqlalchemy", "celery", "httpx", "rich", "pydantic")
_CONTEXT_PATTERNS = (
    re.compile(r"context (?:window|length) exceeded", re.IGNORECASE),
    re.compile(r"maximum context", re.IGNORECASE),
)


class FailureKind(StrEnum):
    NONE = "none"
    SYNTAX = "syntax"
    BUILD = "build"
    TEST = "test"
    PATCH = "patch"
    TOOL = "tool"
    TIMEOUT = "timeout"
    PLAN = "plan"
    CONTEXT = "context"
    LOOP = "loop"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class ClassificationError(HarnessError):
    """A failure description was too narrow to classify at all."""

    code = "classification_error"


@dataclass(frozen=True)
class ClassifiedFailure:
    """One classified failure with focused, bounded evidence."""

    kind: FailureKind
    summary: str
    focused_output: str
    is_environment: bool
    missing_module: str | None = None
    stage: str = ""
    exit_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "summary": self.summary,
            "focused_output": self.focused_output,
            "is_environment": self.is_environment,
            "missing_module": self.missing_module,
            "stage": self.stage,
            "exit_code": self.exit_code,
            "metadata": dict(self.metadata),
        }


def _focus(output: str) -> str:
    """Keep only signal-bearing lines, bounded (no unrelated history)."""
    lines = [line.strip()[:FOCUSED_LINE_CHARS] for line in output.splitlines()]
    signal = [
        line
        for line in lines
        if line and _is_signal_line(line)
    ]
    if not signal:
        signal = [line for line in lines if line][-FOCUSED_LINES:]
    return "\n".join(signal[-FOCUSED_LINES:])


def _is_signal_line(line: str) -> bool:
    lowered = line.lower()
    return any(
        marker in lowered
        for marker in (
            "error", "failed", "assert", "traceback", "exception",
            "no module named", "conflict", "timed out", "timeout",
            "warning:", "not found", "refused",
        )
    )


def classify_failure(
    *,
    command: str,
    exit_code: int | None,
    output: str = "",
    ran: bool = True,
    timed_out: bool = False,
    stage: str = "",
) -> ClassifiedFailure:
    """Classify one failed command from its narrow evidence."""
    if timed_out:
        return ClassifiedFailure(
            kind=FailureKind.TIMEOUT,
            summary=f"command timed out and was terminated: {command}",
            focused_output=_focus(output),
            is_environment=False,
            stage=stage,
            exit_code=exit_code,
            metadata={"timed_out": True},
        )
    if not ran or exit_code is None:
        return ClassifiedFailure(
            kind=FailureKind.ENVIRONMENT,
            summary=f"command could not run (missing tool or interpreter): {command}",
            focused_output=_focus(output),
            is_environment=True,
            stage=stage,
            exit_code=None,
        )
    if exit_code == 0:
        return ClassifiedFailure(
            kind=FailureKind.NONE,
            summary=f"command succeeded: {command}",
            focused_output="",
            is_environment=False,
            stage=stage,
            exit_code=0,
        )

    focused = _focus(output)
    lowered = output.lower()

    missing = _MISSING_MODULE.search(output)
    if missing is not None:
        module = missing.group(1)
        third_party = module.split(".")[0] in _THIRD_PARTY_HINTS
        return ClassifiedFailure(
            kind=FailureKind.ENVIRONMENT,
            summary=f"missing dependency module: {module}",
            focused_output=focused,
            is_environment=True,
            missing_module=module,
            stage=stage,
            exit_code=exit_code,
            metadata={"third_party": third_party},
        )

    if any(p.search(output) for p in _SYNTAX_PATTERNS):
        kind, summary = FailureKind.SYNTAX, "syntax error in changed source"
    elif any(p.search(output) for p in _PATCH_PATTERNS):
        kind, summary = FailureKind.PATCH, "patch failed to apply cleanly"
    elif any(p.search(output) for p in _TEST_PATTERNS):
        kind, summary = FailureKind.TEST, "tests failed against the change"
    elif any(p.search(output) for p in _CONTEXT_PATTERNS):
        kind, summary = FailureKind.CONTEXT, "model context budget exhausted"
    elif "no such file or directory" in lowered or "executable not found" in lowered:
        kind, summary = FailureKind.ENVIRONMENT, "required executable not found"
    elif "traceback" in lowered:
        kind, summary = FailureKind.TOOL, "runtime exception in tool execution"
    else:
        kind, summary = FailureKind.UNKNOWN, "unclassified non-zero exit"

    return ClassifiedFailure(
        kind=kind,
        summary=summary,
        focused_output=focused,
        is_environment=kind is FailureKind.ENVIRONMENT,
        stage=stage,
        exit_code=exit_code,
    )
