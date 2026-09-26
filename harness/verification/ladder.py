"""Ladder execution: ordered stages, first-failure stop, honest statuses.

Gating rules (all deterministic; no model verdict input exists):
1. Stages are considered in PRD §16 order.
2. The first FAILED stage stops the ladder; later stages are recorded as
   skipped with reason "not reached".
3. UNAVAILABLE (no runnable command) and SKIPPED (not applicable) never
   block on their own.
4. VERIFIED requires: no FAILED stage, DIFF_SCOPE passed, and at least one
   test-bearing stage (reproducer/targeted/related/full suite) passed.
   Without test evidence the status is INCONCLUSIVE, never VERIFIED.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from harness.adapters.base import Command, ProjectAdapter, select_adapter
from harness.execution.runner import CommandResult, run_command
from harness.telemetry.store import RunStore
from harness.verification.models import (
    LADDER_ORDER,
    TEST_BEARING_STAGES,
    StageName,
    StageOutcome,
    StageResult,
    VerificationInput,
    VerificationReport,
    VerificationStatus,
)

_SUMMARY_LIMIT = 300


def run_verification_ladder(
    goal: VerificationInput,
    cwd: Path,
    runner: Callable[[tuple[str, ...], Path, float], CommandResult] = run_command,
    adapter: ProjectAdapter | None = None,
    store: RunStore | None = None,
    timeout_seconds: float = 120.0,
    clock: Callable[[], datetime] = datetime.now,
) -> VerificationReport:
    """Run the ladder for one goal and return the structured report."""
    if adapter is None:
        adapter = select_adapter(cwd)
    changed_files = changed_files_from_diff(goal.diff_text)

    stages: list[StageResult] = []
    first_failure: StageResult | None = None
    stopped = False

    for stage_name in LADDER_ORDER:
        if stopped:
            stages.append(_not_reached(stage_name))
            continue
        stage = _evaluate_stage(
            stage_name,
            goal,
            cwd,
            adapter,
            changed_files,
            runner,
            store,
            timeout_seconds,
            clock,
        )
        stages.append(stage)
        if stage.outcome == StageOutcome.FAILED:
            first_failure = stage
            stopped = True

    report = VerificationReport(
        goal_id=goal.goal_id,
        status=_final_status(stages),
        stages=stages,
        first_failure=first_failure,
    )
    if store is not None:
        store.write_verification(report.to_dict())
    return report


def _evaluate_stage(
    stage_name: StageName,
    goal: VerificationInput,
    cwd: Path,
    adapter: ProjectAdapter | None,
    changed_files: list[str],
    runner: Callable[[tuple[str, ...], Path, float], CommandResult],
    store: RunStore | None,
    timeout_seconds: float,
    clock: Callable[[], datetime],
) -> StageResult:
    decision = _decide_stage(stage_name, goal, cwd, adapter, changed_files)
    if isinstance(decision, StageResult):
        return decision

    command: Command = decision
    result = runner(command.argv, cwd, timeout_seconds)
    if not result.ran:
        return _record(
            StageResult(
                name=stage_name,
                outcome=StageOutcome.UNAVAILABLE,
                command=_argv_text(command),
                duration_ms=result.duration_ms,
                summary=result.unavailable_reason or "command could not be started",
            ),
            result.combined_output,
            store,
            clock,
        )
    if result.timed_out:
        outcome = StageOutcome.FAILED
        summary = f"timed out after {timeout_seconds}s"
    elif result.exit_code == 0:
        outcome = StageOutcome.PASSED
        summary = command.label
    else:
        outcome = StageOutcome.FAILED
        summary = _focus(result.combined_output)
    return _record(
        StageResult(
            name=stage_name,
            outcome=outcome,
            command=_argv_text(command),
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            summary=summary,
        ),
        result.combined_output,
        store,
        clock,
    )


def _decide_stage(
    stage_name: StageName,
    goal: VerificationInput,
    cwd: Path,
    adapter: ProjectAdapter | None,
    changed_files: list[str],
) -> Command | StageResult:
    """Map one stage to a runnable command or an immediate non-running result."""
    if adapter is None:
        if stage_name is StageName.DIFF_SCOPE:
            return _diff_scope_result(goal)
        return StageResult(
            name=stage_name,
            outcome=StageOutcome.UNAVAILABLE,
            summary="no project adapter detected for this repository",
        )

    if stage_name is StageName.SYNTAX:
        command = adapter.syntax_command(cwd, changed_files)
        if command is None:
            return StageResult(
                name=stage_name,
                outcome=StageOutcome.SKIPPED,
                summary="no applicable changed source files",
            )
        return command
    if stage_name is StageName.BUILD:
        command = adapter.build_or_typecheck_command(cwd, changed_files)
        if command is None:
            return StageResult(
                name=stage_name,
                outcome=StageOutcome.SKIPPED,
                summary="no build/type-check command configured",
            )
        return command
    if stage_name is StageName.REPRODUCER:
        if goal.reproducer_command is None:
            return StageResult(
                name=stage_name,
                outcome=StageOutcome.SKIPPED,
                summary="no reproducer available for this goal",
            )
        return Command(
            argv=tuple(shlex.split(goal.reproducer_command)),
            label=f"reproducer: {goal.reproducer_command}",
        )
    if stage_name is StageName.TARGETED_TESTS:
        command = adapter.target_test_command(cwd, list(goal.targeted_tests))
        if command is None:
            if not goal.targeted_tests:
                return StageResult(
                    name=stage_name,
                    outcome=StageOutcome.SKIPPED,
                    summary="no targeted tests specified",
                )
            return StageResult(
                name=stage_name,
                outcome=StageOutcome.SKIPPED,
                summary="project provides no test command",
            )
        return command
    if stage_name is StageName.RELATED_TESTS:
        command = adapter.target_test_command(cwd, list(goal.related_tests))
        if command is None:
            if not goal.related_tests:
                return StageResult(
                    name=stage_name,
                    outcome=StageOutcome.SKIPPED,
                    summary="no related tests specified",
                )
            return StageResult(
                name=stage_name,
                outcome=StageOutcome.SKIPPED,
                summary="project provides no test command",
            )
        return command
    if stage_name is StageName.FULL_SUITE:
        command = adapter.full_test_command(cwd)
        if command is None:
            return StageResult(
                name=stage_name,
                outcome=StageOutcome.SKIPPED,
                summary="project provides no test command",
            )
        return command
    if stage_name is StageName.DIFF_SCOPE:
        return _diff_scope_result(goal)
    raise ValueError(f"unknown ladder stage: {stage_name}")


def _diff_scope_result(goal: VerificationInput) -> StageResult:
    """Deterministic diff/scope validation: no command, no discretion."""
    changed_files = changed_files_from_diff(goal.diff_text)
    if not goal.allowed_scope:
        return StageResult(
            name=StageName.DIFF_SCOPE,
            outcome=StageOutcome.PASSED,
            summary=f"{len(changed_files)} changed file(s) within repository scope",
        )
    out_of_scope = sorted(
        path for path in changed_files if not _in_scope(path, goal.allowed_scope)
    )
    if out_of_scope:
        return StageResult(
            name=StageName.DIFF_SCOPE,
            outcome=StageOutcome.FAILED,
            summary="out-of-scope changes: " + ", ".join(out_of_scope),
        )
    return StageResult(
        name=StageName.DIFF_SCOPE,
        outcome=StageOutcome.PASSED,
        summary=(
            f"{len(changed_files)} changed file(s) within allowed scope: "
            + ", ".join(goal.allowed_scope)
        ),
    )


def _in_scope(path: str, allowed_scope: tuple[str, ...]) -> bool:
    return any(path == scope or path.startswith(scope.rstrip("/") + "/") for scope in allowed_scope)


def changed_files_from_diff(diff_text: str | None) -> list[str]:
    """Extract the changed files from unified diff text, sorted and unique."""
    if not diff_text:
        return []
    files: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            path = line[4:].split("\t", 1)[0]
            if path.startswith("b/"):
                path = path[2:]
            if path:
                files.add(path)
    return sorted(files)


def _record(
    stage: StageResult,
    raw_output: str,
    store: RunStore | None,
    clock: Callable[[], datetime],
) -> StageResult:
    """Attach evidence-tool artifacts when a run store is provided."""
    if store is None:
        return stage
    if stage.outcome not in (StageOutcome.PASSED, StageOutcome.FAILED):
        return stage
    record = store.record_tool_call(
        name=f"verify:{stage.name.value}",
        args_summary=stage.command or "",
        status="ok" if stage.outcome is StageOutcome.PASSED else "error",
        started_at=clock().isoformat(),
        duration_ms=stage.duration_ms,
        output=raw_output or None,
    )
    return StageResult(
        name=stage.name,
        outcome=stage.outcome,
        command=stage.command,
        exit_code=stage.exit_code,
        duration_ms=stage.duration_ms,
        summary=stage.summary,
        artifact=record.artifact,
    )


def _not_reached(stage_name: StageName) -> StageResult:
    return StageResult(
        name=stage_name,
        outcome=StageOutcome.SKIPPED,
        summary="not reached: ladder stopped at an earlier failure",
    )


def _final_status(stages: list[StageResult]) -> VerificationStatus:
    by_name = {stage.name: stage for stage in stages}
    if any(stage.outcome is StageOutcome.FAILED for stage in stages):
        return VerificationStatus.FAILED
    diff_scope = by_name.get(StageName.DIFF_SCOPE)
    if diff_scope is None or diff_scope.outcome is not StageOutcome.PASSED:
        return VerificationStatus.INCONCLUSIVE
    test_evidence = any(
        stage.name in TEST_BEARING_STAGES and stage.outcome is StageOutcome.PASSED
        for stage in stages
    )
    if not test_evidence:
        return VerificationStatus.INCONCLUSIVE
    return VerificationStatus.VERIFIED


def _argv_text(command: Command) -> str:
    return " ".join(shlex.quote(part) for part in command.argv)


def _focus(text: str, limit: int = _SUMMARY_LIMIT) -> str:
    """Keep the tail of failing output, where summaries live."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return "... " + text[-limit:]
