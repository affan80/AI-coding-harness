"""Issue #28 — live state and concise tool event rendering."""

from __future__ import annotations

import io

from harness.core.models import SessionStatus
from harness.telemetry import ArtifactRef, ToolCallRecord
from tui.render import LINE_WIDTH, Renderer, SessionResult


def _tool_record(**overrides) -> ToolCallRecord:
    defaults = dict(
        id="tool-0001",
        seq=1,
        name="read_file",
        args_summary="app/auth.py",
        status="ok",
        started_at="2026-09-27T12:00:00+00:00",
        duration_ms=4,
        summary="file contents",
        truncated=False,
        artifact=None,
    )
    defaults.update(overrides)
    return ToolCallRecord(**defaults)


def _render() -> tuple[Renderer, io.StringIO]:
    buffer = io.StringIO()
    return Renderer(stdout=buffer), buffer


def test_tool_event_is_concise_single_line() -> None:
    renderer, buffer = _render()

    renderer.show_tool_event(_tool_record())

    output = buffer.getvalue()
    assert output == "read_file app/auth.py\n"


def test_error_status_is_marked() -> None:
    renderer, buffer = _render()

    renderer.show_tool_event(_tool_record(status="error"))

    assert "[error]" in buffer.getvalue()


def test_truncated_output_shows_evidence_reference_not_body() -> None:
    renderer, buffer = _render()
    record = _tool_record(
        truncated=True,
        artifact=ArtifactRef(
            path="artifacts/tool-0001-output.txt",
            sha256="a" * 64,
            size=90_000,
        ),
    )

    renderer.show_tool_event(record)

    output = buffer.getvalue()
    assert "artifacts/tool-0001-output.txt" in output
    assert "sha256:aaaaaaaaaaaa" in output
    assert "x" * 1000 not in output


def test_long_summaries_are_capped_to_line_width() -> None:
    renderer, buffer = _render()

    renderer.show_tool_event(
        _tool_record(args_summary="y" * 5000, summary="y" * 5000)
    )

    for line in buffer.getvalue().splitlines():
        assert len(line) <= LINE_WIDTH


def test_state_transitions_render_without_reasoning() -> None:
    renderer, buffer = _render()

    renderer.show_state("UNDERSTAND")

    assert buffer.getvalue() == "UNDERSTAND   ✓\n"


def test_final_summary_is_parseable_and_honest() -> None:
    renderer, buffer = _render()
    result = SessionResult(
        status=SessionStatus.PARTIAL,
        files_changed=["app/auth.py"],
        checks=["target-tests: 2 passed"],
        limitations=["full-suite regression pending replan"],
        report_path="runs/s-20260927-000000-abcd1234",
    )

    renderer.show_final(result)

    lines = buffer.getvalue().splitlines()
    assert "Status: PARTIAL" in lines
    assert "Files changed: 1" in lines
    assert "  M app/auth.py" in lines
    assert "Check: target-tests: 2 passed" in lines
    assert "Limitation: full-suite regression pending replan" in lines
    assert "Report: runs/s-20260927-000000-abcd1234" in lines
    # Unknown values are omitted, never invented.
    assert not any(line.startswith("Goals:") for line in lines)
    assert not any(line.startswith("Recovery") for line in lines)


def test_final_summary_includes_goals_when_known() -> None:
    renderer, buffer = _render()

    renderer.show_final(
        SessionResult(status=SessionStatus.VERIFIED, goals_completed=6, goals_total=6)
    )

    assert "Goals: 6/6" in buffer.getvalue()
