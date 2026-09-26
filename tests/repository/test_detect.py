"""Detection behavior: languages, manifests, frameworks, workspaces, tests, commands.

Covers sub-issue #31 (detect languages, manifests, workspaces, and tests).
"""

from __future__ import annotations

import json

from harness.repository import profile_repository


def languages_of(profile) -> dict[str, int]:
    return {stat.language: stat.file_count for stat in profile.summary.languages}


def manifest_by_path(profile, path):
    for manifest in profile.manifests:
        if manifest.path == path:
            return manifest
    raise AssertionError(f"manifest {path} not found in {[(m.path) for m in profile.manifests]}")


def test_python_project_detected_from_manifest_and_source(write_tree):
    root = write_tree(
        {
            "pyproject.toml": (
                "[project]\n"
                'name = "demo-api"\n'
                'dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]\n\n'
                "[tool.pytest.ini_options]\n"
                'testpaths = ["tests"]\n\n'
                "[tool.ruff]\n"
                "line-length = 100\n"
            ),
            "src/main.py": "from fastapi import FastAPI\n",
            "src/models.py": "x = 1\n",
            "tests/test_main.py": "def test_ok():\n    assert True\n",
        }
    )
    profile = profile_repository(root)
    assert "python" in languages_of(profile)
    manifest = manifest_by_path(profile, "pyproject.toml")
    assert manifest.parsed is True
    assert manifest.project_name == "demo-api"
    assert manifest.ecosystem == "python"
    frameworks = {hit.name for hit in profile.frameworks}
    assert {"fastapi", "uvicorn"} <= frameworks
    commands = {command.purpose: command for command in profile.commands}
    assert commands["test"].command == "pytest"
    assert commands["lint"].command == "ruff check ."
    assert ("tests/test_main.py", "python-test-file") in {
        (loc.path, loc.kind) for loc in profile.tests
    }
    assert ("tests", "python-test-dir") in {(loc.path, loc.kind) for loc in profile.tests}


def test_node_project_detected_from_manifest_and_lockfile(write_tree):
    root = write_tree(
        {
            "package.json": json.dumps(
                {
                    "name": "demo-web",
                    "scripts": {"test": "vitest run", "build": "vite build", "dev": "vite"},
                    "dependencies": {"react": "^18", "next": "^14"},
                    "devDependencies": {"typescript": "^5", "vitest": "^1"},
                }
            ),
            "package-lock.json": '{"name": "demo-web", "lockfileVersion": 3}',
            "tsconfig.json": '{"compilerOptions": {"strict": true}}',
            "src/index.tsx": "export default 1;\n",
            "src/index.test.tsx": "it('works', () => {});\n",
        }
    )
    profile = profile_repository(root)
    assert "typescript" in languages_of(profile)
    manifest = manifest_by_path(profile, "package.json")
    assert manifest.parsed is True
    assert manifest.project_name == "demo-web"
    lock = manifest_by_path(profile, "package-lock.json")
    assert lock.kind == "lockfile"
    assert manifest_by_path(profile, "tsconfig.json").kind == "tsconfig.json"
    frameworks = {hit.name for hit in profile.frameworks}
    assert {"react", "next", "typescript"} <= frameworks
    commands = {command.purpose: command.command for command in profile.commands}
    assert commands["test"] == "npm test"
    assert commands["build"] == "npm run build"
    assert commands["run"] == "npm run dev"
    assert ("src/index.test.tsx", "node-test-file") in {
        (loc.path, loc.kind) for loc in profile.tests
    }


def test_package_manager_field_sets_pnpm_runner(write_tree):
    root = write_tree(
        {
            "package.json": json.dumps(
                {
                    "name": "web",
                    "packageManager": "pnpm@9.1.0",
                    "scripts": {"test": "vitest", "build": "vite build"},
                }
            ),
            "packages/app/package.json": json.dumps({"name": "app", "scripts": {"build": "vite"}}),
        }
    )
    profile = profile_repository(root)
    commands = {(c.purpose, c.command) for c in profile.commands}
    assert ("test", "pnpm run test") in commands
    assert ("build", "pnpm run build") in commands


def test_yarn_lockfile_sets_yarn_runner(write_tree):
    root = write_tree(
        {
            "package.json": json.dumps({"name": "web", "scripts": {"test": "jest"}}),
            "yarn.lock": "# yarn lockfile v1\n",
        }
    )
    profile = profile_repository(root)
    assert ("test", "yarn test") in {(c.purpose, c.command) for c in profile.commands}


def test_npm_workspaces_monorepo(write_tree):
    root = write_tree(
        {
            "package.json": json.dumps(
                {
                    "name": "monorepo",
                    "workspaces": ["packages/*", "apps/web"],
                    "scripts": {"test": "jest"},
                }
            ),
            "packages/api/package.json": json.dumps({"name": "api"}),
            "packages/api/src/index.js": "export 1;\n",
            "apps/web/package.json": json.dumps({"name": "web"}),
            "packages/empty/": None,
        }
    )
    profile = profile_repository(root)
    assert len(profile.workspaces) == 1
    workspace = profile.workspaces[0]
    assert workspace.kind == "npm"
    assert workspace.declared_members == ("packages/*", "apps/web")
    # Only member dirs that actually contain a package.json resolve.
    assert workspace.resolved_members == ("apps/web", "packages/api")
    assert "packages/api/package.json" in {m.path for m in profile.manifests}


def test_pnpm_workspace_file(write_tree):
    root = write_tree(
        {
            "pnpm-workspace.yaml": "packages:\n  - 'services/*'\n  - 'toolbox'\n",
            "services/auth/package.json": json.dumps({"name": "auth"}),
            "toolbox/package.json": json.dumps({"name": "toolbox"}),
        }
    )
    profile = profile_repository(root)
    workspace = profile.workspaces[0]
    assert workspace.kind == "pnpm"
    assert workspace.declared_members == ("services/*", "toolbox")
    assert workspace.resolved_members == ("services/auth", "toolbox")


def test_uv_and_cargo_workspaces(write_tree):
    root = write_tree(
        {
            "Cargo.toml": "[workspace]\nmembers = [\"crates/*\"]\n",
            "crates/core/Cargo.toml": "[package]\nname = \"core\"\n",
            "pyproject.toml": "[tool.uv.workspace]\nmembers = [\"libs/*\"]\n",
            "libs/etl/pyproject.toml": "[project]\nname = \"etl\"\n",
        }
    )
    profile = profile_repository(root)
    by_kind = {ws.kind: ws for ws in profile.workspaces}
    assert by_kind["cargo"].resolved_members == ("crates/core",)
    assert by_kind["uv"].resolved_members == ("libs/etl",)


def test_requirements_and_go_mod_detection(write_tree):
    root = write_tree(
        {
            "requirements.txt": "# comment\n\ndjango==5.0\n-e ./editable\n-r base.txt\ncelery>=5\n",
            "go.mod": "module example.com/m/cmdtool\n\ngo 1.22\n",
            "manage.py": "x = 1\n",
        }
    )
    profile = profile_repository(root)
    frameworks = {hit.name for hit in profile.frameworks if hit.ecosystem == "python"}
    assert {"django", "celery"} <= frameworks
    go_manifest = manifest_by_path(profile, "go.mod")
    assert go_manifest.parsed is True
    assert go_manifest.project_name == "example.com/m/cmdtool"


def test_malformed_manifest_warns_but_does_not_fail(write_tree):
    root = write_tree(
        {
            "package.json": "{not valid json,,,",
            "app.js": "console.log(1);\n",
        }
    )
    profile = profile_repository(root)
    manifest = manifest_by_path(profile, "package.json")
    assert manifest.parsed is False
    assert any("package.json" in warning for warning in profile.warnings)
    assert "app.js" in {entry.path for entry in profile.files}


def test_oversized_manifest_is_not_parsed(write_tree):
    root = write_tree(
        {
            "package.json": json.dumps({"name": "big", "data": "x" * 4096}),
            "index.js": "1;\n",
        }
    )
    profile = profile_repository(root, _limits(max_manifest_bytes=1024))
    manifest = manifest_by_path(profile, "package.json")
    assert manifest.parsed is False
    assert any("max_manifest_bytes" in warning for warning in profile.warnings)


def _limits(**overrides):
    from harness.contracts.repository import InventoryLimits

    defaults = dict(
        max_files=100_000,
        max_depth=32,
        max_manifest_bytes=2 * 1024 * 1024,
        max_gitignore_bytes=256 * 1024,
        deadline_seconds=60.0,
    )
    defaults.update(overrides)
    return InventoryLimits(**defaults)


def test_test_location_cap_sets_truncated_flag(write_tree):
    spec = {f"tests/test_case_{i:03d}.py": "def test_x(): pass\n" for i in range(510)}
    root = write_tree(spec)
    profile = profile_repository(root)
    assert profile.tests_truncated is True
    assert len(profile.tests) <= 500
    # Directories are prioritized over the file list.
    assert profile.tests[0].path == "tests"
    assert profile.tests[0].file_count == 510


def test_node_test_dirs_and_python_test_dirs(write_tree):
    root = write_tree(
        {
            "src/__tests__/unit.spec.js": "it('x');\n",
            "src/__tests__/helper.js": "h();\n",
            "tests/test_a.py": "def test_a(): pass\n",
            "app.py": "1;\n",
        }
    )
    profile = profile_repository(root)
    kinds = {(loc.path, loc.kind) for loc in profile.tests}
    assert ("src/__tests__", "node-test-dir") in kinds
    assert ("src/__tests__/unit.spec.js", "node-test-file") in kinds
    assert ("tests", "python-test-dir") in kinds
    # helper.js is not a test file and does not create a test dir entry.
    assert ("src/__tests__/helper.js", "node-test-file") not in kinds


def test_makefile_targets_become_commands(write_tree):
    root = write_tree(
        {
            "Makefile": (
                "PATH := $(shell echo 1)\n.PHONY: test lint\n\n"
                "test:\n\tpytest\n\nlint:\n\truff\n\nbuild:\n\tdocker build .\n"
            ),
            "app.py": "1;\n",
        }
    )
    profile = profile_repository(root)
    commands = {(c.purpose, c.command) for c in profile.commands}
    assert ("test", "make test") in commands
    assert ("lint", "make lint") in commands
    assert ("build", "make build") in commands


def test_dockerfile_and_special_names_classified(write_tree):
    root = write_tree(
        {
            "Dockerfile": "FROM python:3.12\n",
            "Makefile": "all:\n\techo hi\n",
            "logo.png": b"\x89PNG\r\n",
            "main.go": "package main\n",
        }
    )
    profile = profile_repository(root)
    lang_by_path = {entry.path: entry.language for entry in profile.files}
    assert lang_by_path["Dockerfile"] == "dockerfile"
    assert lang_by_path["Makefile"] == "makefile"
    assert lang_by_path["main.go"] == "go"
    png_entry = next(e for e in profile.files if e.path == "logo.png")
    assert png_entry.is_binary is True
    assert png_entry.language == "other"
