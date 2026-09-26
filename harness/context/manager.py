import math
from dataclasses import dataclass, field
from enum import Enum, IntEnum


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

class ContextManager:
    """Token budgeting and priority enforcement (issue #45; PRD §§12.1-12.3).

    The safe budget is derived from the model capabilities with output
    headroom reserved: ``safe = (max_context - max_output) * safe_ratio``.
    P0-P2 items are non-evictable (objective, constraints, current step,
    active failure, plan, target source); compaction evicts strictly from
    P7 upward through P3 and never crosses the P2 boundary.
    """

    # Priorities that compaction may never drop (PRD §12.3).
    NON_EVICTABLE_BELOW = Priority.P2_TARGET

    def __init__(
        self,
        capabilities: ModelCapabilities,
        safe_ratio: float = 0.8,
        reserve_output_headroom: bool = True,
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

    def budget(self) -> int:
        """The safe token budget selected context may never exceed."""
        return self.safe_capacity

    def over_budget_tokens(self) -> int:
        """How far the current selection exceeds the safe budget (0 = within)."""
        return max(0, self.metrics.total_tokens - self.safe_capacity)

    def add_item(self, item: ContextItem):
        self.items.append(item)
        self._recalculate_metrics()

    def invalidate_by_id(self, item_id: str):
        """Remove an item (e.g., summary invalidated by a patch) so it can be re-read."""
        self.items = [i for i in self.items if i.id != item_id]
        self._recalculate_metrics()

    def _recalculate_metrics(self):
        self.metrics.total_tokens = sum(i.tokens for i in self.items)
        self.metrics.tokens_by_category = {lvl.value: 0 for lvl in Level}
        for i in self.items:
            self.metrics.tokens_by_category[i.level.value] += i.tokens

    def compact(self) -> list[ContextItem]:
        """Enforce the safe budget with strict eviction order.

        P0-P2 are non-evictable. Everything else is evicted from the lowest
        priority first (P7 background, then P6 metadata, ... up through P3
        tests) until the selection fits the safe budget.
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
        return self.items

    def get_context(self) -> str:
        """Assemble the final context string for the model."""
        compacted = self.compact()
        return "\n\n".join(
            [f"[{i.level.value}] {i.id} ({i.reason}):\n{i.content}" for i in compacted]
        )
