"""Issue #47 acceptance: compaction, invalidation, and context measurement."""

from harness.context.manager import (
    ContextItem,
    ContextManager,
    Level,
    ModelCapabilities,
    Priority,
)


def _caps() -> ModelCapabilities:
    return ModelCapabilities(max_context_tokens=500, max_output_tokens=100)


def _item(item_id: str, tokens: int, priority: Priority) -> ContextItem:
    return ContextItem(item_id, "c" * (tokens * 4), priority, Level.L2_EXACT,
                       f"reason {item_id}")


def test_invalidation_removes_the_item_and_updates_measurement():
    manager = ContextManager(_caps())
    manager.add_item(_item("summary:auth.py", 50, Priority.P5_SUMMARIES))
    before = manager.metrics.total_tokens
    assert before == 50

    manager.invalidate_by_id("summary:auth.py")

    assert manager.metrics.total_tokens == 0
    assert manager.metrics.tokens_by_category["L2"] == 0
    assert all(item.id != "summary:auth.py" for item in manager.items)


def test_invalidation_of_an_unknown_id_is_a_clean_no_op():
    manager = ContextManager(_caps())
    manager.add_item(_item("keep", 40, Priority.P6_METADATA))
    manager.invalidate_by_id("never-existed")
    assert manager.metrics.total_tokens == 40
    assert len(manager.items) == 1


def test_compaction_events_are_counted_per_enforcement():
    manager = ContextManager(_caps())
    for index in range(6):
        manager.add_item(_item(f"bg{index}", 100, Priority.P7_BACKGROUND))

    assert manager.metrics.total_tokens == 600
    manager.compact()  # 600 > 400 safe capacity: enforcement #1
    assert manager.metrics.compaction_events == 1
    assert manager.metrics.total_tokens <= manager.budget()

    manager.compact()  # already within budget: no second enforcement
    assert manager.metrics.compaction_events == 1


def test_measurement_breaks_tokens_down_by_disclosure_level():
    manager = ContextManager(_caps())
    manager.add_item(ContextItem("meta", "M" * 40, Priority.P6_METADATA,
                                 Level.L0_METADATA, "scan"))
    manager.add_item(ContextItem("summary", "S" * 80, Priority.P5_SUMMARIES,
                                 Level.L1_SUMMARY, "dependency summary"))
    manager.add_item(ContextItem("source", "X" * 120, Priority.P2_TARGET,
                                 Level.L2_EXACT, "target source"))

    by_level = manager.metrics.tokens_by_category
    assert by_level["L0"] == 10
    assert by_level["L1"] == 20
    assert by_level["L2"] == 30
    assert manager.metrics.total_tokens == 60


def test_invalidated_summaries_can_be_re_read_after_a_patch():
    """The patch-invalidation cycle from PRD §12.8, measured."""
    manager = ContextManager(_caps())
    manager.add_item(_item("l1:auth.py", 60, Priority.P5_SUMMARIES))
    manager.add_item(_item("objective", 60, Priority.P0_CRITICAL))

    manager.invalidate_by_id("l1:auth.py")
    assert manager.metrics.total_tokens == 60

    # the summary is re-read fresh after the patch and re-added at L1
    manager.add_item(ContextItem(
        "l1:auth.py", "S" * 320, Priority.P5_SUMMARIES, Level.L1_SUMMARY,
        "re-read after patch",
    ))
    assert manager.metrics.total_tokens == 140
    assert manager.metrics.tokens_by_category["L1"] == 80
    assert manager.metrics.tokens_by_category["L2"] == 60  # the P0 objective
