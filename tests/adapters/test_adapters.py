"""Issue #57-#59: adapter detection, command contracts, Python and Node."""

import json
import subprocess

from harness.adapters.base import CommandKind
from harness.adapters.detect import detect_adapter
from harness.adapters.node_adapter import NodeAdapter
from harness.adapters.python_adapter import PythonAdapter

# ---------------------------------------------------------------------------
# Issue #57 — contract: commands returned, never executed; structured results
# ---------------------------------------------------------------------------


def test_detect_is_deterministic_for_python_node_and_unsupported(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "package.json").write_text("{}")
    (tmp_path / "c").mkdir()  # no manifests at all

    adapter, result = detect_adapter(tmp_path / "a")
    assert adapter is not None and adapter.name == "python" and result.supported
    adapter, result = detect_adapter(tmp_path / "b")
    assert adapter is not None and adapter.name == "node" and result.supported
    # deterministic: same input, same answer
    assert detect_adapter(tmp_path / "b")[0].name == "node"
    adapter, result = detect_adapter(tmp_path / "c")
    assert adapter is None
    assert not result.supported
    assert "manifest" in result.reason


def test_adapters_return_commands_without_executing_them(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.pytest.ini_options]\n"
    )
    adapter = PythonAdapter()
    target = adapter.target_test_command(tmp_path, ["tests/test_x.py"])
    assert target.argv == ["python", "-m", "pytest", "-q", "tests/test_x.py"]
    assert target.requires == "pytest"
    # nothing was executed: the project directory only holds the manifest
    assert list(tmp_path.iterdir()) == [tmp_path / "pyproject.toml"]


# ---------------------------------------------------------------------------
# Issue #58 — Python adapter
# ---------------------------------------------------------------------------


def _python_project(tmp_path, extra_pyproject: str = ""):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n" + extra_pyproject
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text("def test_ok():\n    assert 1\n")
    return tmp_path


def test_python_adapter_detects_manifests_and_builds_commands(tmp_path):
    adapter = PythonAdapter()
    root = _python_project(tmp_path)

    assert adapter.detect(root).supported
    assert adapter.detect(root).adapter_name == "python"
    full = adapter.full_test_command(root)
    target = adapter.target_test_command(root, ["tests/test_demo.py"])
    # targeted and full are distinct where the project supports it
    assert full.argv == ["python", "-m", "pytest", "-q"]
    assert target.argv != full.argv
    assert "tests/test_demo.py" in target.argv
    build = adapter.build_commands(root)
    assert build[0].kind is CommandKind.BUILD
    assert adapter.setup_commands(root)[0].argv[-1] == "."  # editable install


def test_python_adapter_prefers_configured_tools(tmp_path):
    root = _python_project(tmp_path, "[tool.ruff]\n[tool.mypy]\n")
    adapter = PythonAdapter()
    lint = adapter.lint_commands(root)
    typecheck = adapter.typecheck_commands(root)
    assert lint[0].argv == ["python", "-m", "ruff", "check", "."]
    assert typecheck[0].argv == ["python", "-m", "mypy", "."]
    assert lint[0].requires == "ruff"

    # without configuration, nothing is invented
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "pyproject.toml").write_text("[project]\nname='b'\n")
    assert adapter.lint_commands(bare) == []
    assert adapter.typecheck_commands(bare) == []


def test_python_adapter_reports_missing_dependencies_as_environment_failure(
    tmp_path, monkeypatch
):
    root = _python_project(tmp_path, "[tool.ruff]\n")
    adapter = PythonAdapter()

    def which(name):
        return "/usr/bin/yes" if name in ("python", "pytest") else None

    monkeypatch.setattr("harness.adapters.python_adapter.shutil.which", which)
    status = adapter.check_environment(root)
    assert not status.ok
    assert status.missing == ["ruff"]


def test_python_requirements_project_detected(tmp_path):
    (tmp_path / "requirements.txt").write_text("rich>=13\n")
    adapter, _result = detect_adapter(tmp_path)
    assert adapter is not None and adapter.name == "python"
    setup = adapter.setup_commands(tmp_path)
    assert setup[0].argv == ["python", "-m", "pip", "install", "-r",
                             "requirements.txt"]


# ---------------------------------------------------------------------------
# Issue #59 — Node adapter and workspaces
# ---------------------------------------------------------------------------


def _git_init(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True,
                   check=True)


def test_node_adapter_prefers_configured_scripts(tmp_path):
    _git_init(tmp_path)
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"test": "jest", "build": "webpack", "lint": "eslint ."},
    }))
    adapter = NodeAdapter()
    assert adapter.detect(tmp_path).supported
    full = adapter.full_test_command(tmp_path)
    assert full.argv == ["npm", "run", "test"]
    target = adapter.target_test_command(tmp_path, ["src/a.test.js"])
    assert target.argv == ["npm", "run", "test", "src/a.test.js"]
    assert full.argv != target.argv  # targeted and full are distinct
    assert adapter.build_commands(tmp_path)[0].argv == ["npm", "run", "build"]
    assert adapter.lint_commands(tmp_path)[0].argv == ["npm", "run", "lint"]
    # no typecheck script -> nothing invented
    assert adapter.typecheck_commands(tmp_path) == []


def test_node_adapter_selects_package_manager_by_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"test": "vitest"},
    }))
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n")
    adapter = NodeAdapter()
    assert adapter.full_test_command(tmp_path).argv == ["pnpm", "run", "test"]
    setup = adapter.setup_commands(tmp_path)
    assert setup[0].argv == ["pnpm", "install"]
    assert setup[0].requires == "pnpm"


def test_node_adapter_selects_yarn_by_lockfile_and_omits_run_subcommand(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"test": "jest"},
    }))
    (tmp_path / "yarn.lock").write_text("__metadata:\n  version: 8\n")
    adapter = NodeAdapter()
    # yarn runs scripts directly, without the `run` subcommand npm/pnpm need
    assert adapter.full_test_command(tmp_path).argv == ["yarn", "test"]
    setup = adapter.setup_commands(tmp_path)
    assert setup[0].argv == ["yarn", "install"]
    assert setup[0].requires == "yarn"


def test_node_adapter_npm_ci_when_package_lock_present(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text("{}")
    adapter = NodeAdapter()
    assert adapter.setup_commands(tmp_path)[0].argv == ["npm", "ci"]


def test_node_adapter_detects_workspace_roots(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "workspaces": ["packages/*"],
    }))
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "packages" / "api" / "package.json").write_text("{}")
    adapter = NodeAdapter()
    roots = adapter.workspace_roots(tmp_path)
    assert roots == [tmp_path / "packages" / "api"]


def test_node_workspace_roots_accept_literal_paths_alongside_globs(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "workspaces": ["apps/web", "packages/*"],
    }))
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "packages" / "lib").mkdir(parents=True)
    (tmp_path / "packages" / "empty").mkdir(parents=True)  # bare dir, no manifest
    roots = NodeAdapter().workspace_roots(tmp_path)
    # every directory matching the declared patterns, in deterministic order;
    # manifest-level filtering is the caller's job
    assert roots == [
        tmp_path / "apps" / "web",
        tmp_path / "packages" / "api",
        tmp_path / "packages" / "empty",
        tmp_path / "packages" / "lib",
    ]


def test_node_corrupt_manifest_degrades_to_defaults_without_inventing(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    adapter = NodeAdapter()
    # still a node project (manifest present), but nothing is guessable
    assert adapter.detect(tmp_path).supported
    assert adapter.full_test_command(tmp_path) is None
    assert adapter.target_test_command(tmp_path, ["a.test.js"]) is None
    assert adapter.build_commands(tmp_path) == []
    assert adapter.workspace_roots(tmp_path) == []


def test_node_target_test_without_targets_returns_none(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"test": "jest"},
    }))
    assert NodeAdapter().target_test_command(tmp_path, []) is None


def test_node_without_test_script_returns_none_not_invented(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    adapter = NodeAdapter()
    assert adapter.full_test_command(tmp_path) is None
    assert adapter.target_test_command(tmp_path, ["x.test.js"]) is None


def test_node_environment_reports_missing_package_manager(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}")
    monkeypatch.setattr(
        "harness.adapters.node_adapter.shutil.which", lambda name: None
    )
    status = NodeAdapter().check_environment(tmp_path)
    assert not status.ok
    assert status.missing == ["npm"]


# ---------------------------------------------------------------------------
# Issue #13 — shared conftest fixtures (python_project / node_project /
# npm_monorepo) must select the right adapter deterministically
# ---------------------------------------------------------------------------


def test_shared_python_project_fixture_selects_python_adapter(python_project):
    adapter, result = detect_adapter(python_project)
    assert result.supported
    assert adapter is not None and adapter.name == "python"
    # pytest is configured via [tool.pytest.ini_options] in the fixture
    full = adapter.full_test_command(python_project)
    assert full is not None and full.requires == "pytest"
    target = adapter.target_test_command(
        python_project, ["tests/test_main.py"]
    )
    assert target.argv == ["python", "-m", "pytest", "-q", "tests/test_main.py"]


def test_shared_node_project_fixture_prefers_configured_scripts(node_project):
    adapter, result = detect_adapter(node_project)
    assert result.supported
    assert adapter is not None and adapter.name == "node"
    # npm ci because the fixture ships a package-lock.json
    setup = adapter.setup_commands(node_project)
    assert setup[0].argv == ["npm", "ci"]
    # configured scripts win over any invented command
    assert adapter.full_test_command(node_project).argv == ["npm", "run", "test"]
    assert adapter.build_commands(node_project)[0].argv == ["npm", "run", "build"]
    assert adapter.lint_commands(node_project)[0].argv == ["npm", "run", "lint"]
    target = adapter.target_test_command(node_project, ["src/index.test.ts"])
    assert target.argv == ["npm", "run", "test", "src/index.test.ts"]


def test_shared_monorepo_fixture_detects_workspaces(npm_monorepo):
    adapter = NodeAdapter()
    assert adapter.detect(npm_monorepo).supported
    roots = adapter.workspace_roots(npm_monorepo)
    assert (npm_monorepo / "packages" / "app") in roots
    assert (npm_monorepo / "packages" / "lib") in roots
    # the fixture declares scripts at the root: test command comes from there
    full = adapter.full_test_command(npm_monorepo)
    assert full is not None and full.argv == ["npm", "run", "test"]
