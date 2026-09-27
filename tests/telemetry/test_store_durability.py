"""Issue #2/#24/#25 durability boundaries: torn streams, atomic artifacts,
corrupt documents, and goal/plan evidence in the run directory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.cli.input import build_parser
from harness.cli.main import SessionSummary, run_session
from harness.core.models import SessionStatus, UserRequest
from harness.telemetry.report import STATUS_VERIFIED, CheckResult
from harness.telemetry.store import RunStore, reconstruct_run


@pytest.fixture()
def store(tmp_path: Path) -> RunStore:
    request = UserRequest(objective="fix the login bug", repository_path="/repo")
    return RunStore.start(request, runs_root=tmp_path / "runs")


def _big_output() -> str:
    return "x" * 10_000


def test_torn_final_event_line_is_skipped(store: RunStore):
    store.append_event("state", "first")
    store.append_event("state", "second")
    # Simulate a crash mid-append: half of a JSON record hits the disk.
    with open(store.run_dir / "events.jsonl", "ab") as fh:
        fh.write(b'{"seq": 99, "kind": "sta')

    events = store.read_events()
    # RunStore.start already logged "session started"; the torn line after
    # "second" must not appear as a record.
    assert [event["message"] for event in events] == [
        "session started",
        "first",
        "second",
    ]
    assert all(event["kind"] != "sta" for event in events)


def test_torn_final_tool_record_is_skipped_and_reconstruction_survives(store: RunStore):
    store.record_tool_call(
        name="read_file", args_summary="app.py", status="ok",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=3, summary="ok",
    )
    with open(store.run_dir / "tool-calls.jsonl", "ab") as fh:
        fh.write(b'{"id": "tool-0002", "name": "run_comm')

    records = store.read_tool_calls()
    assert [record.id for record in records] == ["tool-0001"]
    evidence = reconstruct_run(store.run_dir)
    assert [tool["id"] for tool in evidence["tool_calls"]] == ["tool-0001"]


def test_tool_event_carries_time_duration_status_and_evidence_reference(store: RunStore):
    record = store.record_tool_call(
        name="run_command", args_summary="pytest -q", status="error",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=400,
        output=_big_output(),
    )
    assert record.artifact is not None
    tool_events = [e for e in store.read_events() if e["kind"] == "tool"]
    assert len(tool_events) == 1
    event = tool_events[0]
    assert event["timestamp"]  # time
    assert event["data"]["duration_ms"] == 400
    assert event["data"]["status"] == "error"
    assert event["data"]["artifact"] == record.artifact.path  # evidence reference
    assert record.artifact.sha256 == hashlib.sha256(
        (store.run_dir / record.artifact.path).read_bytes()
    ).hexdigest()


def test_interrupted_artifact_write_keeps_previous_artifact_valid(
    store: RunStore, monkeypatch
):
    good_record = store.record_tool_call(
        name="run_command", args_summary="first", status="ok",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=1,
        output=_big_output(),
    )
    good_artifact = store.run_dir / good_record.artifact.path
    good_bytes = good_artifact.read_bytes()

    # A crash while storing the next artifact leaves a stray temp file, the
    # previous artifact intact, and no record or event for the failed call.
    def exploding_write(path: Path, content: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content)
        raise OSError("interrupted")

    monkeypatch.setattr("harness.telemetry.store._atomic_write", exploding_write)
    with pytest.raises(OSError):
        store.record_tool_call(
            name="run_command", args_summary="second", status="ok",
            started_at="2026-09-27T12:00:01+00:00", duration_ms=1,
            output=_big_output(),
        )
    monkeypatch.undo()

    assert good_artifact.read_bytes() == good_bytes
    assert (store.paths.artifacts_dir / "tool-0002-output.txt.tmp").exists()
    assert [record.id for record in store.read_tool_calls()] == ["tool-0001"]

    # Recovery: the next successful call stores and references its artifact.
    retry = store.record_tool_call(
        name="run_command", args_summary="retry", status="ok",
        started_at="2026-09-27T12:00:02+00:00", duration_ms=1,
        output="y" * 10_000,
    )
    assert retry.artifact.sha256 == hashlib.sha256(
        (store.run_dir / retry.artifact.path).read_bytes()
    ).hexdigest()


def test_corrupt_session_json_never_blocks_final_status(store: RunStore):
    (store.run_dir / "session.json").write_text("{corrupt", encoding="utf-8")
    store.finalize(SessionStatus.FAILED, stop_reason="boom")

    session = json.loads((store.run_dir / "session.json").read_text(encoding="utf-8"))
    assert session["status"] == "failed"
    assert session["stop_reason"] == "boom"


def test_reconstruction_covers_goals_plan_and_artifact_hash(store: RunStore):
    store.write_document("goals.json", {
        "goals": [{"id": "G1", "title": "fix login", "status": "COMPLETED"}]
    })
    store.write_document("plan.json", {
        "steps": [{"id": "S1", "action": "patch", "target": "app/auth.py"}]
    })
    record = store.record_tool_call(
        name="apply_patch", args_summary="app/auth.py", status="ok",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=9,
        output=_big_output(),
    )
    store.write_verification({"status": "pass", "checks": [], "notes": []})
    store.finalize(SessionStatus.VERIFIED)

    evidence = reconstruct_run(store.run_dir)
    assert evidence["goals"] == [{"id": "G1", "title": "fix login", "status": "COMPLETED"}]
    assert evidence["plan_present"] is True
    assert evidence["plan_steps"] == 1
    assert evidence["objective"] == "fix the login bug"
    assert evidence["event_count"] >= 2  # session started + tool event + finalize
    assert evidence["tool_calls"][0]["artifact"]["sha256"] == record.artifact.sha256
    artifact_path = store.run_dir / evidence["tool_calls"][0]["artifact"]["path"]
    assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == record.artifact.sha256


def test_run_session_persists_goals_plan_and_atomic_report(tmp_path: Path):
    lines: list[str] = []
    argv = [str(tmp_path), "demo objective", "--runs-root", str(tmp_path / "runs")]
    args = build_parser().parse_args(argv)
    code = run_session(
        args,
        lambda request, budget, store, recorder: SessionSummary(
            status=STATUS_VERIFIED,
            goals_completed=1,
            goals_total=1,
            checks=[CheckResult(
                name="check", command="true", passed=True, exit_code=0,
                output_summary="ok",
            )],
            goals=[{"id": "G1", "title": "demo", "status": "COMPLETED"}],
            plan=[{"id": "S1", "action": "verify", "target": "demo"}],
        ),
        prompt=lambda _label: "",
        out=lines.append,
    )
    assert code == 0
    runs = list((tmp_path / "runs").iterdir())
    run_dir = next(path for path in runs if path.is_dir())
    assert json.loads((run_dir / "goals.json").read_text())["goals"][0]["id"] == "G1"
    assert json.loads((run_dir / "plan.json").read_text())["steps"][0]["id"] == "S1"
    assert (run_dir / "final-report.md").is_file()
    assert not (run_dir / "final-report.md.tmp").exists()
    evidence = reconstruct_run(run_dir)
    assert evidence["plan_present"] is True
    assert evidence["goals"][0]["title"] == "demo"


def test_run_session_without_plan_leaves_plan_json_absent(tmp_path: Path):
    argv = [str(tmp_path), "demo objective", "--runs-root", str(tmp_path / "runs")]
    args = build_parser().parse_args(argv)
    run_session(
        args,
        lambda request, budget, store, recorder: SessionSummary(
            status=STATUS_VERIFIED,
            goals=[{"id": "G1", "title": "demo", "status": "COMPLETED"}],
        ),
        prompt=lambda _label: "",
        out=lambda _line: None,
    )
    run_dir = next(
        path for path in (tmp_path / "runs").iterdir() if path.is_dir()
    )
    assert (run_dir / "goals.json").is_file()
    assert not (run_dir / "plan.json").exists()  # honestly absent, not fabricated
