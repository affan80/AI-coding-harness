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


def _commands_by_purpose(profile) -> dict[str, str]:
    return {c.purpose: c.command for c in profile.commands}


def test_node_commands_are_never_invented(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    # package.json with no scripts at all: no commands may be guessed.
    make_file(root, "package.json", json.dumps({"name": "bare"}))
    make_file(root, "src/index.ts", "export const x = 1;\n")

    profile = profile_repository(root)

    assert profile.commands == ()


def test_node_commands_come_only_from_configured_scripts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    # "check" is one of the contract's canonical purposes; "compile" is not.
    make_file(root, "package.json", json.dumps({
        "name": "app",
        "scripts": {"check": "tsc --noEmit", "compile": "tsc"},
    }))
    make_file(root, "src/index.ts", "export const x = 1;\n")

    profile = profile_repository(root)

    by_purpose = _commands_by_purpose(profile)
    # canonical-named scripts are reported under their configured purpose
    assert by_purpose == {"check": "npm run check"}
    # a non-canonical script name is never invented into a canonical purpose
    assert "compile" not in by_purpose


def test_requirements_only_project_invents_no_commands(tmp_path: Path) -> None:
    # The no-invention rule wins over convenience: a bare requirements.txt
    # with a tests/ directory is not enough evidence to claim a test command.
    # (The pre-rework detector mapped this to "pytest -q"; the contract-based
    # implementation only reports commands with an explicit source.)
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "requirements.txt", "rich>=13\n")
    make_file(root, "tests/test_demo.py", "def test_ok():\n    assert True\n")

    profile = profile_repository(root)

    assert profile.commands == ()
    manifest_kinds = {m.kind for m in profile.manifests}
    assert "requirements.txt" in manifest_kinds


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

    assert _commands_by_purpose(profile) == {
        "build": "npm run build",
        "lint": "ruff check .",
        "test": "npm test",
    }
    # determinism: same inputs, byte-identical command mapping
    again = profile_repository(root)
    assert _commands_by_purpose(again) == _commands_by_purpose(profile)


def test_monorepo_profile_is_stable_for_unchanged_input(npm_monorepo: Path) -> None:
    first = profile_repository(npm_monorepo)
    second = profile_repository(npm_monorepo)

    assert first.fingerprint() == second.fingerprint()
    assert first.commands == second.commands
    # workspaces detected (both package roots) and stable across runs
    assert len(first.workspaces) == 2
    assert first.workspaces == second.workspaces
    assert _commands_by_purpose(first).get("test") == "npm test"


def test_manifests_record_kind_and_path_for_both_ecosystems(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "pyproject.toml", "[project]\nname = 'x'\n")
    make_file(root, "requirements-dev.txt", "pytest\n")
    make_file(root, "package.json", json.dumps({"name": "n"}))

    profile = profile_repository(root)

    manifest_kinds = {m.kind for m in profile.manifests}
    assert manifest_kinds == {"pyproject.toml", "requirements.txt", "package.json"}
