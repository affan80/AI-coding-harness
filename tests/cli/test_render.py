"""Issue #28: live state + concise tool events, bounded, evidence-linked."""

from harness.cli.render import render_final_summary, render_lines, render_live
from harness.telemetry.events import EventRecorder, EventType


def _runstore_style_events():
    """Events exactly as RunStore writes them (JSONL dict shape)."""
    return [
        {"seq": 1, "kind": "state", "state": "UNDERSTAND",
         "message": "session started"},
        {"seq": 2, "kind": "state", "state": "EXECUTE", "message": ""},
        {
            "seq": 3, "kind": "tool", "message": "read app.py",
            "tool_call_id": "tool-0001",
            "data": {"tool": "read_file", "status": "ok", "duration_ms": 3,
                     "artifact": None, "truncated": False},
        },
        {
            "seq": 4, "kind": "tool", "message": "pytest -q",
            "tool_call_id": "tool-0002",
            "data": {"tool": "run_tests", "status": "error", "duration_ms": 4210,
                     "artifact": "artifacts/tool-0002-output.txt",
                     "truncated": True},
        },
    ]


def test_state_transitions_and_tool_events_render_concisely():
    lines = render_lines(_runstore_style_events())
    assert any("UNDERSTAND" in line and "✓" in line for line in lines)
    assert any("EXECUTE" in line for line in lines)
    tool_lines = [line for line in lines if "read_file" in line or "run_tests" in line]
    assert len(tool_lines) == 2
    assert "3 ms" in tool_lines[0]
    assert "✗" in tool_lines[1] and "4210 ms" in tool_lines[1]


def test_every_visible_tool_line_maps_to_stored_evidence():
    lines = render_lines(_runstore_style_events())
    tool_lines = [line for line in lines if "ev=tool-" in line]
    assert {line.split("ev=")[1] for line in tool_lines} == {
        "tool-0001", "tool-0002",
    }


def test_large_output_never_reaches_the_ui_but_is_pointed_to():
    huge = {"tool": "run_command", "status": "ok", "duration_ms": 9,
            "artifact": "artifacts/tool-0003-output.txt", "truncated": True}
    events = [{
        "seq": 1, "kind": "tool", "message": "x" * 5000,
        "tool_call_id": "tool-0003", "data": huge,
    }]
    lines = render_lines(events)
    assert len(lines) == 1
    assert len(lines[0]) <= 160
    assert "artifacts/tool-0003-output.txt" in lines[0]
    assert "x" * 100 not in lines[0]  # raw output is never echoed


def test_event_recorder_shape_is_supported_too():
    recorder = EventRecorder()
    recorder.emit(EventType.STATE, "PLAN -> EXECUTE")
    recorder.emit(EventType.TOOL_CALL, "patch app.py", data={
        "tool": "apply_patch", "status": "ok", "duration_ms": 7,
    })
    text = render_live(recorder, current_state="VERIFY")
    assert "PLAN -> EXECUTE" in text
    assert "apply_patch" in text and "7 ms" in text
    assert text.endswith("→ VERIFY")


def test_final_summary_block_matches_prd_shape():
    lines = render_final_summary(
        status="VERIFIED", goals_completed=3, goals_total=3,
        changed_files=["a.py", "b.py"], checks_passed=4, checks_total=4,
        retries=1, report_path="runs/s/final-report.md",
    )
    assert lines[0] == "Status: VERIFIED"
    assert "Goals: 3/3" in lines[1]
    assert "Files changed: 2" in lines[2]
    assert "Checks: 4/4" in lines[3]
    assert "Recovery attempts: 1" in lines[4]
    assert "runs/s/final-report.md" in lines[5]
