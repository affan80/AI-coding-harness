"""Issue #47: context compaction, invalidation, and observability.

Covers the auto-compaction trigger near the safe budget, raw eviction
evidence retained on disk, path-based invalidation after patches, and the
selected-versus-discovered size metrics (PRD §§12.7-12.9).
"""

import json

from harness.context.assembler import Candidate, assemble_context
from harness.context.manager import (
    ContextItem,
    ContextManager,
    Level,
    ModelCapabilities,
    Priority,
)


def _caps(context_tokens: int = 1000, output_tokens: int = 0) -> ModelCapabilities:
    return ModelCapabilities(
        max_context_tokens=context_tokens, max_output_tokens=output_tokens
    )


def _item(item_id: str, content: str, priority: Priority = Priority.P6_METADATA):
    return ContextItem(
        id=item_id,
        content=content,
        priority=priority,
        level=Level.L0_METADATA if priority is Priority.P6_METADATA else Level.L2_EXACT,
        reason="test",
    )


def test_add_item_compacts_automatically_near_safe_capacity():
    manager = ContextManager(_caps(), auto_compact=True)
    assert manager.budget() == 800  # (1000 - 0) * 0.8

    for n in range(3):
        manager.add_item(_item(f"pad-{n}", "x" * 1200))  # ~300 tokens each
    assert manager.metrics.compaction_events >= 1
    assert manager.metrics.total_tokens <= manager.safe_capacity

    passive = ContextManager(_caps(), auto_compact=False)
    for n in range(3):
        passive.add_item(_item(f"pad-{n}", "x" * 1200))
    assert passive.metrics.compaction_events == 0
    assert passive.metrics.total_tokens > passive.safe_capacity


def test_evicted_items_are_retained_as_raw_evidence_on_disk(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manager = ContextManager(_caps(), evidence_dir=evidence_dir)
    protected = _item("keep:plan.py", "p" * 400, priority=Priority.P2_TARGET)
    manager.add_item(protected)
    for n in range(6):
        manager.add_item(_item(f"drop-{n}", "d" * 1200))  # worst priorities
    manager.compact()

    assert manager.metrics.compaction_events >= 1
    assert manager.last_evicted, "compaction must report what it dropped"

    evidence_file = evidence_dir / "context-evictions.jsonl"
    assert evidence_file.exists()
    lines = [json.loads(line) for line in evidence_file.read_text().splitlines()]
    # Metrics are cumulative across compaction events; the evidence file
    # must hold exactly the items the metrics claim were evicted.
    assert len(lines) == manager.metrics.evicted_items
    assert sum(entry["tokens"] for entry in lines) == manager.metrics.evicted_tokens
    evicted_ids = {item.id for item in manager.last_evicted}
    assert evicted_ids <= {entry["id"] for entry in lines}
    assert all("content" in entry for entry in lines), "raw content retained"
    assert [entry["sequence"] for entry in lines] == sorted(
        entry["sequence"] for entry in lines
    )
    # The non-evictable target survived; evicted content is not in context.
    assert "keep:plan.py" in [i.id for i in manager.items]
    for item in manager.items:
        assert item.id not in evicted_ids


def test_invalidate_paths_drops_every_level_for_changed_files():
    manager = ContextManager(_caps())
    for level_id in ("l0", "l1", "l2"):
        manager.add_item(_item(f"{level_id}:src/cache.py", "c" * 200))
    manager.add_item(_item("l2:src/other.py", "o" * 200, priority=Priority.P2_TARGET))

    removed = manager.invalidate_paths(["src/cache.py"])
    assert removed == 3
    remaining = [i.id for i in manager.items]
    assert remaining == ["l2:src/other.py"]
    assert manager.metrics.invalidated_items == 3


def test_invalidated_summaries_are_kept_in_eviction_evidence(tmp_path):
    evidence_dir = tmp_path / "evidence"
    manager = ContextManager(_caps(), evidence_dir=evidence_dir)
    manager.add_item(_item("l1:src/cache.py", "s" * 200))
    manager.invalidate_paths(["src/cache.py"])

    lines = (evidence_dir / "context-evictions.jsonl").read_text().splitlines()
    assert lines, "prior content must be retained as diff evidence"
    assert json.loads(lines[0])["id"] == "l1:src/cache.py"


def test_large_repo_retrieves_relevant_initially_unloaded_file(tmp_path):
    """Acceptance: a relevant file that the first pass did not load is pulled
    into the working set by a failure-driven second pass, compaction runs,
    and metrics report selected-versus-discovered size."""
    for n in range(30):
        (tmp_path / f"module_{n:02d}.py").write_text(
            f"# utility module {n}\nVALUE_{n} = {n}\n" + "x" * 900
        )
    (tmp_path / "rerank_cache.py").write_text(
        "# rerank cache eviction policy\n"
        "def evict_stale_entries(cache, ttl):\n"
        "    return [k for k, v in cache.items() if v.age > ttl]\n"
    )

    decoys = [
        Candidate(path=f"module_{n:02d}.py", summary=f"utility module {n} helpers")
        for n in range(30)
    ]
    relevant = Candidate(
        path="rerank_cache.py",
        summary="rerank cache eviction policy for stale entries",
    )

    # Small model budget forces compaction during the first pass.
    manager = ContextManager(
        _caps(context_tokens=1200), evidence_dir=tmp_path / "evidence"
    )
    first = assemble_context(
        manager, tmp_path, decoys, goal_text="fix utility helpers"
    )
    assert manager.metrics.compaction_events >= 1, "large scan must compact"
    assert manager.metrics.discovered_tokens > manager.metrics.total_tokens, (
        "selected working set must be smaller than the discovered scan"
    )
    assert "l2:rerank_cache.py" not in [i.id for i in first.selected], (
        "the relevant file starts unloaded"
    )

    # The failure names the cache: the second pass must retrieve it, and
    # compaction must retain it (P2 target) over lower-priority leftovers.
    second = assemble_context(
        manager,
        tmp_path,
        [relevant],
        goal_text="fix eviction",
        failure_text="rerank cache eviction is stale",
    )
    loaded = [i for i in manager.items if i.id == "l2:rerank_cache.py"]
    assert loaded, "relevant initially unloaded file must be retrieved"
    assert loaded[0].priority == Priority.P2_TARGET
    assert manager.metrics.total_tokens <= manager.safe_capacity
    assert (tmp_path / "evidence" / "context-evictions.jsonl").exists()
    assert second.unselected == [], "the target is no longer unselected"
