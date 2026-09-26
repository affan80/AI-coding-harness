"""Issue #13 slice — adapter detection and per-stage command selection."""

from __future__ import annotations

from pathlib import Path

from harness.adapters import NodeAdapter, PythonAdapter, select_adapter


def test_python_project_selects_python_adapter(python_project: Path) -> None:
    adapter = select_adapter(python_project)

    assert isinstance(adapter, PythonAdapter)
    assert adapter.detect(python_project)
    assert not NodeAdapter().detect(python_project)


def test_python_syntax_targets_changed_py_files(python_project: Path) -> None:
    adapter = PythonAdapter(python_executable="py312")

    command = adapter.syntax_command(python_project, ["app/main.py", "README.md", "app/models.py"])

    assert command is not None
    assert command.argv[0] == "py312"
    assert command.argv[1] == "-c"
    assert "ast.parse" in command.argv[2]
    assert "app/main.py" in command.argv
    assert "README.md" not in command.argv


def test_python_syntax_without_py_changes_is_none(python_project: Path) -> None:
    adapter = PythonAdapter()

    assert adapter.syntax_command(python_project, ["README.md"]) is None


def test_python_typecheck_only_when_configured(python_project: Path) -> None:
    adapter = PythonAdapter()

    assert adapter.build_or_typecheck_command(python_project, ["app/main.py"]) is None

    (python_project / "pyproject.toml").write_text(
        "[project]\nname='sample'\n[tool.mypy]\nstrict=true\n"
    )
    command = adapter.build_or_typecheck_command(python_project, ["app/main.py", "README.md"])
    assert command is not None
    assert command.argv == ("mypy", "app/main.py")


def test_python_test_commands(python_project: Path) -> None:
    adapter = PythonAdapter(python_executable="py312")

    targeted = adapter.target_test_command(python_project, ["tests/test_main.py::test_ok"])
    full = adapter.full_test_command(python_project)

    assert targeted is not None and targeted.argv == (
        "py312", "-m", "pytest", "-q", "tests/test_main.py::test_ok",
    )
    assert full is not None and full.argv == ("py312", "-m", "pytest", "-q")
    assert adapter.target_test_command(python_project, []) is None


def test_node_project_selects_node_adapter(node_project: Path) -> None:
    adapter = select_adapter(node_project)

    assert isinstance(adapter, NodeAdapter)
    assert adapter.detect(node_project)
    assert not PythonAdapter().detect(node_project)


def test_node_commands_come_from_package_scripts(node_project: Path) -> None:
    adapter = NodeAdapter()

    build = adapter.build_or_typecheck_command(node_project, ["src/index.ts"])
    targeted = adapter.target_test_command(node_project, ["src/index.test.ts"])
    full = adapter.full_test_command(node_project)

    assert build is not None and build.argv == ("npm", "run", "build")
    assert targeted is not None and targeted.argv == ("npm", "test", "--", "src/index.test.ts")
    assert full is not None and full.argv == ("npm", "test")


def test_node_syntax_checks_each_changed_file(node_project: Path) -> None:
    adapter = NodeAdapter()

    command = adapter.syntax_command(node_project, ["src/index.ts", "src/util.ts", "notes.txt"])

    assert command is not None
    assert command.argv[0:3] == ("bash", "-c", command.argv[2])
    assert "node --check" in command.argv[2]
    assert "src/index.ts" in command.argv
    assert "notes.txt" not in command.argv
    assert adapter.syntax_command(node_project, ["notes.txt"]) is None


def test_node_without_scripts_yields_no_commands(tmp_path: Path, make_file_fixture) -> None:
    root = tmp_path / "nodeless"
    root.mkdir()
    make_file_fixture(root, "package.json", '{"name": "bare"}')

    adapter = NodeAdapter()

    assert adapter.detect(root)
    assert adapter.build_or_typecheck_command(root, ["a.js"]) is None
    assert adapter.target_test_command(root, ["a.test.js"]) is None
    assert adapter.full_test_command(root) is None


def test_unrecognized_project_selects_no_adapter(tmp_path: Path, make_file_fixture) -> None:
    (tmp_path / "src").mkdir()
    make_file_fixture(tmp_path, "src/main.rs", "fn main() {}\n")

    assert select_adapter(tmp_path) is None
