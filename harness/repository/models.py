"""Serializable data contracts for repository inventory and profiling.

These are the Person B output contracts named by issue #4; they follow the
dataclass + ``to_dict``/``from_dict`` conventions the shared models (issue #1)
use, so the evidence store can persist them unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class FileEntry:
    """One inventoried file. Metadata only — file bodies are never read."""

    path: str  # posix-style path relative to the repository root
    size: int  # bytes, from stat


@dataclass(frozen=True)
class Manifest:
    kind: str  # manifest file name, e.g. "pyproject.toml"
    path: str


@dataclass(frozen=True)
class WorkspaceRoot:
    """A directory acting as a project/workspace root."""

    path: str  # "." for the repository root
    kind: str  # "python" | "node"


@dataclass(frozen=True)
class TestLocation:
    path: str
    file_count: int


@dataclass(frozen=True)
class LanguageStat:
    language: str
    files: int
    total_bytes: int


@dataclass
class InventoryResult:
    """Outcome of one bounded inventory walk."""

    entries: list[FileEntry]
    total_files: int
    total_bytes: int
    total_dirs: int
    ignored_paths: int
    ignored_by_pattern: dict[str, int]
    omitted_by_limit: int
    truncated: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> InventoryResult:
        return cls(
            entries=[FileEntry(**e) for e in data["entries"]],
            total_files=data["total_files"],
            total_bytes=data["total_bytes"],
            total_dirs=data["total_dirs"],
            ignored_paths=data["ignored_paths"],
            ignored_by_pattern=dict(data["ignored_by_pattern"]),
            omitted_by_limit=data["omitted_by_limit"],
            truncated=data["truncated"],
        )


@dataclass
class RepositoryProfile:
    """Deterministic first-pass profile of a repository root."""

    root: str
    files: list[FileEntry] = field(default_factory=list)
    languages: list[LanguageStat] = field(default_factory=list)
    manifests: list[Manifest] = field(default_factory=list)
    workspaces: list[WorkspaceRoot] = field(default_factory=list)
    test_locations: list[TestLocation] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    monorepo: bool = False
    total_files: int = 0
    total_bytes: int = 0
    total_dirs: int = 0
    ignored_paths: int = 0
    ignored_by_pattern: dict[str, int] = field(default_factory=dict)
    omitted_by_limit: int = 0
    truncated: bool = False
    fingerprint: str = ""  # sha256 of the stable content; excludes root path

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RepositoryProfile:
        return cls(
            root=data["root"],
            files=[FileEntry(**e) for e in data["files"]],
            languages=[LanguageStat(**e) for e in data["languages"]],
            manifests=[Manifest(**e) for e in data["manifests"]],
            workspaces=[WorkspaceRoot(**e) for e in data["workspaces"]],
            test_locations=[TestLocation(**e) for e in data["test_locations"]],
            frameworks=list(data["frameworks"]),
            commands=dict(data["commands"]),
            monorepo=data["monorepo"],
            total_files=data["total_files"],
            total_bytes=data["total_bytes"],
            total_dirs=data["total_dirs"],
            ignored_paths=data["ignored_paths"],
            ignored_by_pattern=dict(data["ignored_by_pattern"]),
            omitted_by_limit=data["omitted_by_limit"],
            truncated=data["truncated"],
            fingerprint=data["fingerprint"],
        )
