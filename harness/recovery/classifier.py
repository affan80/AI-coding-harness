"""Deterministic failure classification and focused evidence collection (#63).

Classification uses structured inputs only — stage identity, exit codes,
timeout flags, availability — never free-text guessing, so an environment
failure (missing executable, unwritable path) can never be misreported as a
source-code defect.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.execution.runner import CommandResult
from harness.recovery.models import AttemptRecord, FailureClass, FailureEvidence
from harness.telemetry.models import ArtifactRef
from harness.verification.models import StageName, StageResult

_ERROR_LIMIT = 300


@dataclass(frozen=True)
class FailureInput:
    """Structured facts about one failure; no run history attached."""

    goal_id: str
    stage: StageName | None = None  # verification stage that failed, if any
    command_result: CommandResult | None = None
    patch_failed: bool = False
    plan_invalid: bool = False
    loop_detected: bool = False
    latest_diff: str | None = None
    relevant_tests: tuple[str, ...] = ()
    prior_attempts: tuple[AttemptRecord, ...] = ()
    artifact: ArtifactRef | None = None
    error_text: str | None = None  # for failures without a command (plan/context)


def classify_failure(failure: FailureInput) -> FailureClass:
    """Map structured facts to one PRD §17 failure class."""
    result = failure.command_result
    if failure.loop_detected:
        return FailureClass.LOOP
    if failure.plan_invalid:
        return FailureClass.PLAN
    if failure.patch_failed:
        return FailureClass.PATCH
    if result is not None:
        if not result.ran:
            # Could not even start: missing executable, OS-level error.
            return FailureClass.ENVIRONMENT
        if result.timed_out:
            return FailureClass.TIMEOUT
    if failure.stage is StageName.SYNTAX:
        return FailureClass.SYNTAX
    if failure.stage is StageName.BUILD:
        return FailureClass.BUILD
    if failure.stage in (
        StageName.REPRODUCER,
        StageName.TARGETED_TESTS,
        StageName.RELATED_TESTS,
        StageName.FULL_SUITE,
    ):
        return FailureClass.TEST
    if failure.stage is StageName.DIFF_SCOPE:
        # Scope violations are patch problems: the change went somewhere
        # it was not approved to go.
        return FailureClass.PATCH
    if failure.error_text is not None:
        return FailureClass.TOOL
    return FailureClass.TOOL


def collect_failure_evidence(
    failure: FailureInput,
    failure_class: FailureClass | None = None,
) -> FailureEvidence:
    """Narrow a failure to the recovery input bundle (PRD §17).

    Deliberately takes no session/run-history parameter: what the recovery
    step sees is exactly what this function puts in the bundle.
    """
    result = failure.command_result
    command_text: str | None = None
    exit_code: int | None = None
    timed_out = False
    error_text = failure.error_text or ""

    if result is not None:
        command_text = " ".join(result.argv)
        exit_code = result.exit_code
        timed_out = result.timed_out
        raw_error = result.combined_output
    else:
        raw_error = error_text

    affected_paths = _paths_from_error(error_text or raw_error) or _paths_from_diff(
        failure.latest_diff
    )

    return FailureEvidence(
        failure_class=failure_class or classify_failure(failure),
        concise_error=_focus(raw_error),
        goal_id=failure.goal_id,
        stage=failure.stage.value if failure.stage else None,
        failing_command=command_text,
        exit_code=exit_code,
        timed_out=timed_out,
        latest_diff=failure.latest_diff,
        affected_paths=tuple(sorted(set(affected_paths))),
        relevant_tests=tuple(failure.relevant_tests),
        prior_attempts=tuple(failure.prior_attempts),
        artifact=failure.artifact,
    )


def _focus(text: str | None, limit: int = _ERROR_LIMIT) -> str:
    """Keep the tail of failing output, where summaries live."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "... " + text[-(limit - 4):]


def _paths_from_error(text: str) -> list[str]:
    """Extract candidate file paths mentioned in an error message."""
    import re

    matches = re.findall(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|json|toml|yaml|yml)", text)
    return [m.lstrip("./") for m in matches]


def _paths_from_diff(diff_text: str | None) -> list[str]:
    if not diff_text:
        return []
    from harness.verification.ladder import changed_files_from_diff

    return changed_files_from_diff(diff_text)


def stage_result_to_input(
    goal_id: str,
    stage: StageResult,
    command_result: CommandResult | None = None,
    latest_diff: str | None = None,
    relevant_tests: tuple[str, ...] = (),
    prior_attempts: tuple[AttemptRecord, ...] = (),
) -> FailureInput:
    """Build a FailureInput from a failed verification stage result."""
    return FailureInput(
        goal_id=goal_id,
        stage=stage.name,
        command_result=command_result,
        latest_diff=latest_diff,
        relevant_tests=relevant_tests,
        prior_attempts=prior_attempts,
        error_text=stage.summary,
    )
