"""RepositoryProfiler: assemble the deterministic :class:`RepositoryProfile`.

This is the entry point Workstream A's orchestrator calls during
``INSPECT_REPOSITORY``: given a repository path it produces the L0 profile
(paths, sizes, languages, manifests, frameworks, workspaces, tests, commands)
without reading source bodies, so profiling cost is proportional to file count,
not repository bytes. See PRD §§12.5, 13.
"""

from __future__ import annotations

import time
from pathlib import Path

from harness.contracts.repository import (
    SCHEMA_VERSION,
    ExcludedDir,
    FileEntry,
    InventoryLimits,
    InventorySummary,
    LanguageStat,
    ProfileStats,
    RepositoryProfile,
)
from harness.repository.detect import detect_structure
from harness.repository.errors import RepositoryProfileError
from harness.repository.inventory import InventoryResult, build_inventory, looks_like_git_repo

_MAX_WARNINGS = 50


class RepositoryProfiler:
    """Profiles one repository per call; instances are stateless and reusable."""

    def __init__(
        self,
        limits: InventoryLimits | None = None,
        *,
        backend: str = "auto",
    ) -> None:
        if backend not in {"auto", "git", "filesystem"}:
            raise ValueError(f"unknown inventory backend {backend!r}")
        self.limits = limits or InventoryLimits()
        self.backend = backend

    def profile(self, root: str | Path) -> RepositoryProfile:
        """Profile the repository at *root*; raises :class:`RepositoryProfileError`
        when the path is missing or not a directory."""
        given = Path(root)
        if not given.exists():
            raise RepositoryProfileError("root-not-found", str(given))
        if not given.is_dir():
            raise RepositoryProfileError("root-not-a-directory", str(given))
        resolved = given.resolve()

        started = time.monotonic()
        inventory = build_inventory(given, self.limits, backend=self.backend)
        detection = detect_structure(given, inventory.files, self.limits)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        files = sorted(inventory.files, key=lambda entry: entry.path)
        warnings = _combine_warnings(inventory, detection)

        return RepositoryProfile(
            schema_version=SCHEMA_VERSION,
            root_path=str(given),
            repo_name=resolved.name or str(resolved),
            is_git_repository=looks_like_git_repo(given),
            empty=not files,
            limits=self.limits,
            files=tuple(files),
            summary=_build_summary(files, inventory),
            manifests=tuple(detection.manifests),
            frameworks=tuple(detection.frameworks),
            workspaces=tuple(detection.workspaces),
            tests=tuple(detection.tests),
            commands=tuple(detection.commands),
            tests_truncated=detection.tests_truncated,
            warnings=warnings,
            stats=_build_stats(inventory, detection, elapsed_ms),
        )


def profile_repository(
    root: str | Path,
    limits: InventoryLimits | None = None,
    *,
    backend: str = "auto",
) -> RepositoryProfile:
    """Convenience wrapper around :class:`RepositoryProfiler`."""
    return RepositoryProfiler(limits, backend=backend).profile(root)


def _build_summary(files: list[FileEntry], inventory: InventoryResult) -> InventorySummary:
    counts: dict[str, int] = {}
    bytes_by_language: dict[str, int] = {}
    total_bytes = 0
    text_files = 0
    binary_files = 0
    for entry in files:
        counts[entry.language] = counts.get(entry.language, 0) + 1
        bytes_by_language[entry.language] = (
            bytes_by_language.get(entry.language, 0) + entry.size_bytes
        )
        total_bytes += entry.size_bytes
        if entry.is_binary:
            binary_files += 1
        else:
            text_files += 1
    languages = tuple(
        LanguageStat(
            language=language,
            file_count=counts[language],
            total_bytes=bytes_by_language[language],
        )
        for language in sorted(counts, key=lambda name: (-counts[name], name))
    )
    largest = tuple(sorted(files, key=lambda entry: (-entry.size_bytes, entry.path))[:10])
    excluded = tuple(
        ExcludedDir(name=name, hits=hits)
        for name, hits in sorted(
            inventory.excluded_dirs.items(), key=lambda item: (-item[1], item[0])
        )
    )
    return InventorySummary(
        total_files=len(files),
        total_bytes=total_bytes,
        text_files=text_files,
        binary_files=binary_files,
        languages=languages,
        largest_files=largest,
        ignored_by_gitignore=inventory.ignored_by_gitignore,
        ignored_by_builtins=inventory.ignored_by_builtins,
        excluded_dirs=excluded,
        truncated=inventory.truncated,
        truncation_reasons=tuple(inventory.truncation_reasons),
    )


def _combine_warnings(inventory: InventoryResult, detection) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for warning in inventory.warnings + detection.warnings:
        seen.setdefault(warning)
    combined = list(seen)[:_MAX_WARNINGS]
    overflow = len(inventory.warnings) + len(detection.warnings) - len(combined)
    if overflow > 0:
        combined.append(f"...and {overflow} more warnings")
    return tuple(combined)


def _build_stats(inventory: InventoryResult, detection, elapsed_ms: int) -> ProfileStats:
    return ProfileStats(
        backend=inventory.backend,
        dirs_visited=inventory.dirs_visited,
        file_bodies_read=inventory.file_bodies_read + detection.file_bodies_read,
        symlink_entries=inventory.symlink_entries,
        missing_entries=inventory.missing_entries,
        elapsed_ms=elapsed_ms,
    )
