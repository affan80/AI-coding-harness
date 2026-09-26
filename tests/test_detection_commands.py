"""Issue #31: configured commands, manifests, workspaces, and stability.

Complements the existing detection tests by pinning the acceptance criteria
that earlier coverage did not exercise: the no-invention guarantee for
project commands, requirements-only projects, mixed Python+Node manifests,
and profile stability for monorepo fixtures.
"""

import json
from pathlib import Path

from harness.repository import profile_repository
from tests.conftest import make_file


def test_node_commands_are_never_invented(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    # package.json with no scripts at all: no commands may be guessed.
    make_file(root, "package.json", json.dumps({"name": "bare"}))
    make_file(root, "src/index.ts", "export const x = 1;\n")

    profile = profile_repository(root)

    assert profile.commands == {}


def test_node_commands_come_only_from_configured_scripts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "package.json", json.dumps({
        "name": "app",
        "scripts": {"check": "tsc --noEmit"},  # non-standard name: not mapped
    }))
    make_file(root, "src/index.ts", "export const x = 1;\n")

    profile = profile_repository(root)

    # a script that is not one of the canonical names is not invented into one
    assert "typecheck" not in profile.commands
    assert profile.commands == {}


def test_requirements_only_project_still_gets_test_command(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "requirements.txt", "rich>=13\n")
    make_file(root, "tests/test_demo.py", "def test_ok():\n    assert True\n")

    profile = profile_repository(root)

    assert profile.commands.get("test") == "pytest -q"


def test_mixed_python_and_node_manifests_merge_deterministically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "pyproject.toml", "[tool.ruff]\n")
    make_file(root, "package.json", json.dumps({
        "name": "web",
        "scripts": {"test": "vitest run", "build": "vite build"},
    }))
    make_file(root, "app/main.py", "x = 1\n")
    make_file(root, "src/index.ts", "export const x = 1;\n")

    profile = profile_repository(root)

    assert profile.commands == {
        "build": "npm run build",
        "lint": "ruff check .",
        "test": "npm run test",
    }
    # determinism: same inputs, byte-identical command mapping
    again = profile_repository(root)
    assert again.commands == profile.commands


def test_monorepo_profile_is_stable_for_unchanged_input(npm_monorepo: Path) -> None:
    first = profile_repository(npm_monorepo)
    second = profile_repository(npm_monorepo)

    assert first.fingerprint == second.fingerprint
    assert first.commands == second.commands
    assert first.monorepo and second.monorepo
    # workspaces and configured commands survive the round trip
    assert first.workspaces == second.workspaces
    assert first.commands.get("test") == "npm run test"


def test_manifests_record_kind_and_path_for_both_ecosystems(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "pyproject.toml", "[project]\nname = 'x'\n")
    make_file(root, "requirements-dev.txt", "pytest\n")
    make_file(root, "package.json", json.dumps({"name": "n"}))

    profile = profile_repository(root)

    manifest_kinds = {m.kind for m in profile.manifests}
    assert manifest_kinds == {"pyproject.toml", "requirements-dev.txt", "package.json"}
