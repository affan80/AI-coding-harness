"""Reproducing suspected findings with deterministic evidence (#67).

A reproducer asserts the EXPECTED behavior — exactly like the ladder's
reproducer stage — so a failing run demonstrates the defect and a passing
run rejects it. Failed reproduction attempts are preserved on the finding
as evidence alongside the rejection reason.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from harness.audit.models import Finding, FindingStatus, transition
from harness.core.models import EvidenceRef
from harness.execution.runner import run_command
from harness.telemetry.models import ArtifactRef
from harness.telemetry.store import RunStore

_OUTPUT_TAIL = 300


@dataclass(frozen=True)
class ReproductionResult:
    """Outcome of one reproduction attempt; kept even when rejected."""

    finding_id: str
    attempted: bool
    reproduced: bool
    reason: str
    command: str | None = None
    exit_code: int | None = None
    output_tail: str = ""
    artifact: ArtifactRef | None = None

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "attempted": self.attempted,
            "reproduced": self.reproduced,
            "reason": self.reason,
            "command": self.command,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
            "artifact": self.artifact.to_dict() if self.artifact else None,
        }


def attempt_reproduction(
    finding: Finding,
    cwd: Path,
    runner: Callable = run_command,
    timeout_seconds: float = 120.0,
    store: RunStore | None = None,
) -> ReproductionResult:
    """Run the finding's proposed reproducer and classify the attempt."""
    if finding.proposed_reproducer is None:
        return ReproductionResult(
            finding_id=finding.finding_id,
            attempted=False,
            reproduced=False,
            reason="no reproducer proposed for this finding",
        )

    import shlex

    argv = tuple(shlex.split(finding.proposed_reproducer))
    result = runner(argv, cwd, timeout_seconds)

    artifact: ArtifactRef | None = None
    output = result.combined_output
    if store is not None and output:
        record = store.record_tool_call(
            name=f"audit-reproduce:{finding.finding_id}",
            args_summary=finding.proposed_reproducer,
            status="ok" if result.exit_code == 0 else "error",
            started_at=datetime.now().isoformat(),
            duration_ms=result.duration_ms,
            output=output,
        )
        artifact = record.artifact

    if not result.ran:
        return ReproductionResult(
            finding_id=finding.finding_id,
            attempted=True,
            reproduced=False,
            reason=f"reproducer could not run: {result.unavailable_reason}",
            command=finding.proposed_reproducer,
            output_tail=output[-_OUTPUT_TAIL:],
            artifact=artifact,
        )
    if result.timed_out:
        return ReproductionResult(
            finding_id=finding.finding_id,
            attempted=True,
            reproduced=False,
            reason="reproducer timed out; the defect could not be demonstrated",
            command=finding.proposed_reproducer,
            exit_code=result.exit_code,
            output_tail=output[-_OUTPUT_TAIL:],
            artifact=artifact,
        )
    if result.exit_code == 0:
        return ReproductionResult(
            finding_id=finding.finding_id,
            attempted=True,
            reproduced=False,
            reason="reproducer passed; the suspected defect did not reproduce",
            command=finding.proposed_reproducer,
            exit_code=0,
            output_tail=output[-_OUTPUT_TAIL:],
            artifact=artifact,
        )
    return ReproductionResult(
        finding_id=finding.finding_id,
        attempted=True,
        reproduced=True,
        reason=f"reproducer failed with exit code {result.exit_code}, demonstrating the defect",
        command=finding.proposed_reproducer,
        exit_code=result.exit_code,
        output_tail=output[-_OUTPUT_TAIL:],
        artifact=artifact,
    )


def apply_reproduction(finding: Finding, result: ReproductionResult) -> Finding:
    """Move the finding to REPRODUCED or REJECTED based on the attempt."""
    evidence_ref = None
    if result.attempted:
        # Preserve the attempt itself as evidence, reproduced or not.
        evidence_ref = EvidenceRef(
            kind="reproducer" if result.reproduced else "reproduction_attempt",
            description=result.reason,
            path=result.artifact.path if result.artifact else None,
            sha256=result.artifact.sha256 if result.artifact else None,
            metadata={
                "command": result.command or "",
                "exit_code": result.exit_code,
                "output_tail": result.output_tail,
            },
        )
    if result.reproduced:
        return transition(finding, FindingStatus.REPRODUCED, evidence=evidence_ref)
    return transition(
        finding,
        FindingStatus.REJECTED,
        evidence=evidence_ref,
        reason=result.reason,
    )


def confirm(finding: Finding) -> Finding:
    """Accept a reproduced finding as repair work (deterministic gate)."""
    return transition(finding, FindingStatus.CONFIRMED)
