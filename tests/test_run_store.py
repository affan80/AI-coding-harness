"""Issue #24 — run directory creation and atomic state files."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.core.models import SessionStatus, UserRequest
from harness.telemetry.store import RunStore


def _request(**overrides) -> UserRequest:
    defaults = {"repository": "/tmp/sample", "objective": "Fix the login bug"}
    defaults.update(overrides)
    return UserRequest(**defaults)


def _fixed_clock(tz_offset_seconds: int = 0):
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

    def clock() -> datetime:
        return base + timedelta(seconds=tz_offset_seconds)

    return clock


def test_start_creates_run_directory_with_state_files(tmp_path: Path) -> None:
    store = RunStore.start(_request(), runs_root=tmp_path / "runs", clock=_fixed_clock())

    assert store.run_dir.is_dir()
    assert store.run_dir.parent == tmp_path / "runs"
    request = json.loads((store.run_dir / "request.json").read_text())
    assert request["repository"] == "/tmp/sample"
    assert request["objective"] == "Fix the login bug"
    assert request["write_policy"] == "scoped"

    session = json.loads((store.run_dir / "session.json").read_text())
    assert session["status"] == "in_progress"
    assert session["session_id"] == store.session_id


def test_session_ids_are_unique_and_sortable(tmp_path: Path) -> None:
    first = RunStore.start(_request(), runs_root=tmp_path, clock=_fixed_clock(0))
    second = RunStore.start(_request(), runs_root=tmp_path, clock=_fixed_clock(5))

    assert first.session_id != second.session_id
    assert first.session_id < second.session_id
    assert re.fullmatch(r"s-\d{8}-\d{6}-[0-9a-f]{8}", first.session_id)


def test_run_directory_is_gitignored(tmp_path: Path) -> None:
    RunStore.start(_request(), runs_root=tmp_path / "runs")

    ignored = (tmp_path / "runs" / ".gitignore").read_text()
    assert "*" in ignored
    assert "!.gitignore" in ignored


def test_documents_round_trip_through_json(tmp_path: Path) -> None:
    store = RunStore.start(_request(), runs_root=tmp_path)
    payload = {"goals": [{"id": "G1", "description": "login works"}]}

    store.write_document("goals.json", payload)
    restored = json.loads((store.run_dir / "goals.json").read_text())

    assert restored == payload


def test_unknown_document_name_is_rejected(tmp_path: Path) -> None:
    store = RunStore.start(_request(), runs_root=tmp_path)

    with pytest.raises(ValueError, match="unknown run document"):
        store.write_document("chains-of-thought.json", {})


def test_interrupted_write_leaves_previous_state_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore.start(_request(), runs_root=tmp_path)
    good = {"plan": [{"id": "S1"}]}
    store.write_document("plan.json", good)

    import harness.telemetry.store as store_module

    def broken_replace(src, dst):
        raise OSError("simulated crash between temp write and replace")

    monkeypatch.setattr(store_module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        store.write_document("plan.json", {"plan": [{"id": "CORRUPT"}]})

    # The previous valid document survives and no partial content leaked.
    restored = json.loads((store.run_dir / "plan.json").read_text())
    assert restored == good
    monkeypatch.undo()

    # A retry succeeds and cleans up the temp file.
    store.write_document("plan.json", {"plan": [{"id": "S1"}, {"id": "S2"}]})
    restored = json.loads((store.run_dir / "plan.json").read_text())
    assert [step["id"] for step in restored["plan"]] == ["S1", "S2"]
    assert not list(store.run_dir.glob("*.tmp"))


def test_finalize_records_terminal_status(tmp_path: Path) -> None:
    store = RunStore.start(_request(), runs_root=tmp_path, clock=_fixed_clock())

    store.finalize(SessionStatus.FAILED, stop_reason="verification failed")

    session = json.loads((store.run_dir / "session.json").read_text())
    assert session["status"] == "failed"
    assert session["stop_reason"] == "verification failed"
    assert session["created_at"] == session["updated_at"]


def test_events_stream_is_sequential_jsonl(tmp_path: Path) -> None:
    store = RunStore.start(_request(), runs_root=tmp_path)
    store.append_event("state", "UNDERSTAND", state="UNDERSTAND")
    store.append_event("info", "profiled repository")

    lines = (store.run_dir / "events.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
    assert records[0]["kind"] == "info"
    assert records[-1]["message"] == "profiled repository"
