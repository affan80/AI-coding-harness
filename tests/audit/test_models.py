"""Issue #66 — finding lifecycle and evidence gates."""

from __future__ import annotations

import pytest

from harness.audit import (
    Finding,
    FindingStatus,
    InvalidTransitionError,
    authorizes_repair,
    transition,
)
from harness.core.models import EvidenceRef


def _finding(**overrides) -> Finding:
    defaults = dict(
        finding_id="F1",
        title="password check inverted",
        description="login rejects valid passwords when the hash matches",
        source="llm_review",
        scope_path="app/auth.py",
        proposed_reproducer='python -c "import app.auth; assert app.auth.login(None)"',
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _repro_evidence() -> EvidenceRef:
    return EvidenceRef(
        kind="reproducer",
        description="reproducer failed with exit code 1",
        metadata={"exit_code": 1},
    )


def test_llm_only_observation_stays_suspected() -> None:
    finding = _finding()

    with pytest.raises(InvalidTransitionError, match="deterministic evidence"):
        transition(finding, FindingStatus.REPRODUCED)

    assert finding.status is FindingStatus.SUSPECTED
    assert not authorizes_repair(finding)


def test_suspected_cannot_jump_straight_to_confirmed() -> None:
    with pytest.raises(InvalidTransitionError, match="not allowed"):
        transition(_finding(evidence=(_repro_evidence(),)), FindingStatus.CONFIRMED)


def test_reproduced_evidence_unlocks_the_lifecycle() -> None:
    finding = transition(_finding(), FindingStatus.REPRODUCED, evidence=_repro_evidence())

    assert finding.status is FindingStatus.REPRODUCED
    assert not authorizes_repair(finding)  # reproduced alone still cannot patch

    confirmed = transition(finding, FindingStatus.CONFIRMED)

    assert confirmed.status is FindingStatus.CONFIRMED
    assert authorizes_repair(confirmed)


def test_non_deterministic_evidence_kind_does_not_satisfy_the_gate() -> None:
    opinion = EvidenceRef(kind="model_observation", description="the model is pretty sure")

    with pytest.raises(InvalidTransitionError, match="deterministic evidence"):
        transition(_finding(), FindingStatus.REPRODUCED, evidence=opinion)


def test_patch_and_verify_complete_the_lifecycle() -> None:
    finding = transition(_finding(), FindingStatus.REPRODUCED, evidence=_repro_evidence())
    finding = transition(finding, FindingStatus.CONFIRMED)
    patch_evidence = EvidenceRef(kind="tool_result", description="patch applied")
    finding = transition(finding, FindingStatus.PATCHED, evidence=patch_evidence)

    assert finding.status is FindingStatus.PATCHED
    verified = transition(
        finding,
        FindingStatus.VERIFIED,
        evidence=EvidenceRef(kind="tool_result", description="ladder verified"),
    )

    assert verified.status is FindingStatus.VERIFIED
    assert authorizes_repair(verified)
    # VERIFIED is terminal.
    with pytest.raises(InvalidTransitionError):
        transition(verified, FindingStatus.REPRODUCED)


def test_rejections_require_and_retain_the_reason() -> None:
    with pytest.raises(InvalidTransitionError, match="must record why"):
        transition(_finding(), FindingStatus.REJECTED)

    rejected = transition(
        _finding(),
        FindingStatus.REJECTED,
        evidence=EvidenceRef(kind="reproduction_attempt", description="reproducer passed"),
        reason="reproducer passed; defect not reproducible",
    )

    assert rejected.status is FindingStatus.REJECTED
    assert rejected.rejection_reason == "reproducer passed; defect not reproducible"
    assert not authorizes_repair(rejected)
    assert len(rejected.evidence) == 1  # the failed attempt is preserved


def test_failed_patch_reopens_the_finding() -> None:
    finding = transition(_finding(), FindingStatus.REPRODUCED, evidence=_repro_evidence())
    finding = transition(finding, FindingStatus.CONFIRMED)
    finding = transition(finding, FindingStatus.PATCHED)

    reopened = transition(
        finding,
        FindingStatus.CONFIRMED,
        reason=None,
        evidence=EvidenceRef(kind="tool_result", description="ladder failed again"),
    )

    assert reopened.status is FindingStatus.CONFIRMED


def test_finding_round_trips_through_serialization() -> None:
    finding = transition(_finding(), FindingStatus.REPRODUCED, evidence=_repro_evidence())
    finding = transition(finding, FindingStatus.CONFIRMED)

    restored = Finding.from_dict(finding.to_dict())

    assert restored.to_dict() == finding.to_dict()
    assert restored.status is FindingStatus.CONFIRMED
