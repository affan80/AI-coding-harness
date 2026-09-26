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
    def __init__(self, capabilities: ModelCapabilities, safe_ratio: float = 0.8):
        self.capabilities = capabilities
        self.safe_capacity = int(capabilities.max_context_tokens * safe_ratio)
        self.items: list[ContextItem] = []
        self.metrics = ContextMetrics()
        self.metrics.tokens_by_category = {lvl.value: 0 for lvl in Level}

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
        """
        Sort items by priority. Evict lowest priority items until within safe capacity.
        P0 items cannot be evicted even if they exceed capacity (though they shouldn't).
        """
        if self.metrics.total_tokens <= self.safe_capacity:
            return self.items

        self.metrics.compaction_events += 1

        # Sort items: P0 first (0), then P1 (1)...
        self.items.sort(key=lambda x: x.priority)

        retained = []
        current_tokens = 0

        for item in self.items:
            if item.priority == Priority.P0_CRITICAL:
                retained.append(item)
                current_tokens += item.tokens
            else:
                if current_tokens + item.tokens <= self.safe_capacity:
                    retained.append(item)
                    current_tokens += item.tokens

        self.items = retained
        self._recalculate_metrics()
        return self.items

    def get_context(self) -> str:
        """Assemble the final context string for the model."""
        compacted = self.compact()
        return "\n\n".join(
            [f"[{i.level.value}] {i.id} ({i.reason}):\n{i.content}" for i in compacted]
        )
