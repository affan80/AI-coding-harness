"""Live state and concise tool-event rendering (issue #28, PRD §§5.2, 7).

Rendering is a pure function from recorded events to bounded text lines, so
it is fully testable without a terminal and the TUI can reuse it. Rules:

* every visible line traces back to a stored event (seq + evidence id);
* raw output is never printed — tool lines show name, status, duration, and
  the evidence reference; full output lives in artifacts/;
* unbounded output cannot break the UI because each line is capped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

MAX_LINE_CHARS = 160

_TOOL_GLYPHS = {
    "ok": "✓",
    "error": "✗",
    "denied": "⊘",
    "timeout": "⏱",
}
_STATE_GLYPHS = {"done": "✓", "active": "→", "pending": "○"}


def _cap(text: str, limit: int = MAX_LINE_CHARS) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _tool_line(event: Mapping[str, Any]) -> str:
    data = event.get("data", {})
    glyph = _TOOL_GLYPHS.get(str(data.get("status", "")), "·")
    base = (
        f"{glyph} {data.get('tool', 'tool')} {data.get('status', '?')} "
        f"({data.get('duration_ms', 0)} ms)"
    )
    artifact = data.get("artifact")
    if artifact:
        base += f" [output: {artifact}]"
    elif data.get("truncated"):
        base += " [output truncated]"
    tool_call_id = event.get("tool_call_id") or ""
    return _cap(f"{base}  ev={tool_call_id}")


def render_lines(
    events: Iterable[Mapping[str, Any]],
    *,
    current_state: str | None = None,
) -> list[str]:
    """Render the execution view: state transitions + concise tool events."""
    lines: list[str] = []
    states_seen: list[str] = []
    for event in events:
        # RunStore JSONL uses "kind"; telemetry events use "type".
        kind = str(event.get("kind") or event.get("type", ""))
        if kind == "state":
            state = str(
                event.get("state")
                or event.get("message")
                or event.get("description", "")
            )
            states_seen.append(state)
            lines.append(_cap(f"{_STATE_GLYPHS['done']} {state}"))
        elif kind in ("tool", "tool_call"):
            lines.append(_tool_line(event))
        # every other kind stays out of the live view; the evidence lives in
        # events.jsonl and is surfaced through the final report instead
    if current_state:
        lines.append(_cap(f"{_STATE_GLYPHS['active']} {current_state}"))
    return lines


def render_live(recorder, current_state: str | None = None) -> str:
    """Render from a telemetry event log (``EventRecorder`` or ``RunStore``).

    Both event shapes are accepted: frozen dataclasses with ``to_dict`` and
    plain JSONL dictionaries.
    """
    normalized = []
    for event in recorder.events():
        if hasattr(event, "to_dict"):
            normalized.append(event.to_dict())
        else:
            normalized.append(event)
    return "\n".join(render_lines(normalized, current_state=current_state))


def render_final_summary(
    *,
    status: str,
    goals_completed: int,
    goals_total: int,
    changed_files: list[str],
    checks_passed: int,
    checks_total: int,
    retries: int,
    report_path: str,
    detail: str = "",
) -> list[str]:
    """Final block mirroring PRD §5.3 (issue #29 consumes this)."""
    lines = [
        f"Status: {status}",
        f"Goals: {goals_completed}/{goals_total}",
        f"Files changed: {len(changed_files)}",
        f"Checks: {checks_passed}/{checks_total} passed",
        f"Recovery attempts: {retries}",
    ]
    if detail:
        lines.append(f"Detail: {_cap(detail)}")
    lines.append(f"Report: {report_path}")
    return lines
