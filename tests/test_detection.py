"""Issue #31 — language, manifest, workspace, framework, test, command detection."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_file

from harness.repository import profile_repository


def test_python_project_profile(python_project: Path) -> None:
    profile = profile_repository(python_project)

    python = next(lang for lang in profile.languages if lang.language == "Python")
    assert python.files == 3
    assert python.total_bytes > 0

    kinds = {manifest.kind for manifest in profile.manifests}
    assert "pyproject.toml" in kinds

    assert {"fastapi", "sqlalchemy", "pytest", "httpx"}.issubset(set(profile.frameworks))
    assert profile.commands.get("test") == "pytest -q"

    locations = {location.path: location.file_count for location in profile.test_locations}
    assert locations.get("tests") == 1

    assert [(w.path, w.kind) for w in profile.workspaces] == [(".", "python")]
    assert not profile.monorepo


def test_node_project_profile(node_project: Path) -> None:
    profile = profile_repository(node_project)

    languages = {lang.language: lang.files for lang in profile.languages}
    assert languages["TypeScript"] == 2

    kinds = {manifest.kind for manifest in profile.manifests}
    assert {"package.json", "package-lock.json"}.issubset(kinds)

    assert {"react", "vite", "vitest", "typescript"}.issubset(set(profile.frameworks))
    assert profile.commands == {
        "build": "npm run build",
        "lint": "npm run lint",
        "test": "npm run test",
    }

    locations = {location.path: location.file_count for location in profile.test_locations}
    assert locations.get("src") == 1  # no conventional test dir; grouped by parent
    assert not profile.monorepo


def test_requirements_file_frameworks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "requirements.txt").write_text(
        "# runtime\nFlask==2.3.0\ngunicorn>=21.0\n-r base.txt\n\n"
    )
    make_file(root, "app.py")

    profile = profile_repository(root)

    assert {"flask", "gunicorn"}.issubset(set(profile.frameworks))
    assert "requirements.txt" in {m.kind for m in profile.manifests}


def test_npm_workspaces_monorepo(npm_monorepo: Path) -> None:
    profile = profile_repository(npm_monorepo)

    assert profile.monorepo
    workspace_paths = {workspace.path for workspace in profile.workspaces}
    assert workspace_paths == {".", "packages/app", "packages/lib"}
    assert {workspace.kind for workspace in profile.workspaces} == {"node"}


def test_python_multi_root_monorepo(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "services/api/pyproject.toml").write_text(
        '[project]\nname = "api"\ndependencies = ["fastapi"]\n'
    )
    (root / "services/worker/pyproject.toml").write_text(
        '[project]\nname = "worker"\ndependencies = ["celery"]\n'
    )
    make_file(root, "services/api/main.py")
    make_file(root, "services/worker/loop.py")

    profile = profile_repository(root)

    assert profile.monorepo
    workspace_paths = {workspace.path for workspace in profile.workspaces}
    assert workspace_paths == {"services/api", "services/worker"}


def test_language_stats_sorted_by_count(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(3):
        make_file(root, f"mod{index}.py")
    make_file(root, "app.ts")
    make_file(root, "app.test.ts")
    make_file(root, "style.css")

    profile = profile_repository(root)

    counts = {lang.language: lang.files for lang in profile.languages}
    assert counts == {"Python": 3, "TypeScript": 2, "CSS": 1}
    by_rank = [lang.language for lang in profile.languages]
    assert by_rank == ["Python", "TypeScript", "CSS"]


def test_detection_is_stable_for_unchanged_input(python_project: Path) -> None:
    first = profile_repository(python_project)
    second = profile_repository(python_project)

    assert first.fingerprint == second.fingerprint
    assert first.languages == second.languages
    assert first.frameworks == second.frameworks
    assert first.workspaces == second.workspaces
    assert first.test_locations == second.test_locations
    assert first.commands == second.commands
