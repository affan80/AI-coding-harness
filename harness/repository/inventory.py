"""Bounded, deterministic file inventory for a repository root.

The walk never reads file bodies and never follows directory symlinks. Files
are described by relative path and stat size only, which keeps memory and I/O
proportional to the number of entries, not repository content volume.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .ignores import GITIGNORE_IGNORE_REASON, GitignoreStack, builtin_ignore_match
from .models import FileEntry


@dataclass
class InventoryOptions:
    max_files: int = 50_000
    max_depth: int = 32


@dataclass
class _ScanState:
    entries: list[FileEntry] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    total_dirs: int = 0
    ignored_paths: int = 0
    ignored_by_pattern: dict[str, int] = field(default_factory=dict)
    omitted_by_limit: int = 0


class _CapReached(Exception):
    pass


def inventory_repository(
    root: str | os.PathLike[str],
    options: InventoryOptions | None = None,
) -> InventoryResult:
    """Walk the repository and return the ignore-filtered inventory."""
    options = options or InventoryOptions()
    state = _ScanState()
    truncated = False
    try:
        _scan_dir(os.fspath(root), "", 0, GitignoreStack(), state, options)
    except _CapReached:
        truncated = True
    state.entries.sort(key=lambda e: e.path)
    return InventoryResult(
        entries=state.entries,
        total_files=state.total_files,
        total_bytes=state.total_bytes,
        total_dirs=state.total_dirs,
        ignored_paths=state.ignored_paths,
        ignored_by_pattern=dict(sorted(state.ignored_by_pattern.items())),
        omitted_by_limit=state.omitted_by_limit,
        truncated=truncated,
    )


def _scan_dir(
    abs_dir: str,
    rel_prefix: str,
    depth: int,
    stack: GitignoreStack,
    state: _ScanState,
    options: InventoryOptions,
) -> None:
    if depth > options.max_depth:
        state.omitted_by_limit += 1
        return
    try:
        with os.scandir(abs_dir) as it:
            children = sorted(it, key=lambda e: e.name)
    except OSError:
        # Unreadable directories are skipped, not fatal.
        return
    state.total_dirs += 1
    pushed = _push_gitignore(abs_dir, rel_prefix, stack)
    try:
        for entry in children:
            rel_path = rel_prefix + entry.name
            is_dir = entry.is_dir(follow_symlinks=False)
            matched = builtin_ignore_match(rel_path, is_dir)
            if matched is not None:
                state.ignored_paths += 1
                state.ignored_by_pattern[matched] = (
                    state.ignored_by_pattern.get(matched, 0) + 1
                )
                continue
            if stack.is_ignored(rel_path, is_dir):
                state.ignored_paths += 1
                state.ignored_by_pattern[GITIGNORE_IGNORE_REASON] = (
                    state.ignored_by_pattern.get(GITIGNORE_IGNORE_REASON, 0) + 1
                )
                continue
            if is_dir:
                _scan_dir(
                    entry.path,
                    rel_path + "/",
                    depth + 1,
                    stack,
                    state,
                    options,
                )
            else:
                # Includes symlinks to files and directories; they are listed
                # as entries (never followed) so the inventory stays complete.
                if len(state.entries) >= options.max_files:
                    raise _CapReached
                size = entry.stat(follow_symlinks=False).st_size
                state.total_files += 1
                state.entries.append(FileEntry(path=rel_path, size=size))
                state.total_bytes += size
    finally:
        if pushed:
            stack.pop()


def _push_gitignore(abs_dir: str, rel_prefix: str, stack: GitignoreStack) -> bool:
    gi_path = os.path.join(abs_dir, ".gitignore")
    if os.path.islink(gi_path) or not os.path.isfile(gi_path):
        return False
    try:
        with open(gi_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return False
    stack.push(rel_prefix, lines)
    return True
