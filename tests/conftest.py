"""Shared fixtures for building synthetic repository trees."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def make_file(root: Path, rel: str, content: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
name = "sample"
dependencies = ["fastapi>=0.110", "sqlalchemy[asyncio]>=2.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx"]

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
    )
    make_file(root, "app/main.py", "from fastapi import FastAPI\n")
    make_file(root, "app/models.py", "class User:\n    pass\n")
    make_file(root, "tests/test_main.py", "def test_ok():\n    assert True\n")
    return root


@pytest.fixture
def node_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "web",
                "scripts": {"test": "vitest run", "build": "vite build", "lint": "eslint ."},
                "dependencies": {"react": "^18.0.0"},
                "devDependencies": {"vite": "^5.0.0", "vitest": "^1.0.0", "typescript": "^5.0.0"},
            }
        )
    )
    (root / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3}))
    make_file(root, "src/index.ts", "export const x = 1;\n")
    make_file(root, "src/index.test.ts", 'import { test } from "vitest";\n')
    return root


@pytest.fixture
def npm_monorepo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"name": "mono", "workspaces": ["packages/*"], "scripts": {"test": "jest"}})
    )
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
    make_file(root, "packages/app/package.json", json.dumps({"name": "app"}))
    make_file(root, "packages/lib/package.json", json.dumps({"name": "lib"}))
    make_file(root, "packages/app/src/main.ts")
    make_file(root, "packages/lib/src/util.ts")
    return root
