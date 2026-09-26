"""Language, manifest, workspace, framework, test, and command detection.

Detection reads manifest files only (package.json, pyproject.toml,
requirements*.txt, pnpm-workspace.yaml). Source and test files are classified
by path; file bodies are never read, so profiling cost stays bounded.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .models import FileEntry, LanguageStat, Manifest, TestLocation, WorkspaceRoot

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".pyw": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".txt": "Text",
}

MANIFEST_BASENAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "Makefile",
}

PY_FRAMEWORKS = {
    "fastapi", "flask", "django", "starlette", "pydantic", "pytest", "httpx",
    "sqlalchemy", "celery", "typer", "click", "aiohttp", "uvicorn", "gunicorn",
}
JS_FRAMEWORKS = {
    "react", "next", "vue", "nuxt", "svelte", "express", "fastify",
    "@nestjs/core", "vite", "jest", "vitest", "mocha", "typescript", "eslint",
    "prettier", "tailwindcss", "webpack",
}

TEST_DIR_NAMES = {"tests", "test", "__tests__", "e2e", "integration", "unit"}

_PY_TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py)$")
_JS_TEST_FILE = re.compile(r"\.(test|spec)\.(js|jsx|mjs|cjs|ts|tsx|mts|cts)$")


def is_test_file(path: str) -> bool:
    return bool(_PY_TEST_FILE.search(path) or _JS_TEST_FILE.search(path))


def detect_languages(entries: list[FileEntry]) -> list[LanguageStat]:
    counts: dict[str, list[int]] = {}
    for entry in entries:
        basename = entry.path.rsplit("/", 1)[-1]
        if "." in basename:
            suffix = basename.rsplit(".", 1)[-1].lower()
            language = LANGUAGE_BY_SUFFIX.get(f".{suffix}", "Other")
        else:
            language = "Other"
        stat = counts.setdefault(language, [0, 0])
        stat[0] += 1
        stat[1] += entry.size
    stats = [LanguageStat(lang, files, total_bytes) for lang, (files, total_bytes) in counts.items()]
    stats.sort(key=lambda s: (-s.files, s.language))
    return stats


def find_manifests(entries: list[FileEntry]) -> list[Manifest]:
    manifests: list[Manifest] = []
    for entry in entries:
        name = entry.path.rsplit("/", 1)[-1]
        if name in MANIFEST_BASENAMES or (
            name.startswith("requirements") and name.endswith(".txt")
        ):
            manifests.append(Manifest(kind=name, path=entry.path))
    return manifests


def load_manifest_bodies(root: Path, manifests: list[Manifest]) -> dict[str, object]:
    """Read the small subset of manifests whose content detection needs."""
    bodies: dict[str, object] = {}
    for manifest in manifests:
        path = root / manifest.path
        try:
            if manifest.kind == "package.json":
                with open(path, encoding="utf-8") as fh:
                    bodies[manifest.path] = json.load(fh)
            elif manifest.kind == "pyproject.toml":
                with open(path, "rb") as fh:
                    bodies[manifest.path] = tomllib.load(fh)
            elif manifest.kind.startswith("requirements") and manifest.kind.endswith(".txt"):
                with open(path, encoding="utf-8") as fh:
                    bodies[manifest.path] = fh.read().splitlines()
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            bodies[manifest.path] = None
    return bodies


def detect_frameworks(manifests: list[Manifest], bodies: dict[str, object]) -> list[str]:
    found: set[str] = set()
    for manifest in manifests:
        body = bodies.get(manifest.path)
        if manifest.kind == "package.json" and isinstance(body, dict):
            for section in ("dependencies", "devDependencies"):
                deps = body.get(section)
                if isinstance(deps, dict):
                    for name in deps:
                        lowered = name.lower()
                        if lowered in JS_FRAMEWORKS:
                            found.add(lowered)
        elif manifest.kind == "pyproject.toml" and isinstance(body, dict):
            found.update(_pyproject_frameworks(body))
        elif manifest.kind.startswith("requirements") and isinstance(body, list):
            for line in body:
                name = _pep508_name(line)
                if name in PY_FRAMEWORKS:
                    found.add(name)
    return sorted(found)


def _pyproject_frameworks(body: dict) -> set[str]:
    found: set[str] = set()
    project = body.get("project")
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            for raw in deps:
                name = _pep508_name(raw)
                if name in PY_FRAMEWORKS:
                    found.add(name)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    for raw in group:
                        name = _pep508_name(raw)
                        if name in PY_FRAMEWORKS:
                            found.add(name)
    tool = body.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            poetry_deps = poetry.get("dependencies")
            if isinstance(poetry_deps, dict):
                for name in poetry_deps:
                    if name.lower() in PY_FRAMEWORKS:
                        found.add(name.lower())
        if isinstance(tool.get("pytest"), dict):
            found.add("pytest")
    return found


def _pep508_name(raw: str) -> str:
    name = raw.strip()
    if not name or name.startswith(("#", "-")):
        return ""
    cut = len(name)
    for sep in (";", "<", ">", "=", "!", "~", "[", " ", "\t"):
        index = name.find(sep)
        if index != -1:
            cut = min(cut, index)
    return name[:cut].strip().lower()


def detect_workspaces(
    manifests: list[Manifest], bodies: dict[str, object]
) -> tuple[list[WorkspaceRoot], bool]:
    """Return workspace roots and whether the layout is a monorepo."""
    by_dir: dict[str, str] = {}
    explicit_monorepo = False
    for manifest in manifests:
        directory = manifest.path.rsplit("/", 1)[0] if "/" in manifest.path else "."
        if manifest.kind == "package.json":
            by_dir.setdefault(directory, "node")
            body = bodies.get(manifest.path)
            if isinstance(body, dict) and body.get("workspaces"):
                explicit_monorepo = True
        elif manifest.kind == "pyproject.toml":
            by_dir.setdefault(directory, "python")
        elif manifest.kind == "pnpm-workspace.yaml":
            explicit_monorepo = True
    families: dict[str, int] = {}
    for kind in by_dir.values():
        families[kind] = families.get(kind, 0) + 1
    monorepo = explicit_monorepo or any(count > 1 for count in families.values())
    workspaces = [WorkspaceRoot(path=p, kind=k) for p, k in sorted(by_dir.items())]
    return workspaces, monorepo


def detect_test_locations(entries: list[FileEntry]) -> list[TestLocation]:
    counts: dict[str, int] = {}
    for entry in entries:
        if not is_test_file(entry.path):
            continue
        counts[_test_root_of(entry.path)] = counts.get(_test_root_of(entry.path), 0) + 1
    return [TestLocation(path=p, file_count=c) for p, c in sorted(counts.items())]


def _test_root_of(path: str) -> str:
    """Map a test file to its topmost conventional test directory."""
    parts = path.split("/")
    for index, part in enumerate(parts[:-1]):
        if part in TEST_DIR_NAMES:
            return "/".join(parts[: index + 1])
    parent = "/".join(parts[:-1])
    return parent if parent else "."
