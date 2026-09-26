"""Issue #41: artifacts, truncation, normalized failures, and redaction.

The run store already routed oversized output to artifacts/ and normalized
tool statuses; these tests pin the acceptance criteria end to end,
including the redaction seam that was missing: secrets never leak into
records, summaries, or event data, while the raw output stays retrievable
in artifacts/ by path + sha256.
"""

import hashlib
import json
from pathlib import Path

import pytest

from harness.core.models import UserRequest
from harness.telemetry.store import RunStore

SECRET = "sk-live-0123456789abcdef"


@pytest.fixture()
def store(tmp_path: Path) -> RunStore:
    request = UserRequest(objective="obj", repository_path="/repo")
    return RunStore.start(request, runs_root=tmp_path / "runs", secrets=[SECRET])


def test_oversized_output_is_truncated_in_the_record_and_kept_as_artifact(
    store: RunStore,
):
    huge = ("x" * 200 + f" token={SECRET}\n") * 100  # ~30 KB with a secret in it
    record = store.record_tool_call(
        name="run_command",
        args_summary="pytest -q",
        status="error",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=4_210,
        output=huge,
    )

    assert record.truncated
    assert record.artifact is not None
    artifact_path = store.run_dir / record.artifact.path
    # the full raw output stays retrievable by safe evidence reference
    raw = artifact_path.read_text(encoding="utf-8")
    assert raw == huge
    assert hashlib.sha256(raw.encode()).hexdigest() == record.artifact.sha256
    # the inline summary is bounded (400 chars + the truncation suffix)
    # and scrubbed
    assert len(record.summary) <= 450
    assert SECRET not in record.summary


def test_secrets_never_reach_records_summaries_or_event_data(store: RunStore):
    store.record_tool_call(
        name="run_command",
        args_summary=f"curl -H 'Authorization: Bearer {SECRET}' https://api.example.com",
        status="ok",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=12,
        summary=f"response contained {SECRET} in the body",
    )
    records = [
        json.loads(line)
        for line in (store.run_dir / "tool-calls.jsonl").read_text().splitlines()
    ]
    events = [
        json.loads(line)
        for line in (store.run_dir / "events.jsonl").read_text().splitlines()
    ]
    blob = json.dumps(records + events)
    assert SECRET not in blob
    assert "[REDACTED]" in blob  # the Redactor placeholder


def test_normalized_failure_statuses_are_distinct_and_recorded(store: RunStore):
    for status in ("error", "denied", "timeout"):
        store.record_tool_call(
            name="run_command",
            args_summary=f"command for {status}",
            status=status,
            started_at="2026-01-01T00:00:00+00:00",
            duration_ms=99 if status != "timeout" else 120_000,
            summary=f"{status} outcome",
        )
    records = [
        json.loads(line)
        for line in (store.run_dir / "tool-calls.jsonl").read_text().splitlines()
    ]
    assert [r["status"] for r in records] == ["error", "denied", "timeout"]
    # every tool event carries time, duration, status, and evidence reference
    events = [
        json.loads(line)
        for line in (store.run_dir / "events.jsonl").read_text().splitlines()
        if line
    ]
    tool_events = [e for e in events if e["kind"] == "tool"]
    assert len(tool_events) == 3
    for event in tool_events:
        assert event["timestamp"]
        assert "duration_ms" in event["data"]
        assert event["data"]["status"] in ("error", "denied", "timeout")
        assert event["tool_call_id"]


def test_invalid_tool_status_is_rejected_upfront(store: RunStore):
    with pytest.raises(ValueError):
        store.record_tool_call(
            name="run_command",
            args_summary="x",
            status="sort-of-fine",
            started_at="2026-01-01T00:00:00+00:00",
            duration_ms=1,
        )


def test_default_store_has_no_secrets_to_scrub(tmp_path: Path):
    request = UserRequest(objective="obj", repository_path="/repo")
    store = RunStore.start(request, runs_root=tmp_path / "runs")
    record = store.record_tool_call(
        name="read_file",
        args_summary="app.py",
        status="ok",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=2,
        summary="read 10 bytes",
    )
    assert record.summary == "read 10 bytes"
