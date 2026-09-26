"""Audit finding lifecycle and evidence rules (#66; PRD §18).

Lifecycle: SUSPECTED → REPRODUCED → CONFIRMED → PATCHED → VERIFIED, with
REJECTED as the terminal exit at any pre-patch stage. The evidence gate is
structural: a finding only leaves SUSPECTED (and only a CONFIRMED finding
authorizes source changes) when a deterministic evidence reference — a
reproducer run or a tool result — is attached. LLM-only observations cannot
progress, no matter how confident the model sounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from harness.core.models import EvidenceRef


class FindingStatus(StrEnum):
    SUSPECTED = "suspected"
    REPRODUCED = "reproduced"
    CONFIRMED = "confirmed"
    PATCHED = "patched"
    VERIFIED = "verified"
    REJECTED = "rejected"


# Deterministic evidence kinds that satisfy the reproduction gate. An
# EvidenceRef of any other kind (e.g. a bare model observation) does not.
DETERMINISTIC_EVIDENCE_KINDS = frozenset({"reproducer", "tool_result"})

_ALLOWED_TRANSITIONS: dict[FindingStatus, set[FindingStatus]] = {
    FindingStatus.SUSPECTED: {FindingStatus.REPRODUCED, FindingStatus.REJECTED},
    FindingStatus.REPRODUCED: {FindingStatus.CONFIRMED, FindingStatus.REJECTED},
    FindingStatus.CONFIRMED: {FindingStatus.PATCHED, FindingStatus.REJECTED},
    FindingStatus.PATCHED: {FindingStatus.VERIFIED, FindingStatus.CONFIRMED},
    # Terminal states.
    FindingStatus.VERIFIED: set(),
    FindingStatus.REJECTED: set(),
}

_REPAIR_AUTHORIZING = frozenset(
    {FindingStatus.CONFIRMED, FindingStatus.PATCHED, FindingStatus.VERIFIED}
)


class InvalidTransitionError(Exception):
    """A lifecycle move that the evidence gate does not allow."""

    def __init__(self, finding_id: str, current: FindingStatus, target: FindingStatus, reason: str):
        super().__init__(f"finding {finding_id}: {current.value} -> {target.value}: {reason}")
        self.finding_id = finding_id
        self.current = current
        self.target = target
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "from": self.current.value,
            "to": self.target.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Finding:
    """One audit observation moving through the PRD §18 lifecycle."""

    finding_id: str
    title: str
    description: str
    source: str  # "llm_review" | "baseline_check" | "static_analysis" | ...
    scope_path: str  # affected-scope path the finding lives in
    status: FindingStatus = FindingStatus.SUSPECTED
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    proposed_reproducer: str | None = None  # command asserting the EXPECTED behavior
    rejection_reason: str | None = None
    round: int = 0

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "scope_path": self.scope_path,
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "proposed_reproducer": self.proposed_reproducer,
            "rejection_reason": self.rejection_reason,
            "round": self.round,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        return cls(
            finding_id=data["finding_id"],
            title=data["title"],
            description=data["description"],
            source=data["source"],
            scope_path=data["scope_path"],
            status=FindingStatus(data["status"]),
            evidence=tuple(EvidenceRef.from_dict(e) for e in data.get("evidence", ())),
            proposed_reproducer=data.get("proposed_reproducer"),
            rejection_reason=data.get("rejection_reason"),
            round=data.get("round", 0),
        )


def has_deterministic_evidence(finding: Finding) -> bool:
    return any(e.kind in DETERMINISTIC_EVIDENCE_KINDS for e in finding.evidence)


def authorizes_repair(finding: Finding) -> bool:
    """Only CONFIRMED-or-later findings may cause source changes."""
    return finding.status in _REPAIR_AUTHORIZING


def transition(
    finding: Finding,
    target: FindingStatus,
    *,
    evidence: EvidenceRef | None = None,
    reason: str | None = None,
) -> Finding:
    """Move a finding one lifecycle step, enforcing the evidence gate."""
    if target not in _ALLOWED_TRANSITIONS[finding.status]:
        raise InvalidTransitionError(
            finding.finding_id,
            finding.status,
            target,
            f"transition not allowed from {finding.status.value}",
        )
    updated_evidence = finding.evidence
    if evidence is not None:
        updated_evidence = (*finding.evidence, evidence)

    if target is FindingStatus.REPRODUCED or target is FindingStatus.CONFIRMED:
        if not any(e.kind in DETERMINISTIC_EVIDENCE_KINDS for e in updated_evidence):
            raise InvalidTransitionError(
                finding.finding_id,
                finding.status,
                target,
                "requires deterministic evidence (reproducer run or tool result); "
                "model observations alone stay SUSPECTED",
            )
    if target is FindingStatus.REJECTED:
        if not reason:
            raise InvalidTransitionError(
                finding.finding_id,
                finding.status,
                target,
                "rejections must record why",
            )

    return replace(
        finding,
        status=target,
        evidence=updated_evidence,
        rejection_reason=reason if target is FindingStatus.REJECTED else finding.rejection_reason,
    )


@dataclass
class AuditRoundReport:
    """Outcome of one audit round."""

    round: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def confirmed(self) -> list[Finding]:
        return [f for f in self.findings if f.status is FindingStatus.CONFIRMED]

    @property
    def rejected(self) -> list[Finding]:
        return [f for f in self.findings if f.status is FindingStatus.REJECTED]

    def to_dict(self) -> dict:
        return {
            "round": self.round,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict) -> AuditRoundReport:
        return cls(
            round=data["round"],
            findings=[Finding.from_dict(f) for f in data["findings"]],
        )


@dataclass
class AuditReport:
    """All findings across rounds plus why auditing stopped."""

    rounds: list[AuditRoundReport] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def findings(self) -> list[Finding]:
        return [f for r in self.rounds for f in r.findings]

    @property
    def confirmed(self) -> list[Finding]:
        return [f for f in self.findings if f.status is FindingStatus.CONFIRMED]

    def to_dict(self) -> dict:
        return {
            "rounds": [r.to_dict() for r in self.rounds],
            "stopped_reason": self.stopped_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AuditReport:
        return cls(
            rounds=[AuditRoundReport.from_dict(r) for r in data["rounds"]],
            stopped_reason=data.get("stopped_reason", ""),
        )
