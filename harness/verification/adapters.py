"""Project adapter contract (the #13 slice the verification ladder needs).

Adapters map project types to the deterministic commands each verification
stage runs (PRD §19). Detection is manifest-based and prefers existing
project configuration over invented commands. Adapters return ``None`` when
a stage does not apply to the project, which the ladder records as skipped
or unavailable instead of inventing a command.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Command:
    """One runnable stage command; argv only, never a raw shell string."""

    argv: tuple[str, ...]
    label: str


class ProjectAdapter(ABC):
    """Per-project-type verification command selection."""

    name: str = "project"

    @abstractmethod
    def detect(self, root: Path) -> bool:
        """Return True when this project type matches the repository root."""

    @abstractmethod
    def syntax_command(self, root: Path, changed_files: list[str]) -> Command | None:
        """Cheapest parse/syntax check over the changed files."""

    @abstractmethod
    def build_or_typecheck_command(self, root: Path, changed_files: list[str]) -> Command | None:
        """Build/type-check command when the project configures one."""

    @abstractmethod
    def target_test_command(self, root: Path, tests: list[str]) -> Command | None:
        """Run the given targeted test node ids."""

    @abstractmethod
    def full_test_command(self, root: Path) -> Command | None:
        """Run the project's full test suite."""


class PythonAdapter(ProjectAdapter):
    name = "python"

    def __init__(self, python_executable: str = sys.executable) -> None:
        self._python = python_executable

    def detect(self, root: Path) -> bool:
        if (root / "pyproject.toml").is_file():
            return True
        if (root / "setup.py").is_file() or (root / "setup.cfg").is_file():
            return True
        return any(root.glob("requirements*.txt"))

    def syntax_command(self, root: Path, changed_files: list[str]) -> Command | None:
        py_files = sorted(f for f in changed_files if f.endswith((".py", ".pyi", ".pyw")))
        if not py_files:
            return None
        # Parse-only check: unlike compileall/py_compile this never writes
        # __pycache__ into the target repository, so verification cannot
        # poison later test runs with stale bytecode.
        checker = "import ast, sys; [ast.parse(open(p, 'rb').read(), p) for p in sys.argv[1:]]"
        return Command(
            argv=(self._python, "-c", checker, *py_files),
            label=f"ast.parse {len(py_files)} changed file(s)",
        )

    def build_or_typecheck_command(self, root: Path, changed_files: list[str]) -> Command | None:
        config = _pyproject_tool(root)
        if isinstance(config.get("mypy"), dict):
            py_files = sorted(f for f in changed_files if f.endswith(".py"))
            if py_files:
                return Command(
                    argv=("mypy", *py_files),
                    label=f"mypy {len(py_files)} changed file(s)",
                )
        return None

    def target_test_command(self, root: Path, tests: list[str]) -> Command | None:
        if not tests:
            return None
        return Command(
            argv=(self._python, "-m", "pytest", "-q", *tests),
            label=f"pytest {len(tests)} targeted test(s)",
        )

    def full_test_command(self, root: Path) -> Command | None:
        return Command(argv=(self._python, "-m", "pytest", "-q"), label="pytest full suite")


class NodeAdapter(ProjectAdapter):
    name = "node"

    def detect(self, root: Path) -> bool:
        return (root / "package.json").is_file()

    def syntax_command(self, root: Path, changed_files: list[str]) -> Command | None:
        js_files = sorted(
            f
            for f in changed_files
            if f.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"))
        )
        if not js_files:
            return None
        # node --check accepts one script at a time; loop without a shell by
        # passing files as arguments to an explicit bash invocation.
        script = 'for f in "$@"; do node --check "$f" || exit 1; done'
        return Command(
            argv=("bash", "-c", script, "node-check", *js_files),
            label=f"node --check {len(js_files)} changed file(s)",
        )

    def build_or_typecheck_command(self, root: Path, changed_files: list[str]) -> Command | None:
        scripts = _package_scripts(root)
        if scripts and "build" in scripts:
            return Command(argv=("npm", "run", "build"), label="npm run build")
        return None

    def target_test_command(self, root: Path, tests: list[str]) -> Command | None:
        scripts = _package_scripts(root)
        if not scripts or "test" not in scripts or not tests:
            return None
        return Command(
            argv=("npm", "test", "--", *tests),
            label=f"npm test -- {len(tests)} targeted test(s)",
        )

    def full_test_command(self, root: Path) -> Command | None:
        scripts = _package_scripts(root)
        if not scripts or "test" not in scripts:
            return None
        return Command(argv=("npm", "test"), label="npm test")


def select_adapter(root: Path) -> ProjectAdapter | None:
    """Deterministically pick the adapter for a repository root."""
    adapters: list[ProjectAdapter] = [PythonAdapter(), NodeAdapter()]
    for adapter in adapters:
        if adapter.detect(root):
            return adapter
    return None


def _pyproject_tool(root: Path) -> dict:
    try:
        import tomllib

        with open(root / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        tool = data.get("tool")
        return tool if isinstance(tool, dict) else {}
    except (OSError, ValueError):
        return {}


def _package_scripts(root: Path) -> dict:
    try:
        import json

        with open(root / "package.json", encoding="utf-8") as fh:
            data = json.load(fh)
        scripts = data.get("scripts")
        return scripts if isinstance(scripts, dict) else {}
    except (OSError, ValueError):
        return {}
