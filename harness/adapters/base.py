"""Adapter contract: detection + command discovery without execution (#57).

Adapters only *return* commands; running them is the executor/verification
layer's job. Missing tools are reported as structured environment failures,
never silently skipped or guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class CommandKind(StrEnum):
    SETUP = "setup"
    BUILD = "build"
    LINT = "lint"
    TYPECHECK = "typecheck"
    TARGET_TEST = "target_test"
    FULL_TEST = "full_test"


@dataclass
class AdapterCommand:
    """One discovered command; ``argv`` is executed verbatim, never edited."""

    kind: CommandKind
    argv: list[str]
    description: str = ""
    requires: str = ""  # binary that must exist on PATH


@dataclass
class EnvironmentStatus:
    """Structured result of checking required binaries on PATH (issue #57)."""

    ok: bool
    missing: list[str] = field(default_factory=list)


@dataclass
class DetectionResult:
    """What detection concluded about a repository."""

    supported: bool
    adapter_name: str = ""
    reason: str = ""  # for unsupported projects: why

    @classmethod
    def unsupported(cls, reason: str) -> DetectionResult:
        return cls(supported=False, reason=reason)


UnsupportedProject = DetectionResult


class ProjectAdapter(Protocol):
    """Minimum contract verification (issue #14) consumes."""

    name: str

    def detect(self, root: Path) -> DetectionResult:
        """Return whether this adapter handles the repository at root."""
        ...  # pragma: no cover

    def setup_commands(self, root: Path) -> list[AdapterCommand]:
        ...  # pragma: no cover

    def build_commands(self, root: Path) -> list[AdapterCommand]:
        ...  # pragma: no cover

    def lint_commands(self, root: Path) -> list[AdapterCommand]:
        ...  # pragma: no cover

    def typecheck_commands(self, root: Path) -> list[AdapterCommand]:
        ...  # pragma: no cover

    def target_test_command(self, root: Path, tests: list[str]) -> AdapterCommand | None:
        ...  # pragma: no cover

    def full_test_command(self, root: Path) -> AdapterCommand | None:
        ...  # pragma: no cover

    def check_environment(self, root: Path) -> EnvironmentStatus:
        """Report missing binaries as a structured environment failure."""
        ...  # pragma: no cover
