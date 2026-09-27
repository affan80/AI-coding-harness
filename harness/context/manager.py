import json
import math
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path


@dataclass
class ModelCapabilities:
    """Mock from Issue #6"""
    max_context_tokens: int
    max_output_tokens: int

class Priority(IntEnum):
    P0_CRITICAL = 0      # Objective, constraints, current step, active failure
    P1_PLAN = 1          # Execution plan
    P2_TARGET = 2        # Current target file L2 exact source
    P3_TESTS = 3         # Target test L2 exact source
    P4_SYMBOLS = 4       # Symbol declarations (L2)
    P5_SUMMARIES = 5     # L1 summaries for dependencies
    P6_METADATA = 6      # L0 metadata for repo
    P7_BACKGROUND = 7    # Low priority search candidates

class Level(Enum):
    L0_METADATA = "L0"
    L1_SUMMARY = "L1"
    L2_EXACT = "L2"

@dataclass
class ContextItem:
    id: str
    content: str
    priority: Priority
    level: Level
    reason: str
    tokens: int = field(init=False)

    def __post_init__(self):
        # Rough estimation: 1 token ~= 4 chars (as a fallback when tiktoken/etc isn't available)
        self.tokens = math.ceil(len(self.content) / 4)

@dataclass
class ContextMetrics:
    total_tokens: int = 0
    tokens_by_category: dict[str, int] = field(default_factory=dict)
    compaction_events: int = 0
    # Observability (issue #47; PRD §12.9): what was discovered versus what
    # the working set actually selected, plus what compaction/invalidation
    # removed and where the raw evidence was retained.
    discovered_tokens: int = 0
    selected_items: int = 0
    evicted_items: int = 0
    evicted_tokens: int = 0
    invalidated_items: int = 0

class ContextManager:
    """Token budgeting and priority enforcement (issue #45; PRD §§12.1-12.3).

    The safe budget is derived from the model capabilities with output
    headroom reserved: ``safe = (max_context - max_output) * safe_ratio``.
    P0-P2 items are non-evictable (objective, constraints, current step,
    active failure, plan, target source); compaction evicts strictly from
    P7 upward through P3 and never crosses the P2 boundary.

    Compaction triggers automatically as the selection approaches the safe
    budget when ``auto_compact`` is enabled (PRD §12.7's ~80% line is the
    safe budget itself); assembly-driven flows call :meth:`compact`
    explicitly. Evicted items are retained as raw evidence on disk when
    ``evidence_dir`` is set, and summaries for changed files can be
    invalidated by path (PRD §12.8).
    """

    # Priorities that compaction may never drop (PRD §12.3).
    NON_EVICTABLE_BELOW = Priority.P2_TARGET

    EVIDENCE_FILENAME = "context-evictions.jsonl"

    def __init__(
        self,
        capabilities: ModelCapabilities,
        safe_ratio: float = 0.8,
        reserve_output_headroom: bool = True,
        evidence_dir: Path | str | None = None,
        auto_compact: bool = False,
    ):
        self.capabilities = capabilities
        self.reserve_output_headroom = reserve_output_headroom
        budget_base = capabilities.max_context_tokens
        if reserve_output_headroom:
            budget_base = max(0, capabilities.max_context_tokens - capabilities.max_output_tokens)
        self.safe_capacity = int(budget_base * safe_ratio)
        self.items: list[ContextItem] = []
        self.metrics = ContextMetrics()
        self.metrics.tokens_by_category = {lvl.value: 0 for lvl in Level}
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.auto_compact = auto_compact
        self._eviction_sequence = 0
        self._last_evicted: list[ContextItem] = []

    def budget(self) -> int:
        """The safe token budget selected context may never exceed."""
        return self.safe_capacity

    def over_budget_tokens(self) -> int:
        """How far the current selection exceeds the safe budget (0 = within)."""
        return max(0, self.metrics.total_tokens - self.safe_capacity)

    def add_item(self, item: ContextItem):
        self.items.append(item)
        self._recalculate_metrics()
        if self.auto_compact and self.metrics.total_tokens >= self.safe_capacity:
            self.compact()

    def invalidate_by_id(self, item_id: str):
        """Remove an item (e.g., summary invalidated by a patch) so it can be re-read."""
        before = len(self.items)
        self.items = [i for i in self.items if i.id != item_id]
        removed = before - len(self.items)
        self.metrics.invalidated_items += removed
        self._recalculate_metrics()

    def invalidate_paths(self, paths) -> int:
        """Invalidate every cached item (L0/L1/L2) for the given repo paths.

        After a patch (PRD §12.8) the summaries and exact source for modified
        files must be dropped so the next assembly re-reads them; prior
        content is retained in the eviction evidence, not silently lost.
        Returns the number of items removed.
        """
        suffixes = tuple(f":{p}" for p in paths)
        before = len(self.items)
        removed_items = [i for i in self.items if i.id.endswith(suffixes)]
        self.items = [i for i in self.items if not i.id.endswith(suffixes)]
        removed = before - len(self.items)
        self.metrics.invalidated_items += removed
        if removed_items:
            self._write_eviction_evidence(removed_items)
        self._recalculate_metrics()
        return removed

    def _recalculate_metrics(self):
        self.metrics.total_tokens = sum(i.tokens for i in self.items)
        self.metrics.selected_items = len(self.items)
        self.metrics.tokens_by_category = {lvl.value: 0 for lvl in Level}
        for i in self.items:
            self.metrics.tokens_by_category[i.level.value] += i.tokens

    def compact(self) -> list[ContextItem]:
        """Enforce the safe budget with strict eviction order.

        P0-P2 are non-evictable. Everything else is evicted from the lowest
        priority first (P7 background, then P6 metadata, ... up through P3
        tests) until the selection fits the safe budget. Evicted items are
        retained as raw evidence on disk when ``evidence_dir`` is configured
        (PRD §12.7).
        """
        if self.metrics.total_tokens <= self.safe_capacity:
            return self.items

        self.metrics.compaction_events += 1

        protected = [i for i in self.items if i.priority <= self.NON_EVICTABLE_BELOW]
        evictable = sorted(
            (i for i in self.items if i.priority > self.NON_EVICTABLE_BELOW),
            key=lambda x: x.priority,
            reverse=True,  # worst priority evicted first
        )

        retained = list(protected)
        current_tokens = sum(i.tokens for i in protected)

        evicted: list[ContextItem] = []
        for item in evictable:
            if current_tokens + item.tokens <= self.safe_capacity:
                retained.append(item)
                current_tokens += item.tokens
            else:
                evicted.append(item)

        # Deterministic order for the assembled context.
        retained.sort(key=lambda x: x.priority)
        self.items = retained
        self._recalculate_metrics()
        self._last_evicted = evicted
        if evicted:
            self.metrics.evicted_items += len(evicted)
            self.metrics.evicted_tokens += sum(i.tokens for i in evicted)
            self._write_eviction_evidence(evicted)
        return self.items

    @property
    def last_evicted(self) -> list[ContextItem]:
        """Items dropped by the most recent compaction (raw evidence kept on disk)."""
        return list(self._last_evicted)

    def _write_eviction_evidence(self, evicted: list[ContextItem]) -> None:
        if self.evidence_dir is None:
            return
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        with open(self.evidence_dir / self.EVIDENCE_FILENAME, "a", encoding="utf-8") as fh:
            for item in evicted:
                self._eviction_sequence += 1
                fh.write(json.dumps({
                    "sequence": self._eviction_sequence,
                    "id": item.id,
                    "level": item.level.value,
                    "priority": int(item.priority),
                    "tokens": item.tokens,
                    "reason": item.reason,
                    "content": item.content,
                }) + "\n")

    def get_context(self) -> str:
        """Assemble the final context string for the model."""
        compacted = self.compact()
        return "\n\n".join(
            [f"[{i.level.value}] {i.id} ({i.reason}):\n{i.content}" for i in compacted]
        )
