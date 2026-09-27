"""Ranking and progressive context assembly (issue #46; PRD §§12.4-12.6; FR-05).

Scores repository candidates against the current goal, active failure,
symbols, dependency edges, test relationships, and recency — then assembles
them progressively into a :class:`ContextManager`: L0 metadata for the scan,
L1 summaries for promising candidates, L2 exact source only for the top
ranks. Every selected item records its estimated tokens and a human-readable
selection reason explaining why it entered the working set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from harness.context.manager import (
    ContextItem,
    ContextManager,
    Level,
    Priority,
)

_SUMMARY_CHARS = 400
_PREVIEW_CHARS = 2_000


@dataclass(frozen=True)
class Candidate:
    """One repository file candidate for the working set."""

    path: str
    summary: str = ""  # L1: purpose or key interfaces (may be empty)
    source: str = ""  # L2: exact source text (read lazily by the caller)
    is_test: bool = False


@dataclass
class AssembledContext:
    """Result of one progressive assembly pass."""

    selected: list[ContextItem] = field(default_factory=list)
    unselected: list[Candidate] = field(default_factory=list)

    def reasons(self) -> list[str]:
        return [item.reason for item in self.selected]


def _lexical_terms(text: str) -> set[str]:
    return {term.lower() for term in re_split(text) if len(term) > 2}


def re_split(text: str) -> list[str]:
    import re

    return re.split(r"[^a-zA-Z0-9_]+", text)


def score_candidate(
    candidate: Candidate,
    *,
    goal_text: str = "",
    failure_text: str = "",
    dependency_neighbors: Mapping[str, int] | None = None,
    test_targets: Mapping[str, int] | None = None,
    recent_paths: Mapping[str, int] | None = None,
) -> tuple[int, str]:
    """Score one candidate; return (score, human-readable reason)."""
    score = 0
    reasons: list[str] = []
    terms = _lexical_terms(f"{goal_text} {failure_text}")
    haystack = _lexical_terms(
        f"{candidate.path} {candidate.summary} {candidate.source[:_PREVIEW_CHARS]}"
    )

    overlap = terms & haystack
    if overlap:
        score += 30 * len(overlap)
        reasons.append(f"matches goal terms: {sorted(overlap)[:3]}")

    if candidate.is_test:
        score += 10
        reasons.append("test file for affected source")

    neighbors = dependency_neighbors or {}
    if candidate.path in neighbors:
        score += 20 * min(neighbors[candidate.path], 3)
        reasons.append("dependency of selected files")

    tests = test_targets or {}
    if candidate.path in tests:
        score += 25 * min(tests[candidate.path], 2)
        reasons.append("has failing/affected tests")

    recent = recent_paths or {}
    if candidate.path in recent:
        score += 15
        reasons.append("recently edited")

    if not reasons:
        score += 1
        reasons.append("repository scan")
    return score, "; ".join(reasons)


def assemble_context(
    manager: ContextManager,
    root: Path | str,
    candidates: list[Candidate],
    *,
    goal_text: str = "",
    failure_text: str = "",
    dependency_neighbors: Mapping[str, int] | None = None,
    test_targets: Mapping[str, int] | None = None,
    recent_paths: Mapping[str, int] | None = None,
    l2_slots: int = 3,
) -> AssembledContext:
    """Progressive disclosure: L0 scan → L1 summaries → L2 exact top ranks."""
    root = Path(root)
    scored: list[tuple[int, str, Candidate]] = []
    for candidate in candidates:
        score, reason = score_candidate(
            candidate,
            goal_text=goal_text,
            failure_text=failure_text,
            dependency_neighbors=dependency_neighbors,
            test_targets=test_targets,
            recent_paths=recent_paths,
        )
        scored.append((score, reason, candidate))
    scored.sort(key=lambda entry: entry[0], reverse=True)

    assembled = AssembledContext()
    # Discovered size (PRD §12.9): everything the scan saw — candidate text
    # plus, for lazily loaded files, their on-disk size (stat only, no read)
    # — estimated with the same 4-chars-per-token heuristic the manager
    # uses, so metrics can report selected-versus-discovered size.
    def _discovered_tokens(candidate: Candidate) -> int:
        chars = len(candidate.path) + len(candidate.summary) + len(candidate.source)
        if not candidate.source:
            disk = root / candidate.path
            try:
                chars += disk.stat().st_size
            except OSError:
                pass
        return max(1, (chars + 3) // 4)

    manager.metrics.discovered_tokens = sum(
        _discovered_tokens(c) for c in candidates
    )
    # L0: metadata for the whole bounded scan.
    for score, reason, candidate in scored:
        manager.add_item(ContextItem(
            id=f"l0:{candidate.path}",
            content=f"{candidate.path} ({len(candidate.source)} bytes)",
            priority=Priority.P6_METADATA,
            level=Level.L0_METADATA,
            reason=f"L0 scan; score {score}; {reason}",
        ))

    # L1: summaries for candidates that scored above the scan floor.
    promoted = [entry for entry in scored if entry[0] > 1]
    for score, reason, candidate in promoted:
        if not candidate.summary:
            continue
        manager.add_item(ContextItem(
            id=f"l1:{candidate.path}",
            content=candidate.summary[:_SUMMARY_CHARS],
            priority=Priority.P5_SUMMARIES,
            level=Level.L1_SUMMARY,
            reason=f"L1 summary; score {score}; {reason}",
        ))

    # L2: exact source for the top ranks, read lazily and bounded.
    for score, reason, candidate in promoted[:l2_slots]:
        source = candidate.source
        if not source:
            path = root / candidate.path
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        # Bind the item before adding: add_item may auto-compact, which
        # reorders (and evicts from) manager.items, so items[-1] is unsafe.
        l2_item = ContextItem(
            id=f"l2:{candidate.path}",
            content=source[:_PREVIEW_CHARS],
            priority=Priority.P2_TARGET if not candidate.is_test else Priority.P3_TESTS,
            level=Level.L2_EXACT,
            reason=f"L2 exact; score {score}; {reason}",
        )
        manager.add_item(l2_item)
        assembled.selected.append(l2_item)

    selected_ids = {item.id for item in assembled.selected}
    assembled.unselected = [
        candidate
        for _score, _reason, candidate in scored
        if f"l2:{candidate.path}" not in selected_ids
    ]
    # The manager compacts on assembly: within budget, P0-P2 intact.
    manager.compact()
    assembled.selected = list(manager.items)
    return assembled
