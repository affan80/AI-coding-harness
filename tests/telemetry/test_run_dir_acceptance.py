"""Issues #24/#25 acceptance: run directory, atomic state, streams, artifacts.

The run store landed with the evidence/telemetry workstreams; this module
maps the two issues' acceptance bullets 1:1 — atomic state documents that
survive interrupted writes, append-only JSONL streams with time/duration/
status/evidence on every tool event, oversized output kept as hashed
artifacts, and generated runs excluded from source control.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.core.models import UserRequest
from harness.telemetry.store import RunStore


@pytest.fixture()
def store(tmp_path: Path) -> RunStore:
    request = UserRequest(objective="obj", repository_path="/repo")
    return RunStore.start(request, runs_root=tmp_path / "runs")


def test_run_dir_created_at_start_with_state_documents(store: RunStore, tmp_path):
    run_dir = Path(store.run_dir)
    assert run_dir.is_dir()
    assert (run_dir / "request.json").is_file()
    assert (run_dir / "session.json").is_file()
    session = json.loads((run_dir / "session.json").read_text())
    assert session["status"] == "in_progress"
    assert run_dir.relative_to(tmp_path / "runs").parts[0].startswith("s-")


def test_state_documents_are_whitelisted(store: RunStore):
    store.write_document("goals.json", {"goals": []})
    assert (store.run_dir / "goals.json").is_file()
    with pytest.raises(ValueError):
        store.write_document("not-a-document.json", {})


def test_interrupted_write_keeps_previous_state_readable(
    store: RunStore, monkeypatch
):
    store.write_document("plan.json", {"steps": ["original"]})

    def crash(path, content):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content)
        raise OSError("interrupted")

    monkeypatch.setattr("harness.telemetry.store._atomic_write", crash)
    with pytest.raises(OSError):
        store.write_document("plan.json", {"steps": ["replacement"]})

    # previous valid state survives; the partial temp file is quarantined
    assert json.loads(
        (store.run_dir / "plan.json").read_text()
    ) == {"steps": ["original"]}
    assert (store.run_dir / "plan.json.tmp").exists()


def test_events_and_tool_calls_are_append_only_jsonl(store: RunStore):
    store.append_event("state", "entered PLAN", state="PLAN")
    record = store.record_tool_call(
        name="run_command",
        args_summary="pytest -q",
        status="error",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=4_000,
        summary="1 failed",
    )
    store.append_event("state", "entered VERIFY", state="VERIFY")

    events = [
        json.loads(line)
        for line in (store.run_dir / "events.jsonl").read_text().splitlines()
    ]
    # session-start + 2 state events + the tool event, strictly ordered
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert all(e["timestamp"] for e in events)
    tool_event = next(
        e for e in events if e.get("tool_call_id") == record.id
    )
    # every tool event: time, duration, status, and evidence reference
    assert tool_event["data"]["duration_ms"] == 4_000
    assert tool_event["data"]["status"] == "error"
    assert tool_event["tool_call_id"] == record.id

    calls = [
        json.loads(line)
        for line in (store.run_dir / "tool-calls.jsonl").read_text().splitlines()
    ]
    assert calls[0]["id"] == record.id


def test_oversized_output_becomes_hashed_artifact_not_inline(store: RunStore):
    huge = "e" * 10_000
    record = store.record_tool_call(
        name="run_command",
        args_summary="make",
        status="error",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=10,
        output=huge,
    )
    artifact_path = store.run_dir / record.artifact.path
    raw = artifact_path.read_text(encoding="utf-8")
    assert raw == huge  # retrievable in full by the evidence reference
    assert (
        hashlib.sha256(raw.encode()).hexdigest() == record.artifact.sha256
    )
    assert len(record.summary) < 500  # never inline


def test_generated_runs_are_excluded_from_source_control(tmp_path: Path):
    request = UserRequest(objective="obj", repository_path="/repo")
    RunStore.start(request, runs_root=tmp_path / "runs")
    gitignore = (tmp_path / "runs" / ".gitignore").read_text()
    assert gitignore.splitlines()[0] == "*"
    assert "!.gitignore" in gitignore.splitlines()
