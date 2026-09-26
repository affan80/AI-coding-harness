"""Dependency and test-to-source mapping (issue #44; PRD §§12.4, 13; FR-05).

Lightweight, deterministic edges — no AST parsing, no file bodies beyond the
candidate sources:

- import edges: ``import x`` / ``from x import y`` mapped onto repository
  files (``x.py``, ``x/__init__.py``), one relationship per edge;
- test mapping: a test file relates to a source file when it imports it,
  shares its name stem, or sits beside it under a ``tests/`` root.

Every relationship carries a human-readable ``reason``, and candidates are
drawn from the bounded inventory so ignored/generated content is excluded
before any mapping runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from harness.repository.ignore import builtin_dir_rule, builtin_file_rule
from harness.repository.inventory import InventoryLimits, build_inventory

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(?P<module>[\w.]+)\s+import\s+(?P<names>[\w\s,.()*]+)"
    r"|import\s+(?P<plain>[\w.]+))",
)
MAX_FILE_BYTES = 512 * 1024


@dataclass(frozen=True)
class Relation:
    """One explained candidate relationship (issue #44)."""

    source: str  # repository-relative path
    target: str  # repository-relative path
    kind: str  # "import" | "test_of"
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass
class DependencyMap:
    """All explained edges plus the per-file adjacency index."""

    relations: list[Relation] = field(default_factory=list)
    imports_by_file: dict[str, list[str]] = field(default_factory=dict)
    tests_by_source: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "relations": [r.to_dict() for r in self.relations],
            "imports_by_file": self.imports_by_file,
            "tests_by_source": self.tests_by_source,
        }


def _candidate_files(root: Path, limits: InventoryLimits | None = None) -> list[str]:
    """Bounded inventory paths — ignored/generated content never enters."""
    result = build_inventory(root, limits or InventoryLimits())
    return [entry.path for entry in result.files]


def _is_ignored(name: str) -> bool:
    return builtin_dir_rule(name) is not None or builtin_file_rule(name) is not None


def _resolve_module(module: str, py_files: set[str]) -> str | None:
    """Map ``a.b.c`` onto repo files ``a/b/c.py`` or ``a/b/c/__init__.py``."""
    parts = module.split(".")
    as_file = "/".join(parts) + ".py"
    if as_file in py_files:
        return as_file
    as_package = "/".join(parts) + "/__init__.py"
    if as_package in py_files:
        return as_package
    # partial match: ``from app import auth`` where app/auth.py exists
    prefix = "/".join(parts)
    matches = [f for f in py_files if f.startswith(prefix + "/") and f != as_package]
    if len(matches) == 1:
        return matches[0]
    return None


def _import_edges(
    root: Path, py_files: list[str]
) -> tuple[list[Relation], dict[str, list[str]]]:
    relations: list[Relation] = []
    imports: dict[str, list[str]] = {}
    py_file_set = set(py_files)
    for rel in py_files:
        path = root / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        module_targets: list[str] = []
        for line in text.splitlines():
            match = _IMPORT_RE.match(line)
            if not match:
                continue
            module = match.group("module") or ""
            plain = match.group("plain") or ""
            candidates: list[str] = []
            if module:
                # ``from pkg import name`` prefers pkg/name.py over pkg/__init__.py
                names = [
                    n.strip().split(" as ")[0].strip()
                    for n in (match.group("names") or "").split(",")
                ]
                for name in names:
                    if name and name != "*":
                        candidates.append(f"{module}.{name}")
                candidates.append(module)
            else:
                candidates.append(plain)
            target = None
            for candidate in candidates:
                target = _resolve_module(candidate, py_file_set)
                if target is not None:
                    break
            if target is None or target == rel:
                continue
            reason = f"imports {module!r} (resolved to {target})"
            relations.append(Relation(source=rel, target=target, kind="import",
                                      reason=reason))
            module_targets.append(target)
        if module_targets:
            imports[rel] = sorted(set(module_targets))
    return relations, imports


def _is_test_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.go") or bool(
        re.search(r"\.(test|spec)\.[a-z]+$", name)
    )


def _source_stem(test_path: str) -> str:
    name = Path(test_path).name
    for prefix in ("test_",):
        if name.startswith(prefix):
            return Path(name[: -len(".py")]).stem[len(prefix):] if name.endswith(
                ".py"
            ) else name[len(prefix):]
    return Path(name).stem


def _test_edges(
    root: Path,
    py_files: list[str],
    imports: dict[str, list[str]],
) -> tuple[list[Relation], dict[str, list[str]]]:
    relations: list[Relation] = []
    tests_by_source: dict[str, list[str]] = {}
    py_file_set = set(py_files)
    for test_file in (f for f in py_files if _is_test_file(f)):
        stem = _source_stem(test_file)
        targets: set[str] = set()

        # 1. direct import of the module under test
        for target in imports.get(test_file, []):
            if Path(target).stem == stem:
                targets.add(target)
                relations.append(Relation(
                    source=test_file, target=target, kind="test_of",
                    reason=f"test imports {target}",
                ))

        # 2. name match on the stem
        stem_matches = {
            f for f in py_file_set
            if not _is_test_file(f) and Path(f).stem == stem
        }
        for target in sorted(stem_matches - targets):
            targets.add(target)
            relations.append(Relation(
                source=test_file, target=target, kind="test_of",
                reason=f"test name matches source stem {stem!r}",
            ))

        # 3. co-located convention: tests/test_x.py mirrors src/test_x.py
        if not targets:
            for candidate in py_file_set:
                if _is_test_file(candidate):
                    continue
                if Path(candidate).name == Path(test_file).name.replace(
                    "test_", ""
                ) and "tests/" in test_file:
                    targets.add(candidate)
                    relations.append(Relation(
                        source=test_file, target=candidate, kind="test_of",
                        reason="tests/ mirror of the source file name",
                    ))

        for target in sorted(targets):
            tests_by_source.setdefault(target, []).append(test_file)
    return relations, tests_by_source


def map_dependencies_and_tests(
    root: str | Path, limits: InventoryLimits | None = None
) -> DependencyMap:
    """Build the explained dependency and test mapping for a repository."""
    root = Path(root)
    files = [
        f for f in _candidate_files(root, limits)
        if f.endswith(".py") and not _is_ignored(Path(f).name)
    ]
    relations, imports = _import_edges(root, files)
    test_relations, tests_by_source = _test_edges(root, files, imports)
    relations.extend(test_relations)
    return DependencyMap(
        relations=relations, imports_by_file=imports,
        tests_by_source=tests_by_source,
    )
