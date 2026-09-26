"""Executor loop: plan steps -> typed tool requests -> evidence (issue #11).

One call executes exactly one plan step and returns a :class:`StepOutcome`;
the orchestrator stays the sole owner of session state and decides what runs
next. Every mutation is checkpointed first (when the checkpoint tool is
registered), captured as evidence (old/new hashes, changed lines, patch id),
and inspected through git diff before control returns (issue #53).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.core.models import EvidenceRef, PlanStep, StepKind
from harness.execution.models import EditRequest, FailureKind, StepOutcome
from harness.tools.git_tools import Checkpoint, deserialize_checkpoint, rollback
from harness.tools.registry import ToolRegistry
from harness.tools.result import (
    DENIED,
    STALE_HASH,
    TIMEOUT,
    ToolResult,
)


class ExecutorLoop:
    """Translates one plan step into tool calls and reports the outcome."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.repo_root = registry.repo_root
        self.checkpoints: dict[str, Checkpoint] = {}

    async def execute(
        self,
        step: PlanStep,
        *,
        edit: EditRequest | None = None,
        command: str | None = None,
    ) -> StepOutcome:
        """Run one step through the tool boundary (never the reverse)."""
        if step.kind is StepKind.INSPECT:
            return self._inspect(step)
        if step.kind is StepKind.EDIT:
            return self._edit(step, edit)
        if step.kind is StepKind.SHELL:
            return self._shell(step, command)
        return self._verify(step, command)

    # -- action handlers -----------------------------------------------------

    def _inspect(self, step: PlanStep) -> StepOutcome:
        result = self.registry.call("executor", "read_file", path=self._target(step))
        if result.failed:
            return self._failed(step, result, FailureKind.TOOL)
        return StepOutcome(
            step_id=step.step_id,
            goal_id=step.goal_id,
            ok=True,
            summary=result.summary,
            evidence=(self._evidence(step, "tool_call", result),),
        )

    def _edit(self, step: PlanStep, edit: EditRequest | None) -> StepOutcome:
        if edit is None:
            return self._plan_failure(step, "EDIT step carries no EditRequest")
        path, path_error = self.registry.validate_path(edit.path)
        if path_error is not None:
            return self._failed(step, path_error, FailureKind.TOOL)
        assert path is not None
        if not path.is_file():
            return StepOutcome(
                step_id=step.step_id,
                goal_id=step.goal_id,
                ok=False,
                summary=f"patch target missing: {edit.path}",
                failure_kind=FailureKind.TOOL,
            )

        expected = edit.expected_old_hash or self.registry_hash(path)
        evidence: list[EvidenceRef] = []
        checkpoint_ref = self._checkpoint_before(step, edit.path)
        if checkpoint_ref is not None:
            evidence.append(checkpoint_ref)

        result = self.registry.call(
            "executor",
            "apply_patch",
            path=edit.path,
            diff=edit.diff,
            expected_old_hash=expected,
        )
        if result.failed:
            conflict = result.error_kind in (STALE_HASH, "conflict")
            kind = FailureKind.PATCH_CONFLICT if conflict else FailureKind.TOOL
            return self._failed(step, result, kind, evidence)

        evidence.append(self._evidence(step, "diff", result))
        # The changed file is known from the patch application itself; the
        # git-diff inspection below only adds review evidence.
        diff_evidence, _ = self._inspect_diff(step, edit.path)
        evidence.extend(diff_evidence)
        return StepOutcome(
            step_id=step.step_id,
            goal_id=step.goal_id,
            ok=True,
            summary=result.summary,
            changed_files=(edit.path,),
            evidence=tuple(evidence),
        )

    def _shell(self, step: PlanStep, command: str | None) -> StepOutcome:
        return self._run_command_step(step, command, test_evidence=False)

    def _verify(self, step: PlanStep, command: str | None) -> StepOutcome:
        return self._run_command_step(step, command, test_evidence=True)

    def _run_command_step(
        self, step: PlanStep, command: str | None, *, test_evidence: bool
    ) -> StepOutcome:
        command = command or (step.detail or None)
        if not command:
            return self._plan_failure(
                step, f"{step.kind.value} step carries no command"
            )
        result = self.registry.call("executor", "run_command", command=command)
        if result.failed:
            kind = FailureKind.TIMEOUT if result.error_kind == TIMEOUT else (
                FailureKind.DENIED if result.error_kind == DENIED else FailureKind.TOOL
            )
            if test_evidence:
                kind = FailureKind.TEST
            return self._failed(step, result, kind)
        evidence_kind = "test_run" if test_evidence else "tool_call"
        return StepOutcome(
            step_id=step.step_id,
            goal_id=step.goal_id,
            ok=True,
            summary=result.summary,
            evidence=(self._evidence(step, evidence_kind, result),),
        )

    # -- helpers ---------------------------------------------------------------

    def _target(self, step: PlanStep) -> str:
        return step.detail

    @staticmethod
    def registry_hash(path: Path) -> str:
        from harness.tools.files import file_hash

        return file_hash(path)

    def _checkpoint_before(self, step: PlanStep, path: str) -> EvidenceRef | None:
        """Best-effort checkpoint before a risky write (rollback hook, #56)."""
        result = self.registry.call("executor", "create_checkpoint", files_list=[path])
        if not result.ok:
            return None  # checkpoint tool not registered: proceed, no hidden failure
        checkpoint = result.data["checkpoint"]
        self.checkpoints[checkpoint["checkpoint_id"]] = deserialize_checkpoint(checkpoint)
        return EvidenceRef(
            kind="checkpoint",
            description=f"checkpoint before patch in step {step.step_id}",
            metadata={"checkpoint_id": checkpoint["checkpoint_id"]},
        )

    def _inspect_diff(
        self, step: PlanStep, path: str
    ) -> tuple[list[EvidenceRef], list[str]]:
        result = self.registry.call("executor", "git_diff", path=path)
        if not result.ok:
            return [], []
        evidence = [
            EvidenceRef(
                kind="diff",
                description=result.summary,
                path=result.artifacts[0] if result.artifacts else None,
                metadata={"step_id": step.step_id},
            )
        ]
        # git diff was scoped to this one path; a non-empty diff means the
        # mutation landed there and is now visible for review/rollback.
        return evidence, [path] if result.data["diff"].strip() else []

    @staticmethod
    def _evidence(step: PlanStep, kind: str, result: ToolResult) -> EvidenceRef:
        return EvidenceRef(
            kind=kind,
            description=f"[{step.step_id}] {result.summary}",
            path=result.artifacts[0] if result.artifacts else None,
            metadata={
                "step_id": step.step_id,
                "goal_id": step.goal_id,
                "truncated": result.truncated,
                "tool_result": result.to_dict(),
            },
        )

    def _failed(
        self,
        step: PlanStep,
        result: Any,
        kind: FailureKind,
        evidence: list[EvidenceRef] | None = None,
    ) -> StepOutcome:
        return StepOutcome(
            step_id=step.step_id,
            goal_id=step.goal_id,
            ok=False,
            summary=result.summary,
            failure_kind=kind,
            evidence=tuple(
                evidence
                if evidence is not None
                else [
                    EvidenceRef(
                        kind="tool_call",
                        description=f"[{step.step_id}] {result.summary}",
                        metadata={"error_kind": result.error_kind},
                    )
                ]
            ),
            detail=result.summary,
        )

    def _plan_failure(self, step: PlanStep, summary: str) -> StepOutcome:
        return StepOutcome(
            step_id=step.step_id,
            goal_id=step.goal_id,
            ok=False,
            summary=summary,
            failure_kind=FailureKind.PLAN,
        )


def rollback_checkpoint(executor: ExecutorLoop, checkpoint_id: str) -> bool:
    """Recovery-policy hook: restore a checkpoint made by this session only."""
    checkpoint = executor.checkpoints.get(checkpoint_id)
    if checkpoint is None:
        return False
    result = rollback(checkpoint, executor.repo_root)
    return result.ok
