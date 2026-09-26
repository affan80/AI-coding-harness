"""Manifest, framework, workspace, test, and command detection.

Detection reads only bounded manifest bodies (``max_manifest_bytes``) — never
source files. Everything is derived from the inventory produced by
:mod:`harness.repository.inventory`, so a huge monorepo costs one bounded read
per manifest, not per file. One malformed manifest becomes a warning plus
``ManifestInfo(parsed=False)``, never a failed profile.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from harness.contracts.repository import (
    MAX_TEST_LOCATIONS,
    FileEntry,
    FrameworkHit,
    InventoryLimits,
    ManifestInfo,
    ProjectCommand,
    TestLocation,
    WorkspaceRoot,
)

# --- manifest registry ---------------------------------------------------------

_LOCKFILE_KINDS: dict[str, str] = {
    "package-lock.json": "node",
    "yarn.lock": "node",
    "pnpm-lock.yaml": "node",
    "poetry.lock": "python",
    "uv.lock": "python",
    "Pipfile.lock": "python",
    "Cargo.lock": "rust",
    "go.sum": "go",
    "Gemfile.lock": "ruby",
    "composer.lock": "php",
}

_RECORDED_KINDS: dict[str, tuple[str, str]] = {
    "setup.py": ("setup.py", "python"),
    "setup.cfg": ("setup.cfg", "python"),
    "Pipfile": ("pipfile", "python"),
    "tsconfig.json": ("tsconfig.json", "node"),
    "Gemfile": ("gemfile", "ruby"),
    "pom.xml": ("pom.xml", "java"),
    "build.gradle": ("build.gradle", "java"),
    "build.gradle.kts": ("build.gradle.kts", "java"),
    "settings.gradle": ("settings.gradle", "java"),
    "settings.gradle.kts": ("settings.gradle.kts", "java"),
    "mix.exs": ("mix.exs", "elixir"),
    "environment.yml": ("conda-environment", "python"),
    "environment.yaml": ("conda-environment", "python"),
}

_PARSED_JSON_KINDS: dict[str, str] = {
    "package.json": "node",
    "composer.json": "php",
}
_PARSED_TOML_KINDS: dict[str, str] = {
    "pyproject.toml": "python",
    "Cargo.toml": "rust",
}

_PYTHON_DEP_NAME = re.compile(r"^['\"]?([A-Za-z0-9][A-Za-z0-9._-]*)")
_MAKEFILE_TARGET = re.compile(r"^([a-zA-Z][A-Za-z0-9_.-]*):(?:[^=]|$)")
_GO_MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)

_SCRIPT_PURPOSE_MAP: dict[str, str] = {
    "test": "test",
    "build": "build",
    "lint": "lint",
    "fmt": "format",
    "format": "format",
    "check": "check",
    "typecheck": "check",
    "type-check": "check",
    "tsc": "check",
    "dev": "run",
    "start": "run",
    "serve": "run",
}

_MAKEFILE_PURPOSES: dict[str, str] = {
    "test": "test",
    "lint": "lint",
    "build": "build",
    "fmt": "format",
    "format": "format",
    "check": "check",
}

_KNOWN_NODE_FRAMEWORKS: frozenset[str] = frozenset(
    {
        "react",
        "react-dom",
        "next",
        "vue",
        "nuxt",
        "svelte",
        "@sveltejs/kit",
        "@angular/core",
        "express",
        "fastify",
        "@nestjs/core",
        "koa",
        "hono",
        "vite",
        "webpack",
        "esbuild",
        "rollup",
        "jest",
        "vitest",
        "mocha",
        "cypress",
        "playwright",
        "@playwright/test",
        "puppeteer",
        "typescript",
        "eslint",
        "prettier",
        "tailwindcss",
        "prisma",
        "@prisma/client",
        "drizzle-orm",
        "sequelize",
        "typeorm",
        "mongoose",
        "graphql",
        "axios",
    }
)

_FRAMEWORK_ALIASES: dict[str, str] = {
    "@sveltejs/kit": "sveltekit",
    "@angular/core": "angular",
    "@nestjs/core": "nestjs",
    "@playwright/test": "playwright",
    "@prisma/client": "prisma",
}

_KNOWN_PYTHON_FRAMEWORKS: frozenset[str] = frozenset(
    {
        "django",
        "flask",
        "fastapi",
        "starlette",
        "pyramid",
        "tornado",
        "sanic",
        "litestar",
        "aiohttp",
        "sqlalchemy",
        "alembic",
        "pydantic",
        "celery",
        "click",
        "typer",
        "uvicorn",
        "gunicorn",
        "grpcio",
        "scrapy",
        "requests",
        "httpx",
        "jinja2",
        "pytest",
        "hypothesis",
        "mypy",
        "ruff",
        "black",
        "flake8",
        "isort",
        "pylint",
        "tox",
        "nox",
    }
)

_PYTHON_TEST_FILE = re.compile(r"^(test_.*|.*_test)\.py$")
_NODE_TEST_FILE = re.compile(r"^.*\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)$")
_PYTHON_TEST_DIRS = frozenset({"tests", "test"})
_NODE_TEST_DIRS = frozenset({"__tests__"})

_MAX_NAME_LEN = 200
_MAX_WARNINGS = 50


@dataclass
class _ManifestDraft:
    """Mutable working state; converted to the frozen ``ManifestInfo`` at the end."""

    path: str
    kind: str
    ecosystem: str
    project_name: str | None = None
    parsed: bool = False


@dataclass
class DetectionResult:
    manifests: list[ManifestInfo] = field(default_factory=list)
    frameworks: list[FrameworkHit] = field(default_factory=list)
    workspaces: list[WorkspaceRoot] = field(default_factory=list)
    tests: list[TestLocation] = field(default_factory=list)
    commands: list[ProjectCommand] = field(default_factory=list)
    tests_truncated: bool = False
    file_bodies_read: int = 0
    warnings: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        if len(self.warnings) < _MAX_WARNINGS:
            self.warnings.append(message)


def detect_structure(
    root: Path, files: list[FileEntry], limits: InventoryLimits
) -> DetectionResult:
    """Derive manifests, frameworks, workspaces, tests, and commands."""
    result = DetectionResult()

    drafts: list[_ManifestDraft] = []
    lockfiles_by_dir: dict[str, str] = {}  # dir -> lockfile basename (first wins)
    for entry in files:  # inventory arrives sorted by path
        draft = _manifest_draft(entry)
        if draft is None:
            continue
        drafts.append(draft)
        if draft.kind == "lockfile" and draft.ecosystem == "node":
            directory = _dir_of(draft.path)
            lockfiles_by_dir.setdefault(directory, draft.path.rsplit("/", 1)[-1])

    # Bounded body parsing for manifests that carry structured metadata.
    payloads: dict[str, dict] = {}
    managers_by_dir: dict[str, str] = {}
    for draft in drafts:
        payload = None
        if draft.kind in _PARSED_TOML_KINDS:
            payload = _parse_toml(root, draft, limits, result)
        elif draft.kind in _PARSED_JSON_KINDS:
            payload = _parse_json(root, draft, limits, result)
        elif draft.kind == "pnpm-workspace.yaml":
            payload = _parse_pnpm_workspace(root, draft, limits, result)
        elif draft.kind == "requirements.txt":
            payload = _parse_requirements(root, draft, limits, result)
        elif draft.kind == "go.mod":
            payload = _parse_go_mod(root, draft, limits, result)
        if payload is not None:
            payloads[draft.path] = payload
            if draft.kind == "package.json":
                declared = payload.get("packageManager")
                if isinstance(declared, str) and declared.strip():
                    managers_by_dir[_dir_of(draft.path)] = declared.split("@", 1)[0].strip()

    result.manifests = [
        ManifestInfo(
            path=draft.path,
            kind=draft.kind,
            ecosystem=draft.ecosystem,
            project_name=draft.project_name,
            parsed=draft.parsed,
        )
        for draft in drafts
    ]

    frameworks: set[tuple[str, str, str]] = set()
    for draft in drafts:
        payload = payloads.get(draft.path)
        if payload is None:
            continue
        if draft.ecosystem == "python":
            for dep in _declared_python_deps(draft.kind, payload):
                canonical = _canonical_python_dep(dep)
                if canonical in _KNOWN_PYTHON_FRAMEWORKS:
                    frameworks.add((canonical, "python", draft.path))
        elif draft.kind == "package.json":
            for dep in _declared_node_deps(payload):
                if dep in _KNOWN_NODE_FRAMEWORKS:
                    canonical = _FRAMEWORK_ALIASES.get(dep, dep)
                    frameworks.add((canonical, "node", draft.path))
    result.frameworks = sorted(
        (FrameworkHit(name=name, ecosystem=eco, source=src) for name, eco, src in frameworks),
        key=lambda hit: (hit.name, hit.source),
    )

    result.workspaces = _detect_workspaces(
        root, drafts, payloads, managers_by_dir, lockfiles_by_dir
    )
    result.tests, result.tests_truncated = _detect_tests(files)
    result.commands = _detect_commands(
        root, drafts, payloads, managers_by_dir, lockfiles_by_dir, frameworks, files, limits, result
    )
    return result


def _dir_of(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


# --- manifest identification ---------------------------------------------------


def _manifest_draft(entry: FileEntry) -> _ManifestDraft | None:
    name = entry.path.rsplit("/", 1)[-1]
    if name in _PARSED_TOML_KINDS:
        return _ManifestDraft(entry.path, name, _PARSED_TOML_KINDS[name])
    if name in _PARSED_JSON_KINDS:
        return _ManifestDraft(entry.path, name, _PARSED_JSON_KINDS[name])
    if name in _LOCKFILE_KINDS:
        return _ManifestDraft(entry.path, "lockfile", _LOCKFILE_KINDS[name])
    if name == "pnpm-workspace.yaml":
        return _ManifestDraft(entry.path, name, "node")
    if name == "requirements.txt" or (name.startswith("requirements-") and name.endswith(".txt")):
        return _ManifestDraft(entry.path, "requirements.txt", "python")
    if name == "go.mod":
        return _ManifestDraft(entry.path, name, "go")
    if name.endswith(".csproj"):
        return _ManifestDraft(entry.path, "csproj", "dotnet")
    recorded = _RECORDED_KINDS.get(name)
    if recorded is not None:
        kind, eco = recorded
        return _ManifestDraft(entry.path, kind, eco)
    return None


# --- bounded body parsing ------------------------------------------------------


def _read_bounded(root: Path, rel: str, limits: InventoryLimits) -> str | None:
    """Read at most ``max_manifest_bytes``; None when missing, oversized, or unreadable."""
    try:
        with open(root / rel, "rb") as handle:
            raw = handle.read(limits.max_manifest_bytes + 1)
    except OSError:
        return None
    if len(raw) > limits.max_manifest_bytes:
        return None
    return raw.decode("utf-8", "replace")


def _parse_toml(
    root: Path, draft: _ManifestDraft, limits: InventoryLimits, result: DetectionResult
) -> dict | None:
    text = _read_bounded(root, draft.path, limits)
    if text is None:
        _mark_unparsed(draft, result, "unreadable or larger than max_manifest_bytes")
        return None
    result.file_bodies_read += 1
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        _mark_unparsed(draft, result, f"malformed TOML: {error}")
        return None
    draft.parsed = True
    draft.project_name = _clean_name(
        _dig(data, "project", "name") or _dig(data, "package", "name")
    )
    return data


def _parse_json(
    root: Path, draft: _ManifestDraft, limits: InventoryLimits, result: DetectionResult
) -> dict | None:
    text = _read_bounded(root, draft.path, limits)
    if text is None:
        _mark_unparsed(draft, result, "unreadable or larger than max_manifest_bytes")
        return None
    result.file_bodies_read += 1
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        _mark_unparsed(draft, result, f"malformed JSON: {error}")
        return None
    if not isinstance(data, dict):
        _mark_unparsed(draft, result, "top-level JSON value is not an object")
        return None
    draft.parsed = True
    draft.project_name = _clean_name(data.get("name"))
    return data


def _parse_pnpm_workspace(
    root: Path, draft: _ManifestDraft, limits: InventoryLimits, result: DetectionResult
) -> dict | None:
    text = _read_bounded(root, draft.path, limits)
    if text is None:
        _mark_unparsed(draft, result, "unreadable or larger than max_manifest_bytes")
        return None
    result.file_bodies_read += 1
    members: list[str] = []
    in_packages = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("packages:"):
            in_packages = True
            continue
        if in_packages and stripped.startswith("-"):
            members.append(stripped[1:].strip().strip("'\""))
        elif in_packages and not line[:1].isspace():
            in_packages = False
    draft.parsed = True
    return {"members": members}


def _parse_requirements(
    root: Path, draft: _ManifestDraft, limits: InventoryLimits, result: DetectionResult
) -> dict:
    text = _read_bounded(root, draft.path, limits)
    if text is None:
        _mark_unparsed(draft, result, "unreadable or larger than max_manifest_bytes")
        return {"deps": []}
    result.file_bodies_read += 1
    deps: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-", ".")):
            continue
        match = _PYTHON_DEP_NAME.match(stripped)
        if match:
            deps.append(match.group(1))
    draft.parsed = True
    return {"deps": deps}


def _parse_go_mod(
    root: Path, draft: _ManifestDraft, limits: InventoryLimits, result: DetectionResult
) -> dict | None:
    text = _read_bounded(root, draft.path, limits)
    if text is None:
        _mark_unparsed(draft, result, "unreadable or larger than max_manifest_bytes")
        return None
    result.file_bodies_read += 1
    draft.parsed = True
    match = _GO_MODULE.search(text)
    if match:
        draft.project_name = _clean_name(match.group(1))
    return {"module": draft.project_name or ""}


def _mark_unparsed(draft: _ManifestDraft, result: DetectionResult, reason: str) -> None:
    draft.parsed = False
    result.add_warning(f"manifest {draft.path} not parsed: {reason}")


# --- dependency extraction -----------------------------------------------------


def _declared_python_deps(kind: str, payload: dict) -> list[str]:
    if kind == "pyproject.toml":
        deps: list[str] = []
        project = payload.get("project")
        if isinstance(project, dict):
            deps.extend(str(d) for d in project.get("dependencies", []) if isinstance(d, str))
            optional = project.get("optional-dependencies")
            if isinstance(optional, dict):
                for group in optional.values():
                    if isinstance(group, list):
                        deps.extend(str(d) for d in group if isinstance(d, str))
        tool = payload.get("tool")
        poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
        poetry_deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
        if isinstance(poetry_deps, dict):
            deps.extend(str(name) for name in poetry_deps if name != "python")
        return deps
    if kind == "requirements.txt":
        return payload.get("deps", [])
    return []


def _declared_node_deps(payload: dict) -> list[str]:
    deps: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = payload.get(section)
        if isinstance(block, dict):
            deps.extend(str(name) for name in block)
    return deps


def _canonical_python_dep(declared: str) -> str:
    match = _PYTHON_DEP_NAME.match(declared.strip())
    name = match.group(1) if match else declared
    return name.lower().replace("_", "-")


# --- workspaces -----------------------------------------------------------------


def _detect_workspaces(
    root: Path,
    drafts: list[_ManifestDraft],
    payloads: dict[str, dict],
    managers_by_dir: dict[str, str],
    lockfiles_by_dir: dict[str, str],
) -> list[WorkspaceRoot]:
    workspaces: list[WorkspaceRoot] = []
    manifest_dirs: set[str] = {_dir_of(draft.path) for draft in drafts}
    manifest_paths: set[str] = {draft.path for draft in drafts}

    def manager_for(directory: str) -> str:
        if directory in managers_by_dir:
            return managers_by_dir[directory]
        lockfile = lockfiles_by_dir.get(directory)
        if lockfile == "pnpm-lock.yaml":
            return "pnpm"
        if lockfile == "yarn.lock":
            return "yarn"
        return "npm"

    for draft in drafts:
        payload = payloads.get(draft.path)
        if payload is None:
            continue
        directory = _dir_of(draft.path)
        if draft.kind == "package.json":
            declared = payload.get("workspaces")
            if isinstance(declared, dict):
                declared = declared.get("packages")
            if isinstance(declared, list) and declared:
                workspaces.append(
                    _resolve_workspace(
                        directory,
                        manager_for(directory),
                        [str(item) for item in declared],
                        "package.json",
                        manifest_dirs,
                        manifest_paths,
                    )
                )
        elif draft.kind == "pnpm-workspace.yaml":
            members = payload.get("members", [])
            if members:
                workspaces.append(
                    _resolve_workspace(
                        directory, "pnpm", [str(m) for m in members],
                        "package.json", manifest_dirs, manifest_paths,
                    )
                )
        elif draft.kind == "pyproject.toml":
            tool = payload.get("tool")
            tool = tool if isinstance(tool, dict) else {}
            uv_section = tool.get("uv") if isinstance(tool.get("uv"), dict) else {}
            uv_ws = uv_section.get("workspace")
            uv_members = uv_ws.get("members") if isinstance(uv_ws, dict) else None
            if isinstance(uv_members, list) and uv_members:
                workspaces.append(
                    _resolve_workspace(
                        directory, "uv", [str(m) for m in uv_ws["members"]],
                        "pyproject.toml", manifest_dirs, manifest_paths,
                    )
                )
        elif draft.kind == "Cargo.toml":
            workspace = payload.get("workspace")
            members = workspace.get("members") if isinstance(workspace, dict) else None
            if isinstance(members, list) and members:
                workspaces.append(
                    _resolve_workspace(
                        directory, "cargo", [str(m) for m in workspace["members"]],
                        "Cargo.toml", manifest_dirs, manifest_paths,
                    )
                )
    return sorted(workspaces, key=lambda ws: ws.path)


def _resolve_workspace(
    dir_rel: str,
    kind: str,
    declared: list[str],
    member_manifest: str,
    manifest_dirs: set[str],
    manifest_paths: set[str],
) -> WorkspaceRoot:
    """Resolve declared member globs against inventory directories that contain
    the member manifest; unresolved declarations are kept verbatim."""
    resolved: list[str] = []
    for pattern in declared:
        cleaned = pattern.strip().strip("/")
        if not cleaned:
            continue
        for candidate in sorted(manifest_dirs):
            if candidate == dir_rel:
                continue
            if dir_rel and candidate.startswith(f"{dir_rel}/"):
                sub = candidate[len(dir_rel) + 1 :]
            elif not dir_rel:
                sub = candidate
            else:
                continue
            if f"{candidate}/{member_manifest}" not in manifest_paths:
                continue
            if sub == cleaned or _dir_glob_match(cleaned, sub):
                resolved.append(candidate)
    return WorkspaceRoot(
        path=dir_rel,
        kind=kind,
        declared_members=tuple(declared),
        resolved_members=tuple(sorted(set(resolved))),
    )


def _dir_glob_match(pattern: str, rel_dir: str) -> bool:
    """Segment-aware glob match where ``*`` stays inside one path segment."""
    regex = _dir_glob_regex(pattern.split("/"))
    return regex is not None and regex.fullmatch(rel_dir) is not None


def _dir_glob_regex(pattern_segments: list[str]) -> re.Pattern[str] | None:
    parts: list[str] = []
    for index, segment in enumerate(pattern_segments):
        last = index == len(pattern_segments) - 1
        if segment == "**":
            parts.append(r".*" if last else r"(?:[^/]+/)*")
            if not last:
                continue
        else:
            parts.append(
                "".join(
                    r"[^/]*" if ch == "*" else r"[^/]" if ch == "?" else re.escape(ch)
                    for ch in segment
                )
            )
        if not last:
            parts.append("/")
    try:
        return re.compile("".join(parts))
    except re.error:
        return None


# --- tests -----------------------------------------------------------------------


def _detect_tests(files: list[FileEntry]) -> tuple[list[TestLocation], bool]:
    test_files: list[tuple[str, str]] = []
    dir_counts: Counter[str] = Counter()
    dir_kinds: dict[str, str] = {}
    for entry in files:
        name = entry.path.rsplit("/", 1)[-1]
        kind = None
        if _PYTHON_TEST_FILE.match(name):
            kind = "python-test-file"
        elif _NODE_TEST_FILE.match(name):
            kind = "node-test-file"
        if kind is None:
            continue
        test_files.append((entry.path, kind))
        parts = entry.path.split("/")
        for ancestor in range(1, len(parts)):
            directory = "/".join(parts[:ancestor])
            base = parts[ancestor - 1]
            if base in _PYTHON_TEST_DIRS:
                dir_counts[directory] += 1
                dir_kinds.setdefault(directory, "python-test-dir")
            elif base in _NODE_TEST_DIRS:
                dir_counts[directory] += 1
                dir_kinds.setdefault(directory, "node-test-dir")

    locations: list[TestLocation] = [
        TestLocation(path=path, kind=dir_kinds[path], file_count=dir_counts[path])
        for path in sorted(dir_counts)
    ]
    total = len(test_files) + len(dir_counts)
    for path, kind in sorted(test_files):
        if len(locations) >= MAX_TEST_LOCATIONS:
            break
        locations.append(TestLocation(path=path, kind=kind, file_count=1))
    return locations, total > MAX_TEST_LOCATIONS


# --- commands --------------------------------------------------------------------


def _detect_commands(
    root: Path,
    drafts: list[_ManifestDraft],
    payloads: dict[str, dict],
    managers_by_dir: dict[str, str],
    lockfiles_by_dir: dict[str, str],
    frameworks: set[tuple[str, str, str]],
    files: list[FileEntry],
    limits: InventoryLimits,
    result: DetectionResult,
) -> list[ProjectCommand]:
    commands: list[ProjectCommand] = []

    def manager_for(directory: str) -> str:
        if directory in managers_by_dir:
            return managers_by_dir[directory]
        lockfile = lockfiles_by_dir.get(directory)
        if lockfile == "pnpm-lock.yaml":
            return "pnpm"
        if lockfile == "yarn.lock":
            return "yarn"
        return "npm"

    for draft in drafts:
        payload = payloads.get(draft.path)
        if payload is None:
            continue
        directory = _dir_of(draft.path)
        prefix = f" ({directory})" if directory else ""
        if draft.kind == "package.json":
            manager = manager_for(directory)
            runner = {"pnpm": "pnpm run", "yarn": "yarn"}.get(manager, "npm run")
            scripts = payload.get("scripts")
            if isinstance(scripts, dict):
                used_purposes: set[str] = set()
                for key in scripts:
                    purpose = _SCRIPT_PURPOSE_MAP.get(str(key))
                    if purpose is None or purpose in used_purposes:
                        continue
                    used_purposes.add(purpose)
                    command = (
                        "npm test"
                        if (str(key) == "test" and manager == "npm")
                        else f"{runner} {key}"
                    )
                    commands.append(
                        ProjectCommand(
                            purpose=purpose,
                            command=command,
                            source=f"package.json:scripts.{key}{prefix}",
                        )
                    )
        elif draft.kind == "pyproject.toml":
            tool = payload.get("tool")
            tool = tool if isinstance(tool, dict) else {}
            if "pytest" in tool:
                commands.append(
                    ProjectCommand("test", "pytest", f"pyproject.toml:tool.pytest{prefix}")
                )
            if isinstance(tool.get("ruff"), dict):
                commands.append(
                    ProjectCommand("lint", "ruff check .", f"pyproject.toml:tool.ruff{prefix}")
                )
            if isinstance(tool.get("mypy"), dict):
                commands.append(
                    ProjectCommand("check", "mypy .", f"pyproject.toml:tool.mypy{prefix}")
                )
            if isinstance(tool.get("black"), dict):
                commands.append(
                    ProjectCommand("format", "black .", f"pyproject.toml:tool.black{prefix}")
                )

    # Config files that are not manifests but do declare commands.
    for entry in files:
        name = entry.path.rsplit("/", 1)[-1]
        command_configs = {"pytest.ini", "tox.ini", "setup.cfg", "noxfile.py",
                           "Makefile", "GNUmakefile"}
        if name not in command_configs:
            continue
        directory = _dir_of(entry.path)
        prefix = f" ({directory})" if directory else ""
        if name == "noxfile.py":
            commands.append(ProjectCommand("test", "nox", f"noxfile.py{prefix}"))
            continue
        body = _read_config_bounded(root, entry.path, limits, result)
        if body is None:
            continue
        if name == "tox.ini":
            commands.append(ProjectCommand("test", "tox", f"tox.ini{prefix}"))
        elif name == "pytest.ini" or (name == "setup.cfg" and "[tool:pytest]" in body):
            commands.append(ProjectCommand("test", "pytest", f"{name}{prefix}"))
        elif name in {"Makefile", "GNUmakefile"}:
            for line in body.splitlines():
                match = _MAKEFILE_TARGET.match(line)
                if match is None:
                    continue
                target = match.group(1)
                purpose = _MAKEFILE_PURPOSES.get(target)
                if purpose is not None:
                    commands.append(
                        ProjectCommand(
                            purpose, f"make {target}", f"Makefile:target:{target}{prefix}"
                        )
                    )

    # Fallback: pytest declared as a dependency implies the standard test command.
    if not any(command.purpose == "test" for command in commands):
        if any(name == "pytest" and eco == "python" for name, eco, _src in frameworks):
            commands.append(ProjectCommand("test", "pytest", "inferred:pytest-dependency"))

    deduped: dict[tuple[str, str, str], ProjectCommand] = {}
    for command in commands:
        deduped.setdefault((command.purpose, command.command, command.source), command)
    return sorted(deduped.values(), key=lambda c: (c.purpose, c.command, c.source))


def _read_config_bounded(
    root: Path, rel: str, limits: InventoryLimits, result: DetectionResult
) -> str | None:
    try:
        with open(root / rel, "rb") as handle:
            raw = handle.read(limits.max_manifest_bytes + 1)
    except OSError:
        return None
    result.file_bodies_read += 1
    return raw[: limits.max_manifest_bytes].decode("utf-8", "replace")


def _dig(data: dict, *keys: str):
    current: object = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:_MAX_NAME_LEN]
