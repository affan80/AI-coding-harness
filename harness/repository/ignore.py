"""Ignore handling: built-in excludes plus a .gitignore subset, with git semantics.

Built-in rules are *hard* excludes: generated, dependency, cache, run-artifact,
VCS, OS-junk, and secret-env paths never enter the candidate inventory, and a
``.gitignore`` cannot re-include them. ``.gitignore`` rules are then applied on
top with last-match-wins precedence, deeper files overriding shallower ones —
mirroring git's behavior for the subset of syntax the harness supports
(comments, negation, dir-only ``/`` suffix, anchoring, ``*``, ``?``, ``[...]``,
and ``**``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- built-in hard excludes -------------------------------------------------

# Directories excluded at any depth, by exact name.
BUILTIN_DIR_NAMES: frozenset[str] = frozenset(
    {
        # VCS internals
        ".git",
        ".hg",
        ".svn",
        ".jj",
        ".bzr",
        # dependency/dependency-store directories
        "node_modules",
        "bower_components",
        "jspm_packages",
        ".pnpm-store",
        ".yarn",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        ".eggs",
        ".gradle",
        ".terraform",
        # build outputs
        "dist",
        "build",
        "out",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".turbo",
        ".parcel-cache",
        "target",
        # caches
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".ipynb_checkpoints",
        ".hypothesis",
        ".cache",
        "htmlcov",
        "coverage",
        ".sass-cache",
        # IDE-private state
        ".idea",
        # harness run artifacts (mirrors the repository .gitignore "runs/")
        "runs",
    }
)

# Directory basename globs excluded at any depth.
BUILTIN_DIR_GLOBS: tuple[str, ...] = ("*.egg-info", "*.egg-link", ".stack-work")

# Individual files excluded at any depth, by exact name (OS junk, caches, secrets).
BUILTIN_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        ".coverage",
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
    }
)

# Individual file basename globs excluded at any depth (compiled artifacts).
BUILTIN_FILE_GLOBS: tuple[str, ...] = ("*.pyc", "*.pyo", "*.class", "*.pyd", "*.so")

_MAX_EXCLUDED_NAME_LEN = 120


def builtin_dir_rule(name: str) -> str | None:
    """Return the built-in rule name matching a directory basename, else None."""
    if name in BUILTIN_DIR_NAMES:
        return name
    for glob in BUILTIN_DIR_GLOBS:
        if _glob_match(glob, name):
            return glob
    return None


def builtin_file_rule(name: str) -> str | None:
    """Return the built-in rule name matching a file basename, else None."""
    if name in BUILTIN_FILE_NAMES:
        return name
    for glob in BUILTIN_FILE_GLOBS:
        if _glob_match(glob, name):
            return glob
    return None


def _glob_match(glob: str, name: str) -> bool:
    """fnmatch-style match restricted to a single path segment."""
    return re.fullmatch(_translate_segment(glob), name) is not None


# --- .gitignore subset -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool
    pattern: str  # original text, for observability (capped)


class GitIgnoreSet:
    """Ordered .gitignore rules keyed by the directory that declares them."""

    __slots__ = ("_rules_by_dir",)

    def __init__(self) -> None:
        self._rules_by_dir: dict[str, list[_Rule]] = {}

    def add_rules(self, dir_rel: str, text: str) -> int:
        """Register rules from one .gitignore located in *dir_rel*; returns count."""
        rules = self._rules_by_dir.setdefault(dir_rel, [])
        added = 0
        for raw_line in text.splitlines():
            rule = _compile_line(raw_line)
            if rule is not None:
                rules.append(rule)
                added += 1
        return added

    def is_ignored(self, rel_path: str, is_dir: bool) -> bool:
        """Git precedence: deeper .gitignore files win; last match wins per file."""
        verdict: bool | None = None
        parts = rel_path.split("/")
        # Chains of declaring directories from the root down to the entry's parent.
        prefixes = [""]
        current = ""
        for part in parts[:-1]:
            current = f"{current}/{part}" if current else part
            prefixes.append(current)
        for prefix in prefixes:
            rules = self._rules_by_dir.get(prefix)
            if not rules:
                continue
            local = _relative_to(prefix, rel_path)
            for rule in rules:
                if rule.dir_only and not is_dir:
                    continue
                if rule.regex.fullmatch(local):
                    verdict = not rule.negated
        return verdict is True


def _relative_to(prefix: str, rel_path: str) -> str:
    if not prefix:
        return rel_path
    if rel_path.startswith(prefix + "/"):
        return rel_path[len(prefix) + 1 :]
    return rel_path


def _compile_line(raw_line: str) -> _Rule | None:
    line = raw_line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    # Strip unescaped trailing whitespace (git trims it unless backslash-escaped).
    while line.endswith(" ") and not line.endswith("\\ "):
        line = line[:-1]
    if not line:
        return None
    negated = line.startswith("!")
    if negated:
        line = line[1:]
    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]
    if not line:
        return None
    anchored = "/" in line
    if line.startswith("/"):
        line = line[1:]
    if not line:
        return None
    regex = _translate_pattern(line, anchored=anchored)
    return _Rule(
        regex=regex,
        negated=negated,
        dir_only=dir_only,
        pattern=line[:_MAX_EXCLUDED_NAME_LEN],
    )


def _translate_pattern(pattern: str, anchored: bool) -> re.Pattern[str]:
    """Translate one gitignore pattern body into an anchored regex.

    ``**`` semantics per git: leading ``**/`` matches at any depth, trailing
    ``/**`` matches everything inside, middle ``/**/`` matches zero or more
    directories.
    """
    segments = pattern.split("/")
    body = ""
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        # A lone "**" (no slashes around it) behaves like a regular "*" per git.
        if segment == "**" and not (index == 0 and last):
            if last:
                body += r".*"
            else:
                # Leading or middle "**" absorbs the following separator and
                # may also match zero directories.
                body += r"(?:[^/]+/)*"
                continue
        else:
            body += _translate_segment(segment)
            if not last:
                body += "/"
    if not anchored and "**" not in segments:
        body = r"(?:[^/]+/)*" + body
    return re.compile(body)


def _translate_segment(segment: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "\\" and index + 1 < len(segment):
            out.append(re.escape(segment[index + 1]))
            index += 2
            continue
        if char == "*":
            out.append(r"[^/]*")
        elif char == "?":
            out.append(r"[^/]")
        elif char == "[":
            end = segment.find("]", index + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = segment[index + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                index = end
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)
