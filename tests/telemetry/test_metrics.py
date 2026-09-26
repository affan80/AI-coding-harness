"""Issue #70: usage metrics reconcile with the event log and render concisely."""

from harness.telemetry.events import Event, EventRecorder, EventType
from harness.telemetry.metrics import UsageMetrics


def _recorded_events():
    recorder = EventRecorder()
    recorder.emit(EventType.CONTEXT, "working set built", data={
        "discovered_files": 500, "discovered_tokens": 90_000,
        "selected_files": 7, "selected_tokens": 5_100,
    })
    recorder.emit(EventType.CONTEXT, "budget exceeded; compacted", data={
        "compaction": True,
    })
    recorder.emit(EventType.MODEL_CALL, "intent extraction",
                  data={"input_tokens": 1000, "output_tokens": 300, "cost_usd": 0.01},
                  duration_ms=500)
    recorder.emit(EventType.MODEL_CALL, "plan generation",
                  data={"input_tokens": 800, "output_tokens": 200, "cost_usd": 0.02},
                  duration_ms=700)
    recorder.emit(EventType.TOOL_CALL, "read_file app.py", duration_ms=4)
    recorder.emit(EventType.PATCH, "patched app.py")
    recorder.emit(EventType.FAILURE, "targeted test failed")
    recorder.emit(EventType.RETRY, "materially different repair", data={"recovered": True})
    recorder.emit(EventType.VERIFICATION, "full suite passes")
    recorder.emit(EventType.AUDIT, "audit round 1")
    return recorder.events()


def test_metrics_derive_from_events_and_reconcile():
    events = _recorded_events()
    metrics = UsageMetrics.from_events(events)

    assert metrics.model_calls == 2
    assert metrics.tool_calls == 1
    assert metrics.patches == 1
    assert metrics.failures == 1
    assert metrics.retries == 1
    assert metrics.recovery_attempts == 1
    assert metrics.verifications == 1
    assert metrics.audits == 1
    assert metrics.input_tokens == 1800
    assert metrics.output_tokens == 500
    assert metrics.total_tokens == 2300
    assert metrics.latency_ms == 1204
    assert metrics.cost_usd == 0.03
    assert metrics.context_discovered_tokens == 90_000
    assert metrics.context_selected_tokens == 5_100
    assert metrics.compaction_events == 1


def test_reconciliation_is_exact_replay():
    events = _recorded_events()
    once = UsageMetrics.from_events(events)
    twice = UsageMetrics.from_events(events)
    assert once.to_dict() == twice.to_dict()
    # re-applying the same events to the same metrics object double-counts,
    # proving apply() is the single reconciliation rule
    replay = UsageMetrics.from_events(events)
    for event in events:
        replay.apply(event)
    assert replay.model_calls == once.model_calls * 2


def test_render_is_concise_and_covers_context_ratio():
    lines = UsageMetrics.from_events(_recorded_events()).render_lines()
    assert len(lines) <= 6
    assert any("model calls: 2" in line for line in lines)
    assert any("1800 in / 500 out" in line for line in lines)
    assert any("7/500" in line for line in lines)
    assert any("5100/90000" in line for line in lines)
    assert any("compactions: 1" in line for line in lines)


def test_cost_stays_absent_when_unknown():
    metrics = UsageMetrics.from_events([
        Event(seq=1, type=EventType.MODEL_CALL, description="call", at="now"),
    ])
    assert metrics.cost_usd is None
    lines = metrics.render_lines()
    assert not any("cost:" in line for line in lines)
