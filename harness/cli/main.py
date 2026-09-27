"""Session entry point: status rendering and reliable exit codes (issue #29).

Wires the CLI input (#27) and rendering (#28) into a runnable session:
state events flow to both the live view and the run directory, the runner
hook produces the outcome, the final report is persisted, and the process
exit code maps deterministically:

    VERIFIED -> 0, PARTIAL -> 1, FAILED -> 2, CANCELLED -> 3

so scripted runs can read the outcome from stdout plus the exit status.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from harness.cli.input import gather_request
from harness.cli.render import render_final_summary, render_lines
from harness.telemetry.events import EventRecorder, EventType
from harness.telemetry.report import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_VERIFIED,
    CheckResult,
    ReportInput,
    generate_report,
)
from harness.telemetry.store import RunStore


class ExitCode(IntEnum):
    VERIFIED = 0
    PARTIAL = 1
    FAILED = 2
    CANCELLED = 3


STATUS_EXIT_CODES: dict[str, ExitCode] = {
    STATUS_VERIFIED: ExitCode.VERIFIED,
    STATUS_PARTIAL: ExitCode.PARTIAL,
    STATUS_FAILED: ExitCode.FAILED,
    "CANCELLED": ExitCode.CANCELLED,
}


@dataclass
class SessionSummary:
    """What a runner reports at FINALIZE (PRD §5.3)."""

    status: str  # VERIFIED | PARTIAL | FAILED | CANCELLED
    goals_completed: int = 0
    goals_total: int = 0
    changed_files: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    retries: int = 0
    limitations: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    detail: str = ""
    goals: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    """Ordered plan steps, when the runner produced one (PRD §14 shape)."""


Runner = Callable[..., SessionSummary]
"""(request, budget, run_store, event_recorder) -> SessionSummary"""


def emit_both(recorder: EventRecorder, store: RunStore, kind: str,
              message: str, **data: Any) -> None:
    """One event, two sinks: live rendering and persisted evidence."""
    recorder.emit(EventType(kind), message, data=data or None)
    store.append_event(kind, message, data=dict(data))


def run_session(
    args: argparse.Namespace,
    runner: Runner,
    *,
    prompt: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Run one session end-to-end and return the process exit code."""
    request, budget, _resolved = gather_request(args, prompt=prompt)
    store = RunStore.start(request, runs_root=args.runs_root)
    recorder = EventRecorder()
    emit_both(recorder, store, "state", "INITIALIZE -> UNDERSTAND")

    try:
        summary = runner(request, budget, store, recorder)
    except KeyboardInterrupt:
        summary = SessionSummary(
            status="CANCELLED", detail="interrupted by user",
        )
        emit_both(recorder, store, "state", "session cancelled by user")

    # Evidence: goals, plan, checks, changed files, and the final report.
    store.write_document("goals.json", {"goals": summary.goals})
    if summary.plan:
        store.write_document("plan.json", {"steps": summary.plan})
    store.write_verification({
        "status": "pass" if summary.checks and all(
            c.passed for c in summary.checks
        ) else "fail" if summary.checks else "not_run",
        "checks": [
            {"name": c.name, "command": c.command, "exit_code": c.exit_code,
             "ok": c.passed, "summary": c.output_summary}
            for c in summary.checks
        ],
        "notes": summary.limitations,
    })
    store.record_changed_files(summary.changed_files)

    metrics_events = recorder.events()
    from harness.telemetry.metrics import UsageMetrics

    report = generate_report(ReportInput(
        session_id=store.session_id,
        objective=request.objective,
        status=summary.status if summary.status in (
            STATUS_VERIFIED, STATUS_PARTIAL, STATUS_FAILED,
        ) else STATUS_FAILED,
        detail=summary.detail or (
            "cancelled" if summary.status == "CANCELLED" else ""
        ),
        goals=summary.goals,
        changed_files=summary.changed_files,
        checks=list(summary.checks),
        findings=summary.findings,
        limitations=summary.limitations,
        metrics=UsageMetrics.from_events(metrics_events),
        events=metrics_events,
        evidence=[{"ref_id": "ev-run-dir", "kind": "artifact",
                   "description": "run directory", "path": str(store.run_dir)}],
    ))
    report_path = store.paths.document("final-report.md")
    store.write_text_document("final-report.md", report)
    store.finalize(
        _session_status(summary.status),
        stop_reason=summary.detail,
    )

    # Final view: everything a scripted consumer needs on stdout.
    for line in render_lines(recorder.events()):
        out(line)
    for line in render_final_summary(
        status=summary.status,
        goals_completed=summary.goals_completed,
        goals_total=summary.goals_total,
        changed_files=summary.changed_files,
        checks_passed=sum(1 for c in summary.checks if c.passed),
        checks_total=len(summary.checks),
        retries=summary.retries,
        report_path=str(report_path),
        detail=summary.detail,
    ):
        out(line)
    return STATUS_EXIT_CODES[summary.status].value


def _session_status(status: str) -> Any:
    from harness.core.models import SessionStatus

    mapping = {
        STATUS_VERIFIED: SessionStatus.VERIFIED,
        STATUS_PARTIAL: SessionStatus.PARTIAL,
        STATUS_FAILED: SessionStatus.FAILED,
        "CANCELLED": SessionStatus.CANCELLED,
    }
    return mapping[status]


def main(argv: list[str] | None = None) -> int:
    """Console entry point; the default runner is the scripted self-check."""
    from harness.cli.input import build_parser

    parser = build_parser()
    parser.add_argument(
        "--self-check", action="store_true",
        help="run the deterministic built-in scenario instead of a live model",
    )
    args = parser.parse_args(argv)
    runner = self_check_runner if args.self_check else _no_engine_runner
    return run_session(args, runner)


def _no_engine_runner(request, budget, store, recorder) -> SessionSummary:
    """Honest placeholder: no model engine is wired in this repository yet."""
    emit_both(recorder, store, "failure",
              "no model engine configured; wire a Runner to run real sessions")
    return SessionSummary(
        status=STATUS_FAILED,
        detail="no model engine configured; pass runner= to run_session()",
        limitations=[
            "autonomous execution requires wiring a Runner (model client + orchestrator)",
        ],
    )


def self_check_runner(request, budget, store, recorder) -> SessionSummary:
    """Deterministic VERIFIED outcome for CI/demo use (--self-check)."""
    emit_both(recorder, store, "state", "self-check goal verified")
    return SessionSummary(
        status=STATUS_VERIFIED,
        goals_completed=1,
        goals_total=1,
        changed_files=[],
        checks=[CheckResult(
            name="self-check", command="harness --self-check", passed=True,
            exit_code=0, output_summary="deterministic self-check passed",
        )],
        goals=[{"id": "G1", "title": "self-check", "status": "COMPLETED",
                "acceptance_criteria": ["exit code 0"]}],
        plan=[{"id": "S1", "action": "verify", "target": "self-check",
               "depends_on": []}],
    )
