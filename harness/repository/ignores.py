"""Ignore handling: built-in hard exclusions plus a pragmatic .gitignore subset.

Built-in patterns are hard rules: matching paths never enter the inventory and
``!`` negation cannot re-include them (PRD FR-04). Supported .gitignore
semantics: blank lines, ``#`` comments, ``!`` negation with last-match-wins,
trailing-``/`` directory-only patterns, anchoring for patterns containing a
slash, ``*``/``?`` globs that do not cross ``/``, and ``**`` segments. Nested
.gitignore files override shallower ones. Escaped trailing spaces and exotic
character-class edge cases are out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Categories per PRD §13: VCS metadata, dependencies, caches, build outputs,
# run artifacts, and common OS noise.
BUILTIN_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git/",
    ".git",
    ".svn/",
    ".hg/",
    "node_modules/",
    "bower_components/",
    ".pnpm-store/",
    "venv/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".nox/",
    ".tox/",
    ".cache/",
    "build/",
    "dist/",
    "out/",
    "target/",
    ".next/",
    ".nuxt/",
    ".output/",
    ".turbo/",
    ".parcel-cache/",
    "coverage/",
    ".nyc_output/",
    "runs/",
    "artifacts/",
    "*.egg-info/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    ".coverage",
)

GITIGNORE_IGNORE_REASON = "gitignore"


@dataclass(frozen=True)
class IgnorePattern:
    """One compiled ignore rule."""

    source: str
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool

    def matches(self, rel_path: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        return self.regex.match(rel_path) is not None


def compile_ignore_line(line: str) -> IgnorePattern | None:
    """Compile one ignore-pattern line; return None for blank lines/comments."""
    if line.startswith(("\\#", "\\!")):
        line = line[1:]
    if not line or line.startswith("#"):
        return None
    negated = line.startswith("!")
    if negated:
        line = line[1:]
    dir_only = line.endswith("/")
    line = line.rstrip("/")
    if not line:
        return None
    # A slash anywhere in the remaining pattern anchors it to the directory
    # declaring the rule; a bare name matches at any depth below it.
    anchored = "/" in line
    if line.startswith("/"):
        line = line.lstrip("/")
        anchored = True
    if not line:
        return None
    regex = re.compile(_translate(line, anchored))
    return IgnorePattern(source=line, regex=regex, negated=negated, dir_only=dir_only)


def _translate(pattern: str, anchored: bool) -> str:
    body: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 3] == "**/":
                body.append("(?:[^/]+/)*")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                body.append(".*")
                i += 2
                continue
            body.append("[^/]*")
            i += 1
        elif ch == "?":
            body.append("[^/]")
            i += 1
        elif ch == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                body.append(re.escape(ch))
                i += 1
            else:
                body.append(pattern[i : end + 1])
                i = end + 1
        else:
            body.append(re.escape(ch))
            i += 1
    core = "".join(body)
    if anchored:
        return f"^{core}$"
    return f"^(?:.*/)?{core}$"


_BUILTIN_PATTERNS: list[IgnorePattern] = [
    p for p in (compile_ignore_line(line) for line in BUILTIN_IGNORE_PATTERNS) if p
]


def builtin_ignore_match(rel_path: str, is_dir: bool) -> str | None:
    """Return the built-in pattern source matching the path, if any."""
    for pattern in _BUILTIN_PATTERNS:
        if pattern.matches(rel_path, is_dir):
            return pattern.source
    return None


class GitignoreStack:
    """Active .gitignore rules from the root down to the current directory.

    Layers are pushed root-first, so deeper files evaluate later and win
    ties, mirroring git precedence. Last matching pattern decides, honoring
    ``!`` negation.
    """

    def __init__(self) -> None:
        self._layers: list[tuple[str, list[IgnorePattern]]] = []

    def push(self, base_prefix: str, lines: list[str]) -> None:
        patterns = [p for p in (compile_ignore_line(ln) for ln in lines) if p]
        self._layers.append((base_prefix, patterns))

    def pop(self) -> None:
        self._layers.pop()

    def is_ignored(self, rel_path: str, is_dir: bool) -> bool:
        excluded = False
        for base_prefix, patterns in self._layers:
            local = _scope_to_base(rel_path, base_prefix)
            if local is None:
                continue
            for pattern in patterns:
                if pattern.matches(local, is_dir):
                    excluded = not pattern.negated
        return excluded

    def layer_count(self) -> int:
        return len(self._layers)


def _scope_to_base(rel_path: str, base_prefix: str) -> str | None:
    """Trim rel_path to be relative to a .gitignore's directory, or None."""
    if not base_prefix:
        return rel_path
    if rel_path.startswith(base_prefix):
        return rel_path[len(base_prefix) :]
    return None
