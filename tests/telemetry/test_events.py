"""Issue #69: structured lifecycle telemetry events with redaction."""

import json

from harness.telemetry.events import Event, EventRecorder, EventType


def test_emits_events_for_every_lifecycle_kind():
    recorder = EventRecorder()
    recorder.emit(EventType.STATE, "INITIALIZE -> UNDERSTAND")
    recorder.emit(EventType.MODEL_CALL, "intent extraction",
                  data={"input_tokens": 120, "output_tokens": 80},
                  duration_ms=340)
    recorder.emit(EventType.TOOL_CALL, "read_file app.py", duration_ms=3)
    recorder.emit(EventType.PATCH, "patched app/auth.py (p-abcdef12)")
    recorder.emit(EventType.FAILURE, "test_invalid_password failed (exit 1)")
    recorder.emit(EventType.RETRY, "bounded retry of S3", data={"recovered": True})
    recorder.emit(EventType.AUDIT, "audit round 1: 2 findings")
    recorder.emit(EventType.VERIFICATION, "targeted tests pass (18/18)")
    recorder.emit(EventType.CONTEXT, "working set built",
                  data={"discovered_files": 400, "selected_files": 6})

    kinds = {e.type for e in recorder.events()}
    assert kinds == set(EventType)
    assert [e.seq for e in recorder.events()] == list(range(1, 10))


def test_secrets_are_redacted_from_descriptions_and_data():
    recorder = EventRecorder(secrets=["ghp_super_secret_token"])
    recorder.emit(
        EventType.TOOL_CALL,
        "curl -H 'Authorization: Bearer ghp_super_secret_token' api.example.com",
        data={"command": "echo ghp_super_secret_token", "nested": ["still ghp_super_secret_token"]},
    )
    blob = json.dumps([e.to_dict() for e in recorder.events()])
    assert "ghp_super_secret_token" not in blob
    assert "***REDACTED***" in blob


def test_descriptions_are_bounded_no_chain_of_thought():
    recorder = EventRecorder()
    long_rant = "my reasoning step by step, " * 100  # 2600 chars of raw CoT
    event = recorder.emit(EventType.MODEL_CALL, long_rant)
    assert len(event.description) <= 300
    assert event.description.endswith("…") is False  # hard truncation, not ellipsis


def test_jsonl_round_trip_and_failure_boundary():
    recorder = EventRecorder()
    recorder.emit(EventType.STATE, "PLAN -> EXECUTE", data={"steps": 4})
    recorder.emit(EventType.FAILURE, "patch conflict on app.py")
    lines = recorder.to_jsonl().splitlines()
    assert len(lines) == 2
    restored = Event.from_dict(json.loads(lines[0]))
    assert restored.type is EventType.STATE
    assert restored.data == {"steps": 4}
    # malformed event dicts are rejected, not silently accepted
    import pytest

    with pytest.raises((KeyError, ValueError)):
        Event.from_dict({"seq": 1})
