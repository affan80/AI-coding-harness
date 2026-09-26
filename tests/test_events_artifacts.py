"""Issue #25 — event streams, tool-call records, and large-output artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.core.models import UserRequest
from harness.telemetry import ARTIFACT_INLINE_LIMIT, RunStore


def _store(tmp_path: Path) -> RunStore:
    return RunStore.start(
        UserRequest(repository="/tmp/sample", objective="objective"),
        runs_root=tmp_path / "runs",
    )


def _tool_lines(store: RunStore) -> list[dict]:
    raw = (store.run_dir / "tool-calls.jsonl").read_text().splitlines()
    return [json.loads(line) for line in raw]


def _event_lines(store: RunStore) -> list[dict]:
    raw = (store.run_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in raw]


def test_small_output_stays_inline_without_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)

    record = store.record_tool_call(
        name="search_text",
        args_summary='pattern="authenticate_user"',
        status="ok",
        started_at="2026-09-27T12:00:00+00:00",
        duration_ms=12,
        output="services/auth.py:14:def authenticate_user",
    )

    assert record.artifact is None
    assert not record.truncated
    lines = _tool_lines(store)
    assert len(lines) == 1
    assert lines[0]["id"] == "tool-0001"
    assert lines[0]["summary"] == "services/auth.py:14:def authenticate_user"


def test_large_output_is_stored_as_artifact_with_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    large = "x" * (ARTIFACT_INLINE_LIMIT + 5000)

    record = store.record_tool_call(
        name="run_command",
        args_summary="pytest -q",
        status="error",
        started_at="2026-09-27T12:00:00+00:00",
        duration_ms=931,
        output=large,
    )

    assert record.artifact is not None
    artifact_file = store.run_dir / record.artifact.path
    content = artifact_file.read_bytes()
    assert len(content) == record.artifact.size
    assert hashlib.sha256(content).hexdigest() == record.artifact.sha256
    assert record.truncated

    # Neither the tool record nor the event embeds the large output.
    raw_tools = (store.run_dir / "tool-calls.jsonl").read_text()
    raw_events = (store.run_dir / "events.jsonl").read_text()
    assert large not in raw_tools
    assert large not in raw_events


def test_every_tool_event_carries_evidence_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record_tool_call(
        name="read_file",
        args_summary="app.py",
        status="ok",
        started_at="2026-09-27T12:00:00+00:00",
        duration_ms=3,
        output="contents",
    )
    store.record_tool_call(
        name="run_command",
        args_summary="make test",
        status="timeout",
        started_at="2026-09-27T12:00:01+00:00",
        duration_ms=120_000,
    )

    events = [e for e in _event_lines(store) if e["kind"] == "tool"]
    assert len(events) == 2
    for event in events:
        assert event["timestamp"]
        assert isinstance(event["data"]["duration_ms"], int)
        assert event["data"]["status"] in {"ok", "error", "denied", "timeout"}
        assert event["tool_call_id"]  # evidence reference into tool-calls.jsonl
    assert events[1]["data"]["status"] == "timeout"
    assert events[1]["data"]["duration_ms"] == 120_000


def test_tool_call_ids_and_seq_increment(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.record_tool_call(
        name="repo_tree", args_summary=".", status="ok",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=1,
    )
    second = store.record_tool_call(
        name="repo_tree", args_summary=".", status="ok",
        started_at="2026-09-27T12:00:01+00:00", duration_ms=1,
    )

    assert (first.seq, second.seq) == (1, 2)
    assert (first.id, second.id) == ("tool-0001", "tool-0002")


def test_invalid_tool_status_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="invalid tool status"):
        store.record_tool_call(
            name="read_file", args_summary="app.py", status="excellent",
            started_at="2026-09-27T12:00:00+00:00", duration_ms=1,
        )


def test_record_round_trips_through_serialization(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.record_tool_call(
        name="apply_patch", args_summary="app/auth.py", status="ok",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=8,
        summary="patched auth",
    )

    restored = type(record).from_dict(record.to_dict())

    assert restored == record
