"""Workstream B shared contract: repository inventory and profile types.

Self-contained by team rule: standard library only, no imports from other
contract modules. Consumers (orchestrator, planning, context, adapters) code
against these dataclasses; cross-workstream wiring happens at integration
(issue #20). See PRD §§12.5, 13 and FR-04.

Stability contract
------------------
``RepositoryProfile.fingerprint()`` is a SHA-256 over a canonical JSON rendering
of every content-derived field. Volatile fields (machine-specific paths, elapsed
time, environment-dependent warnings/backend) are excluded, so profiling an
unchanged repository twice — including from two different roots — yields the
same fingerprint. ``to_dict()`` / ``from_dict()`` round-trip without loss.
"""

from __future__ import annotations

import hashlib
import json
import types as _types
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from typing import Any, Union, get_args, get_origin, get_type_hints

SCHEMA_VERSION = 1

MAX_LARGEST_FILES = 10
MAX_TEST_LOCATIONS = 500
MAX_WARNINGS = 50


class ProfileContractError(ValueError):
    """Raised when a profile payload cannot be validated or decoded."""


class Language(StrEnum):
    """Canonical lowercase language names for inventory entries."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    KOTLIN = "kotlin"
    SWIFT = "swift"
    C = "c"
    CPP = "cpp"
    CSHARP = "csharp"
    OBJECTIVE_C = "objective-c"
    RUBY = "ruby"
    PHP = "php"
    SHELL = "shell"
    POWERSHELL = "powershell"
    LUA = "lua"
    PERL = "perl"
    SCALA = "scala"
    DART = "dart"
    HTML = "html"
    CSS = "css"
    SCSS = "scss"
    SQL = "sql"
    PROTOBUF = "protobuf"
    GRAPHQL = "graphql"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    XML = "xml"
    MARKDOWN = "markdown"
    DOCKERFILE = "dockerfile"
    MAKEFILE = "makefile"
    CMAKE = "cmake"
    INI = "ini"
    TEXT = "text"
    OTHER = "other"


def _volatile(obj: Any) -> dict[str, Any]:
    """Canonical dict of *obj* with fields marked ``volatile`` removed."""
    if is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        for f in fields(obj):
            if f.metadata.get("volatile"):
                continue
            out[f.name] = _volatile(getattr(obj, f.name))
        return out
    if isinstance(obj, (list, tuple)):
        return [_volatile(v) for v in obj]
    if isinstance(obj, StrEnum):
        return obj.value
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise TypeError(f"cannot fingerprint value of type {type(obj)!r}")


def _encode(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _encode(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if isinstance(obj, StrEnum):
        return obj.value
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    raise TypeError(f"cannot serialize value of type {type(obj)!r}")


def _decode(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v) for v in value)
        return tuple(_decode(a, v) for a, v in zip(args, value, strict=False))
    if origin is Union or origin is _types.UnionType:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _decode(non_none[0], value)
    if is_dataclass(tp):
        if not isinstance(value, dict):
            raise ProfileContractError(f"expected object for {tp.__name__}, got {type(value)!r}")
        kwargs = {}
        hints = get_type_hints(tp)
        for f in fields(tp):
            if f.name not in value:
                raise ProfileContractError(f"missing field {f.name!r} for {tp.__name__}")
            kwargs[f.name] = _decode(hints[f.name], value[f.name])
        return tp(**kwargs)
    if isinstance(tp, type) and issubclass(tp, StrEnum):
        return tp(value)
    return value


@dataclass(frozen=True, slots=True)
class FileEntry:
    """L0 metadata for one candidate file. Bodies are never read to build this."""

    path: str  # POSIX-style, relative to the repository root, no leading "/"
    size_bytes: int
    language: str  # a Language value, or Language.OTHER for unrecognized kinds
    is_binary: bool = False


@dataclass(frozen=True, slots=True)
class LanguageStat:
    language: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class ExcludedDir:
    """Aggregate count of directories excluded by one built-in ignore rule."""

    name: str  # rule name, e.g. "node_modules", "__pycache__"
    hits: int


@dataclass(frozen=True, slots=True)
class InventorySummary:
    total_files: int
    total_bytes: int
    text_files: int
    binary_files: int
    languages: tuple[LanguageStat, ...]  # sorted by file_count desc, then name asc
    largest_files: tuple[FileEntry, ...]  # up to MAX_LARGEST_FILES, size desc, path asc
    ignored_by_gitignore: int = field(
        default=0, metadata={"volatile": True}  # counting granularity differs per backend
    )
    ignored_by_builtins: int = field(
        default=0, metadata={"volatile": True}  # same backend granularity caveat
    )
    excluded_dirs: tuple[ExcludedDir, ...] = field(
        default=(), metadata={"volatile": True}  # same backend granularity caveat
    )
    truncated: bool = False
    truncation_reasons: tuple[str, ...] = ()  # "max_files" | "max_depth" | "max_dirs" | "deadline"


@dataclass(frozen=True, slots=True)
class ManifestInfo:
    path: str
    kind: str  # "pyproject.toml" | "package.json" | "go.mod" | "lockfile" | ...
    ecosystem: str  # "python" | "node" | "go" | "rust" | "ruby" | "java" | "dotnet" | ...
    project_name: str | None = None
    parsed: bool = False  # False when unreadable, oversized, or malformed (see warnings)


@dataclass(frozen=True, slots=True)
class FrameworkHit:
    name: str  # canonical lowercase, e.g. "fastapi", "next"
    ecosystem: str
    source: str  # manifest path where the dependency was observed


@dataclass(frozen=True, slots=True)
class WorkspaceRoot:
    path: str  # directory (relative) containing the workspace manifest
    kind: str  # "npm" | "yarn" | "pnpm" | "cargo" | "uv" | "poetry"
    declared_members: tuple[str, ...]  # globs/paths exactly as declared
    resolved_members: tuple[str, ...]  # sorted member dirs found in the inventory


@dataclass(frozen=True, slots=True)
class TestLocation:
    path: str  # file or directory, relative to root
    kind: str  # "python-test-file" | "python-test-dir" | "node-test-file" | "node-test-dir"
    file_count: int = 1  # for directories: number of detected test files inside


@dataclass(frozen=True, slots=True)
class ProjectCommand:
    purpose: str  # "test" | "build" | "lint" | "format" | "check" | "run"
    command: str  # actionable command line, e.g. "pytest", "npm run build"
    source: str  # e.g. "package.json:scripts.test (packages/api)"


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    """Bounded-traversal controls. Defaults are safe for very large monorepos."""

    max_files: int = 100_000
    max_depth: int = 32
    max_manifest_bytes: int = 2 * 1024 * 1024
    max_gitignore_bytes: int = 256 * 1024
    deadline_seconds: float = 60.0
    follow_symlinks: bool = False  # directory symlinks are never followed


@dataclass(frozen=True, slots=True)
class ProfileStats:
    backend: str = field(
        default="filesystem", metadata={"volatile": True}  # "git" | "filesystem"
    )
    dirs_visited: int = field(default=0, metadata={"volatile": True})
    file_bodies_read: int = field(
        default=0,
        metadata={"volatile": True},  # git backend delegates ignore reading to git
    )
    symlink_entries: int = 0  # symlinks recorded as files without descending
    missing_entries: int = 0  # listed paths that no longer exist in the worktree
    elapsed_ms: int = field(default=0, metadata={"volatile": True})


@dataclass(frozen=True, slots=True)
class RepositoryProfile:
    """Deterministic first-pass repository profile produced before model planning."""

    schema_version: int
    root_path: str = field(default="", metadata={"volatile": True})  # as given by the caller
    repo_name: str = field(
        default="", metadata={"volatile": True}  # derived from the root dir name
    )
    is_git_repository: bool = False
    empty: bool = False  # no candidate files at all
    limits: InventoryLimits = field(default_factory=InventoryLimits)
    files: tuple[FileEntry, ...] = ()  # sorted by path
    summary: InventorySummary = field(
        default_factory=lambda: InventorySummary(
            total_files=0,
            total_bytes=0,
            text_files=0,
            binary_files=0,
            languages=(),
            largest_files=(),
            ignored_by_gitignore=0,
            ignored_by_builtins=0,
            excluded_dirs=(),
            truncated=False,
        )
    )
    manifests: tuple[ManifestInfo, ...] = ()  # sorted by path
    frameworks: tuple[FrameworkHit, ...] = ()  # sorted by (name, source)
    workspaces: tuple[WorkspaceRoot, ...] = ()  # sorted by path
    tests: tuple[TestLocation, ...] = ()  # sorted by (path, kind)
    commands: tuple[ProjectCommand, ...] = ()  # sorted by (purpose, command, source)
    tests_truncated: bool = False  # test-location list hit MAX_TEST_LOCATIONS
    warnings: tuple[str, ...] = field(default=(), metadata={"volatile": True})
    stats: ProfileStats = field(default_factory=ProfileStats)

    def fingerprint(self) -> str:
        """Stable content hash: identical unchanged input produces identical output."""
        payload = json.dumps(
            _volatile(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Full wire/JSON representation (volatile fields included)."""
        return _encode(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryProfile:
        if not isinstance(data, dict):  # pragma: no cover - defensive
            raise ProfileContractError("profile payload must be a JSON object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ProfileContractError(
                f"unsupported profile schema_version {version!r}; expected {SCHEMA_VERSION}"
            )
        hints = get_type_hints(cls)
        kwargs = {}
        for f in fields(cls):
            if f.name not in data:
                raise ProfileContractError(f"missing profile field {f.name!r}")
            kwargs[f.name] = _decode(hints[f.name], data[f.name])
        return cls(**kwargs)
