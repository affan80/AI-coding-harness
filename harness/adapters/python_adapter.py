"""Python project adapter (#58).

Commands are discovered from real project configuration — pyproject.toml tool
sections, requirements files — and never invented. A Python project without
pytest configuration still gets the standard pytest invocation because pytest
is the PRD-specified Python MVP runner; anything not configured (lint,
type-check, build) returns an empty list instead of a guessed command.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .base import AdapterCommand, CommandKind, DetectionResult, EnvironmentStatus


@dataclass
class PythonConfig:
    has_pyproject: bool = False
    has_requirements: bool = False
    has_setup: bool = False
    pytest_configured: bool = False
    ruff_configured: bool = False
    mypy_configured: bool = False
    package_installable: bool = False


def read_python_config(root: Path) -> PythonConfig:
    config = PythonConfig()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        config.has_pyproject = True
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            data = {}
        tools = data.get("tool", {})
        config.pytest_configured = "pytest" in tools
        config.ruff_configured = "ruff" in tools
        config.mypy_configured = "mypy" in tools
        config.package_installable = "project" in data or "build-system" in data
    config.has_requirements = any(
        (root / name).is_file()
        for name in ("requirements.txt", "requirements-dev.txt",
                     "requirements_dev.txt")
    )
    config.has_setup = (root / "setup.py").is_file() or (
        root / "setup.cfg"
    ).is_file()
    return config


class PythonAdapter:
    name = "python"

    def detect(self, root: Path) -> DetectionResult:
        config = read_python_config(root)
        if config.has_pyproject or config.has_requirements or config.has_setup:
            return DetectionResult(supported=True, adapter_name=self.name)
        return DetectionResult.unsupported(
            "no pyproject.toml, requirements file, or setup.py/setup.cfg"
        )

    def setup_commands(self, root: Path) -> list[AdapterCommand]:
        config = read_python_config(root)
        if config.package_installable:
            return [
                AdapterCommand(
                    kind=CommandKind.SETUP,
                    argv=["python", "-m", "pip", "install", "-e", "."],
                    description="editable install from pyproject",
                    requires="python",
                )
            ]
        if config.has_requirements:
            return [
                AdapterCommand(
                    kind=CommandKind.SETUP,
                    argv=["python", "-m", "pip", "install",
                          "-r", "requirements.txt"],
                    description="install pinned requirements",
                    requires="python",
                )
            ]
        return []

    def build_commands(self, root: Path) -> list[AdapterCommand]:
        # Python MVP: syntax compilation is the build gate; nothing invented.
        return [
            AdapterCommand(
                kind=CommandKind.BUILD,
                argv=["python", "-m", "compileall", "-q", "."],
                description="byte-compile all sources",
                requires="python",
            )
        ]

    def lint_commands(self, root: Path) -> list[AdapterCommand]:
        if read_python_config(root).ruff_configured:
            return [
                AdapterCommand(
                    kind=CommandKind.LINT,
                    argv=["python", "-m", "ruff", "check", "."],
                    description="ruff configured in pyproject.toml",
                    requires="ruff",
                )
            ]
        return []

    def typecheck_commands(self, root: Path) -> list[AdapterCommand]:
        if read_python_config(root).mypy_configured:
            return [
                AdapterCommand(
                    kind=CommandKind.TYPECHECK,
                    argv=["python", "-m", "mypy", "."],
                    description="mypy configured in pyproject.toml",
                    requires="mypy",
                )
            ]
        return []

    def target_test_command(
        self, root: Path, tests: list[str]
    ) -> AdapterCommand | None:
        if not tests:
            return None
        return AdapterCommand(
            kind=CommandKind.TARGET_TEST,
            argv=["python", "-m", "pytest", "-q", *tests],
            description="targeted pytest run",
            requires="pytest",
        )

    def full_test_command(self, root: Path) -> AdapterCommand | None:
        return AdapterCommand(
            kind=CommandKind.FULL_TEST,
            argv=["python", "-m", "pytest", "-q"],
            description="full pytest suite",
            requires="pytest",
        )

    def check_environment(self, root: Path) -> EnvironmentStatus:
        required = ["python", "pytest"]
        for command in (
            self.lint_commands(root) + self.typecheck_commands(root)
        ):
            if command.requires:
                required.append(command.requires)
        missing = [binary for binary in required if shutil.which(binary) is None]
        return EnvironmentStatus(ok=not missing, missing=missing)
