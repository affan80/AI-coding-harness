"""Honest final evidence reports (issue #71).

Generates ``final-report.md`` for every terminal outcome — VERIFIED, PARTIAL,
and FAILED sessions all state exactly what ran. The report contains goals,
changed files, exact verification outcomes, findings, limitations, usage
metrics, evidence links, and the discovered-versus-selected context proof
(PRD §21, §25; FR-12, FR-13).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.telemetry.events import Event
from harness.telemetry.metrics import UsageMetrics

STATUS_VERIFIED = "VERIFIED"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"


@dataclass
class CheckResult:
    """One deterministic check with its exact, honest outcome."""

    name: str
    command: str
    passed: bool
    output_summary: str = ""
    exit_code: int | None = None

    def to_line(self) -> str:
        outcome = "PASS" if self.passed else "FAIL"
        line = f"- {outcome} — {self.name}: `{self.command}`"
        if self.exit_code is not None:
            line += f" (exit {self.exit_code})"
        if self.output_summary:
            line += f" — {self.output_summary}"
        return line


@dataclass
class ReportInput:
    """Everything the report needs; assembled by the caller at FINALIZE."""

    session_id: str
    objective: str
    status: str  # VERIFIED | PARTIAL | FAILED
    detail: str = ""
    goals: list[Mapping[str, Any]] = field(default_factory=list)
    # each goal: {id, title, status, acceptance_criteria}
    changed_files: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    recovery: list[str] = field(default_factory=list)
    metrics: UsageMetrics = field(default_factory=UsageMetrics)
    events: list[Event] = field(default_factory=list)
    evidence: list[Mapping[str, Any]] = field(default_factory=list)
    # each evidence: {ref_id, kind, description, path?}

    def __post_init__(self) -> None:
        if self.status not in (STATUS_VERIFIED, STATUS_PARTIAL, STATUS_FAILED):
            raise ValueError(
                f"status must be {STATUS_VERIFIED}, {STATUS_PARTIAL}, or "
                f"{STATUS_FAILED}, got {self.status!r}"
            )


def generate_report(data: ReportInput) -> str:
    """Render the final markdown report for the session."""
    lines: list[str] = []
    lines.append(f"# Final report — session {data.session_id}")
    lines.append("")
    lines.append(f"**Status: {data.status}**")
    if data.detail:
        lines.append(f"<p>{data.detail}</p>")
    lines.append("")
    lines.append(f"**Objective:** {data.objective}")
    lines.append("")

    # Goals
    lines.append("## Goals")
    if not data.goals:
        lines.append("- none recorded")
    for goal in data.goals:
        criteria = goal.get("acceptance_criteria") or []
        rendered = goal.get("status", "?")
        lines.append(f"- [{rendered}] {goal.get('id', '?')}: {goal.get('title', '?')}")
        for criterion in criteria:
            lines.append(f"  - acceptance: {criterion}")
    lines.append("")

    # Changes
    lines.append("## Changed files")
    if not data.changed_files:
        lines.append("- none")
    for path in data.changed_files:
        lines.append(f"- `{path}`")
    lines.append("")

    # Checks
    lines.append("## Verification checks")
    if not data.checks:
        lines.append("- no deterministic checks were run")
    for check in data.checks:
        lines.append(check.to_line())
    lines.append("")

    # Recovery
    if data.recovery:
        lines.append("## Recovery")
        for entry in data.recovery:
            lines.append(f"- {entry}")
        lines.append("")

    # Findings
    lines.append("## Findings")
    if not data.findings:
        lines.append("- none")
    for finding in data.findings:
        lines.append(f"- {finding}")
    lines.append("")

    # Limitations
    lines.append("## Limitations")
    if not data.limitations:
        lines.append("- none recorded")
    for limitation in data.limitations:
        lines.append(f"- {limitation}")
    lines.append("")

    # Context proof
    m = data.metrics
    lines.append("## Context selection")
    lines.append(
        f"- discovered: {m.context_discovered_files} files / "
        f"{m.context_discovered_tokens} tokens"
    )
    lines.append(
        f"- selected into context: {m.context_selected_files} files / "
        f"{m.context_selected_tokens} tokens"
    )
    if m.context_discovered_tokens:
        pct = 100.0 * m.context_selected_tokens / m.context_discovered_tokens
        lines.append(f"- selection ratio: {pct:.1f}% of discovered tokens")
    if m.compaction_events:
        lines.append(f"- compaction events: {m.compaction_events}")
    lines.append("")

    # Metrics
    lines.append("## Usage metrics")
    lines.append("```text")
    lines.extend(m.render_lines())
    lines.append("```")
    lines.append("")

    # Evidence
    lines.append("## Evidence")
    if not data.evidence:
        lines.append("- no evidence artifacts recorded")
    for ref in data.evidence:
        path = f" ({ref['path']})" if ref.get("path") else ""
        lines.append(
            f"- `{ref.get('ref_id', '?')}` [{ref.get('kind', '?')}] "
            f"{ref.get('description', '')}{path}"
        )
    lines.append("")

    # Event tail: last 20 events as the run's audit trail
    lines.append("## Event log (tail)")
    lines.append("```text")
    for event in data.events[-20:]:
        lines.append(
            f"{event.seq:>4} {event.type.value:<13} {event.description}"
        )
    lines.append("```")
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, content: str) -> Path:
    """Persist ``final-report.md`` inside the run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "final-report.md"
    path.write_text(content, encoding="utf-8")
    return path
