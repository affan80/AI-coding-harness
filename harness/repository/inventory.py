"""Bounded file inventory: tracked plus relevant untracked files, never bodies.

Two backends produce the same candidate-set semantics:

- ``git`` — ``git ls-files`` (cached + untracked, gitignore-excluded) when the
  root is a git work tree; fastest and most exact for git repositories.
- ``filesystem`` — sorted DFS walk with the pure-Python gitignore subset from
  :mod:`harness.repository.ignore`; used for non-git directories, when git is
  unavailable, or when git fails.

Both apply the built-in hard excludes on top of ``.gitignore``, so generated,
dependency, cache, and VCS directories can never enter the candidate set.
Traversal is bounded by candidate count, directory count, depth, and a
wall-clock deadline; hitting a bound truncates the inventory and is reported
on the result instead of being hidden.
"""

from __future__ import annotations

import os
import shutil
import stat as stat_module
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from harness.contracts.repository import FileEntry, InventoryLimits
from harness.repository.classify import classify_file
from harness.repository.ignore import (
    GitIgnoreSet,
    builtin_dir_rule,
    builtin_file_rule,
)

GITIGNORE_NAME = ".gitignore"
_MAX_WARNINGS = 50
_DEADLINE_CHECK_INTERVAL = 64  # check the clock every N directories


@dataclass
class InventoryResult:
    files: list[FileEntry] = field(default_factory=list)
    truncated: bool = False
    truncation_reasons: list[str] = field(default_factory=list)
    excluded_dirs: Counter[str] = field(default_factory=Counter)  # by rule name
    ignored_by_gitignore: int = 0
    ignored_by_builtins: int = 0
    dirs_visited: int = 0
    symlink_entries: int = 0
    missing_entries: int = 0
    file_bodies_read: int = 0  # .gitignore rule files; manifests counted during detection
    backend: str = "filesystem"
    warnings: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        if len(self.warnings) < _MAX_WARNINGS:
            self.warnings.append(message)

    def mark_truncated(self, reason: str) -> None:
        if reason not in self.truncation_reasons:
            self.truncation_reasons.append(reason)
        self.truncated = True


def build_inventory(
    root: Path,
    limits: InventoryLimits,
    *,
    backend: str = "auto",
    deadline: float | None = None,
) -> InventoryResult:
    """Build the candidate file inventory for *root*.

    ``backend`` selects ``git``, ``filesystem``, or ``auto`` (git when the root
    is a git work tree and git is installed, filesystem otherwise). ``deadline``
    is an absolute ``time.monotonic()`` cutoff shared across backend attempts.
    """
    if deadline is None:
        deadline = time.monotonic() + limits.deadline_seconds
    use_git = backend == "git" or (backend == "auto" and looks_like_git_repo(root))
    if not use_git:
        result = InventoryResult(backend="filesystem")
        _walk_filesystem(root, limits, result, deadline)
        return result

    if shutil.which("git") is None:
        result = InventoryResult(backend="filesystem")
        result.add_warning("git executable not found; using filesystem traversal")
        _walk_filesystem(root, limits, result, deadline)
        return result

    result = _walk_git(root, limits, deadline)
    if result.backend == "git":
        return result
    # git failed (not a repository, dubious ownership, timeout, ...): fall back.
    fallback = InventoryResult(backend="filesystem")
    first_warning = result.warnings[0] if result.warnings else "unknown error"
    fallback.add_warning(
        f"git inventory unavailable ({first_warning}); using filesystem traversal"
    )
    for warning in result.warnings:
        fallback.add_warning(f"git: {warning}")
    _walk_filesystem(root, limits, fallback, deadline)
    return fallback


def looks_like_git_repo(root: Path) -> bool:
    """True when *root* is a git work tree (``.git`` dir or work-tree file)."""
    dot_git = root / ".git"
    return dot_git.exists()  # directory (normal clone) or file (linked work tree)


# --- git backend --------------------------------------------------------------


def _walk_git(root: Path, limits: InventoryLimits, deadline: float) -> InventoryResult:
    result = InventoryResult(backend="git")
    tracked = _git_ls_files(root, ["--cached"], limits, result)
    if tracked is None:
        result.backend = "failed"
        return result
    untracked = _git_ls_files(root, ["--others", "--exclude-standard"], limits, result)
    if untracked is None:
        result.backend = "failed"
        return result

    seen: set[str] = set()
    for rel in tracked + untracked:
        if rel in seen:
            continue
        seen.add(rel)
        if len(result.files) >= limits.max_files:
            result.mark_truncated("max_files")
            return result
        if time.monotonic() >= deadline:
            result.mark_truncated("deadline")
            return result
        _record_git_entry(root, rel, result)

    # Best-effort ignore observability (counters only, never fatal).
    ignored = _git_ls_files(
        root, ["--others", "--ignored", "--exclude-standard", "--directory"], limits, result
    )
    if ignored is not None:
        for rel in ignored:
            if rel.endswith("/"):
                basename = rel.rstrip("/").rsplit("/", 1)[-1]
                rule = builtin_dir_rule(basename)
                result.excluded_dirs[rule or basename] += 1
            else:
                basename = rel.rsplit("/", 1)[-1]
                if builtin_file_rule(basename) is not None:
                    result.ignored_by_builtins += 1
                else:
                    result.ignored_by_gitignore += 1
    return result


def _git_ls_files(
    root: Path, args: list[str], limits: InventoryLimits, result: InventoryResult
) -> list[str] | None:
    timeout = max(1.0, limits.deadline_seconds)
    command = ["git", "-C", str(root), "ls-files", "-z", *args]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        result.add_warning(f"git ls-files {' '.join(args)} failed: {error}")
        return None
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        result.add_warning(
            f"git ls-files {' '.join(args)} exited {completed.returncode}: {detail[:200]}"
        )
        return None
    return [p for p in completed.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _record_git_entry(root: Path, rel: str, result: InventoryResult) -> None:
    segments = rel.split("/")
    # Built-in hard excludes also apply to tracked files: a committed
    # node_modules (or similar) must never reach the candidate set.
    for ancestor in segments[:-1]:
        rule = builtin_dir_rule(ancestor)
        if rule is not None:
            result.excluded_dirs[rule] += 1
            return
    name = segments[-1]
    if builtin_file_rule(name) is not None:
        result.ignored_by_builtins += 1
        return
    path = root / rel
    try:
        stat = path.lstat()
    except OSError:
        result.missing_entries += 1  # staged but deleted from the worktree
        return
    if stat_module.S_ISDIR(stat.st_mode):  # gitlink (submodule): not this repo's source
        result.add_warning(f"skipped submodule entry: {rel}")
        result.excluded_dirs[name] += 1
        return
    language, is_binary = classify_file(name)
    result.files.append(
        FileEntry(path=rel, size_bytes=stat.st_size, language=language, is_binary=is_binary)
    )


# --- filesystem backend -------------------------------------------------------


def _walk_filesystem(
    root: Path, limits: InventoryLimits, result: InventoryResult, deadline: float
) -> None:
    max_dirs = max(1024, limits.max_files * 4)
    ignores = GitIgnoreSet()
    _load_gitignore(root, "", ignores, result, limits)
    stack: list[tuple[str, int]] = [("", 0)]  # (relative dir, depth); root depth 0
    while stack:
        dir_rel, depth = stack.pop()
        result.dirs_visited += 1
        if result.dirs_visited > max_dirs:
            result.mark_truncated("max_dirs")
            return
        if result.dirs_visited == 1 or result.dirs_visited % _DEADLINE_CHECK_INTERVAL == 0:
            if time.monotonic() >= deadline:
                result.mark_truncated("deadline")
                return
        try:
            entries = sorted(_list_dir(root / dir_rel if dir_rel else root), key=lambda e: e.name)
        except OSError as error:
            result.add_warning(f"cannot list directory {dir_rel or '.'}: {error}")
            continue

        subdirs: list[str] = []
        for entry in entries:
            name = entry.name
            rel = f"{dir_rel}/{name}" if dir_rel else name
            if len(result.files) >= limits.max_files:
                result.mark_truncated("max_files")
                return
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_symlink = entry.is_symlink()
            except OSError as error:
                result.add_warning(f"stat failed for {rel}: {error}")
                continue
            if is_dir:
                rule = builtin_dir_rule(name)
                if rule is not None:
                    result.excluded_dirs[rule] += 1
                    continue
                if ignores.is_ignored(rel, is_dir=True):
                    result.ignored_by_gitignore += 1
                    continue
                # Files directly inside this directory would sit at depth+2.
                if depth + 2 > limits.max_depth:
                    result.mark_truncated("max_depth")
                    continue
                subdirs.append(rel)
                _load_gitignore(root, rel, ignores, result, limits)
                continue
            # Symlinks are recorded as entries (git lists them too) but never
            # followed, so cycles are impossible.
            if is_symlink:
                result.symlink_entries += 1
            rule = builtin_file_rule(name)
            if rule is not None:
                result.ignored_by_builtins += 1
                continue
            if ignores.is_ignored(rel, is_dir=False):
                result.ignored_by_gitignore += 1
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                result.add_warning(f"stat failed for {rel}: {error}")
                continue
            language, is_binary = classify_file(name)
            result.files.append(
                FileEntry(path=rel, size_bytes=stat.st_size, language=language, is_binary=is_binary)
            )

        # Push reversed so the alphabetically-first subtree is walked first:
        # depth-first order keeps max_files truncation deterministic.
        for rel in reversed(subdirs):
            stack.append((rel, depth + 1))


def _list_dir(path: Path) -> list[os.DirEntry[str]]:
    with os.scandir(path) as iterator:
        return list(iterator)


def _load_gitignore(
    root: Path,
    dir_rel: str,
    ignores: GitIgnoreSet,
    result: InventoryResult,
    limits: InventoryLimits,
) -> None:
    path = root / dir_rel / GITIGNORE_NAME if dir_rel else root / GITIGNORE_NAME
    try:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8", errors="replace")[: limits.max_gitignore_bytes]
    except OSError as error:
        result.add_warning(f"failed to read {dir_rel or '.'}/{GITIGNORE_NAME}: {error}")
        return
    result.file_bodies_read += 1
    ignores.add_rules(dir_rel, text)
