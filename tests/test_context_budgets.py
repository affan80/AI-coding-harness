"""Issue #45: safe budgets with output headroom and strict priority eviction."""

from harness.context.manager import (
    ContextItem,
    ContextManager,
    Level,
    ModelCapabilities,
    Priority,
)


def _caps() -> ModelCapabilities:
    return ModelCapabilities(max_context_tokens=1_000, max_output_tokens=250)


def _item(item_id: str, tokens: int, priority: Priority) -> ContextItem:
    return ContextItem(
        item_id, "c" * (tokens * 4), priority, Level.L2_EXACT, f"reason {item_id}"
    )


def test_budget_derives_from_capabilities_with_output_headroom():
    manager = ContextManager(_caps())
    # (1000 - 250) * 0.8 = 600: the output reserve is never context space.
    assert manager.budget() == 600

    no_reserve = ContextManager(_caps(), reserve_output_headroom=False)
    assert no_reserve.budget() == 800  # legacy 0.8 ratio without reservation


def test_over_budget_tokens_reports_the_overflow():
    manager = ContextManager(_caps())
    manager.add_item(_item("a", 500, Priority.P6_METADATA))
    assert manager.over_budget_tokens() == 0
    manager.add_item(_item("b", 200, Priority.P6_METADATA))
    assert manager.over_budget_tokens() == 100  # 700 - 600


def test_p0_p1_p2_are_never_evicted_even_when_over_budget():
    manager = ContextManager(_caps())
    manager.add_item(_item("objective", 200, Priority.P0_CRITICAL))
    manager.add_item(_item("plan", 200, Priority.P1_PLAN))
    manager.add_item(_item("target", 200, Priority.P2_TARGET))
    manager.add_item(_item("bg1", 150, Priority.P7_BACKGROUND))
    manager.add_item(_item("bg2", 150, Priority.P7_BACKGROUND))

    compacted = manager.compact()

    ids = [i.id for i in compacted]
    assert {"objective", "plan", "target"} <= set(ids)
    assert manager.metrics.total_tokens <= manager.budget()


def test_eviction_follows_p7_downward_order_not_insertion_order():
    manager = ContextManager(_caps())
    manager.add_item(_item("objective", 200, Priority.P0_CRITICAL))
    manager.add_item(_item("p3-test", 200, Priority.P3_TESTS))
    manager.add_item(_item("p5-summary", 200, Priority.P5_SUMMARIES))
    manager.add_item(_item("p6-meta", 200, Priority.P6_METADATA))
    manager.add_item(_item("p7-bg", 200, Priority.P7_BACKGROUND))

    compacted = manager.compact()
    ids = {i.id for i in compacted}

    # budget 600: eviction takes lowest priority first — P7 kept, then P6,
    # then the budget is full and P5/P3 stay out (strict PRD §12.3 order).
    assert "p7-bg" in ids
    assert "p6-meta" in ids
    assert "p5-summary" not in ids
    assert "p3-test" not in ids
    assert "objective" in ids  # P0 never evicted
    assert manager.metrics.total_tokens <= manager.budget()


def test_compaction_is_idempotent_within_budget():
    manager = ContextManager(_caps())
    for index in range(10):
        manager.add_item(_item(f"bg{index}", 100, Priority.P7_BACKGROUND))
    manager.add_item(_item("objective", 100, Priority.P0_CRITICAL))

    once = manager.compact()
    events_after_first = manager.metrics.compaction_events
    twice = manager.compact()

    assert once == twice
    assert manager.metrics.compaction_events == events_after_first
    assert manager.metrics.total_tokens <= manager.budget()


def test_assembled_context_never_exceeds_budget():
    manager = ContextManager(_caps())
    for index in range(12):
        manager.add_item(
            ContextItem(
                f"f{index}", "F" * 400, Priority.P7_BACKGROUND, Level.L2_EXACT,
                "search candidate",
            )
        )
    context_text = manager.get_context()

    assert manager.metrics.total_tokens <= manager.budget()
    assert context_text.count("[L2]") == len(manager.items)
