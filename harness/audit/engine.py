"""Audit engine: rounds, scope guard, baseline checks, repair routing (#68).

Round and scope budgets come from upstream governance (``AUDIT_ROUNDS`` on
the core budget) and the session's allowed scope. Confirmed findings are
converted into ordinary verification inputs — the same ladder, budgets, and
scope rules as user-requested goals — and nothing else can create work.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from harness.adapters.base import ProjectAdapter
from harness.audit.models import (
    AuditReport,
    AuditRoundReport,
    Finding,
    FindingStatus,
    transition,
)
from harness.audit.reproduce import apply_reproduction, attempt_reproduction, confirm
from harness.core.budgets import BudgetKind, consume, has_headroom
from harness.core.models import Budget, BudgetUsage, EvidenceRef
from harness.execution.runner import run_command
from harness.model.types import Message
from harness.telemetry.store import RunStore
from harness.verification.models import GoalKind, VerificationInput


class AuditScopeError(Exception):
    """The audit attempted to exceed the session's allowed scope."""


@dataclass(frozen=True)
class ReviewCandidate:
    """One finding proposed by the focused model review (never repair work)."""

    title: str
    description: str
    scope_path: str
    proposed_reproducer: str | None = None


def parse_review(response_text: str, allowed_scope: Sequence[str]) -> list[ReviewCandidate]:
    """Parse the reviewer's JSON output into validated, scope-checked candidates.

    Candidates outside the session's allowed scope are dropped and recorded
    as rejected so the audit cannot expand beyond approved paths.
    """
    try:
        data = json.loads(response_text)
    except ValueError as exc:
        raise ValueError(f"audit review is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("audit review must be a JSON object with a 'findings' list")

    candidates: list[ReviewCandidate] = []
    for item in data["findings"]:
        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        scope_path = str(item.get("scope_path", "")).strip()
        if not title or not description or not scope_path:
            continue  # malformed candidates are dropped, not guessed into shape
        if allowed_scope and not _within_scope(scope_path, allowed_scope):
            continue  # outside allowed scope: the audit cannot expand there
        candidates.append(
            ReviewCandidate(
                title=title,
                description=description,
                scope_path=scope_path,
                proposed_reproducer=item.get("proposed_reproducer"),
            )
        )
    return candidates


def _within_scope(path: str, allowed_scope: Sequence[str]) -> bool:
    return any(
        path == scope or path.startswith(scope.rstrip("/") + "/") for scope in allowed_scope
    )


def finding_to_verification_input(finding: Finding) -> VerificationInput:
    """Convert a CONFIRMED finding into an ordinary repair goal.

    Raises :class:`InvalidTransitionError`-adjacent errors for anything that
    has not passed the deterministic evidence gate: only confirmed findings
    (with their proven reproducer) enter planning and verification.
    """
    if finding.status is not FindingStatus.CONFIRMED:
        raise AuditScopeError(
            f"finding {finding.finding_id} is {finding.status.value}; "
            "only CONFIRMED findings become repair goals"
        )
    return VerificationInput(
        goal_id=f"audit-{finding.finding_id}",
        goal_kind=GoalKind.AUDIT,
        reproducer_command=finding.proposed_reproducer,
        diff_text=None,
        allowed_scope=(finding.scope_path,),
    )


@dataclass
class AuditGovernor:
    """Round and scope budgets for one audit engagement."""

    budget: Budget
    usage: BudgetUsage
    allowed_scope: tuple[str, ...] = ()
    rounds_used: int = 0

    def begin_round(self) -> bool:
        """True when another audit round is allowed by the round budget."""
        return has_headroom(self.budget, self.usage, BudgetKind.AUDIT_ROUNDS)

    def end_round(self) -> None:
        self.usage = consume(self.budget, self.usage, BudgetKind.AUDIT_ROUNDS)
        self.rounds_used += 1

    def scope_ok(self, path: str) -> bool:
        if not self.allowed_scope:
            return True
        return _within_scope(path, self.allowed_scope)


async def run_audit(
    cwd: Path,
    reviewer,
    adapter: ProjectAdapter,
    governor: AuditGovernor,
    runner: Callable = run_command,
    store: RunStore | None = None,
    timeout_seconds: float = 120.0,
) -> AuditReport:
    """Run audit rounds: baseline checks, focused review, reproduction gate.

    The reviewer receives the affected-scope file list and returns candidate
    findings; only deterministic evidence (baseline tool results, failing
    reproducers) promotes anything toward repair. Rounds stop when the round
    budget is exhausted or a round produces no new findings.
    """
    report = AuditReport()
    round_number = 0

    # One audit pass per engagement; the orchestrator re-engages after
    # routed repairs, and the AUDIT_ROUNDS budget bounds total engagements.
    if not governor.begin_round():
        report.stopped_reason = "audit rounds exhausted (budget)"
        store_report(store, report)
        return report

    while round_number < 1:
        round_number += 1
        round_findings: list[Finding] = []

        # Deterministic baseline checks in the affected scope: lint,
        # typecheck, build — a non-zero exit is tool-result evidence.
        for command in (*adapter.lint_commands(cwd), *adapter.typecheck_commands(cwd)):
            result = runner(tuple(command.argv), cwd, timeout_seconds)
            if result.ran and not result.timed_out and result.exit_code != 0:
                finding = Finding(
                    finding_id=f"audit-r{round_number}-{command.kind.value}-{len(round_findings)}",
                    title=(
                        f"{command.kind.value} check failed: "
                        f"{command.description or ' '.join(command.argv)}"
                    ),
                    description=result.combined_output[-300:],
                    source="baseline_check",
                    scope_path=".",
                    round=round_number,
                )
                finding = transition(
                    finding,
                    FindingStatus.REPRODUCED,
                    evidence=EvidenceRef(
                        kind="tool_result",
                        description=f"{command.kind.value} exited {result.exit_code}",
                        metadata={"exit_code": result.exit_code, "argv": list(command.argv)},
                    ),
                )
                round_findings.append(finding)

        # Focused model review over the affected scope.
        prompt = json.dumps(
            {
                "task": "list suspected defects in the affected scope",
                "affected_scope": list(governor.allowed_scope) or ["<entire repository>"],
                "output_format": (
                    '{"findings": [{"title": str, "description": str, '
                    '"scope_path": str, "proposed_reproducer": str | null}]}'
                ),
            },
            sort_keys=True,
        )
        response = await reviewer.generate([Message(role="user", content=prompt)])
        for candidate in parse_review(response.text or "", governor.allowed_scope):
            round_findings.append(
                Finding(
                    finding_id=f"audit-r{round_number}-llm-{len(round_findings)}",
                    title=candidate.title,
                    description=candidate.description,
                    source="llm_review",
                    scope_path=candidate.scope_path,
                    proposed_reproducer=candidate.proposed_reproducer,
                    round=round_number,
                )
            )

        # Reproduction gate: SUSPECTED findings must demonstrate the defect
        # deterministically or be rejected with the attempt preserved.
        progressed: list[Finding] = []
        for finding in round_findings:
            if finding.status is FindingStatus.SUSPECTED and finding.proposed_reproducer:
                result = attempt_reproduction(finding, cwd, runner, timeout_seconds, store)
                finding = apply_reproduction(finding, result)
            if finding.status is FindingStatus.REPRODUCED:
                finding = confirm(finding)
            progressed.append(finding)

        governor.end_round()
        report.rounds.append(AuditRoundReport(round=round_number, findings=progressed))

        if any(f.status is FindingStatus.CONFIRMED for f in progressed):
            report.stopped_reason = "confirmed findings routed to repair"
        else:
            report.stopped_reason = "no confirmed findings in this round"

    store_report(store, report)
    return report


def store_report(store: RunStore | None, report: AuditReport) -> None:
    if store is not None:
        store.write_document("findings.json", report.to_dict())
