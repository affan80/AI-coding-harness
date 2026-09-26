"""Contract tests for harness.contracts.verification (issue #83)."""

from __future__ import annotations

import json

import pytest

from harness.contracts.verification import (
    DETERMINISTIC_EVIDENCE_KINDS,
    AuditFinding,
    EvidenceRef,
    FailureClass,
    FindingStatus,
    StageName,
    StageOutcome,
    StageResult,
    VerificationContractError,
    VerificationReport,
    VerificationStatus,
)


def _report() -> VerificationReport:
    return VerificationReport(
        goal_id="G1",
        status=VerificationStatus.FAILED,
        stages=(
            StageResult(
                name=StageName.SYNTAX,
                outcome=StageOutcome.PASSED,
                command="python -c 'import ast'",
                exit_code=0,
                duration_ms=12,
                summary="ast.parse 1 changed file(s)",
            ),
            StageResult(
                name=StageName.TARGETED_TESTS,
                outcome=StageOutcome.FAILED,
                command="python -m pytest -q tests/test_math.py::test_add",
                exit_code=1,
                duration_ms=931,
                summary="AssertionError: add(1, 2) != 4",
            ),
            StageResult(
                name=StageName.FULL_SUITE,
                outcome=StageOutcome.SKIPPED,
                summary="not reached",
            ),
        ),
        first_failure=StageResult(
            name=StageName.TARGETED_TESTS,
            outcome=StageOutcome.FAILED,
            command="python -m pytest -q tests/test_math.py::test_add",
            exit_code=1,
            duration_ms=931,
            summary="AssertionError: add(1, 2) != 4",
        ),
    )


def test_report_round_trips_through_json() -> None:
    report = _report()

    encoded = json.loads(json.dumps(report.to_dict()))
    restored = VerificationReport.from_dict(encoded)

    assert restored.to_dict() == report.to_dict()
    assert restored.status is VerificationStatus.FAILED
    assert restored.first_failure is not None
    assert restored.first_failure.name is StageName.TARGETED_TESTS


def test_failure_classes_cover_all_ten_prd_17_categories() -> None:
    assert {cls.value for cls in FailureClass} == {
        "syntax", "build", "test", "patch", "tool",
        "timeout", "plan", "context", "loop", "environment",
    }


def test_finding_states_cover_the_prd_18_lifecycle() -> None:
    assert {status.value for status in FindingStatus} == {
        "suspected", "reproduced", "confirmed", "patched", "verified", "rejected",
    }
    assert DETERMINISTIC_EVIDENCE_KINDS == {"reproducer", "tool_result"}


def test_finding_round_trips_with_evidence_and_metadata() -> None:
    finding = AuditFinding(
        finding_id="F1",
        title="password check inverted",
        description="login rejects valid passwords",
        source="baseline_check",
        scope_path="app/auth.py",
        status=FindingStatus.CONFIRMED,
        evidence=(
            EvidenceRef(
                kind="tool_result",
                ref_id="e-1",
                description="lint exited 1",
                path="artifacts/tool-0001-output.txt",
                sha256="a" * 64,
                metadata={"exit_code": 1, "argv": ["ruff", "check", "."]},
            ),
        ),
        round=2,
    )

    encoded = json.loads(json.dumps(finding.to_dict()))
    restored = AuditFinding.from_dict(encoded)

    assert restored == finding
    assert restored.evidence[0].metadata["exit_code"] == 1


def test_unknown_enum_value_is_rejected_not_guessed() -> None:
    with pytest.raises(VerificationContractError):
        StageResult.from_dict(
            {
                "name": "telepathy",
                "outcome": "passed",
                "command": None,
                "exit_code": 0,
                "duration_ms": 1,
                "summary": "",
            }
        )


def test_missing_field_is_rejected() -> None:
    data = _report().to_dict()
    del data["goal_id"]

    with pytest.raises(VerificationContractError, match="goal_id"):
        VerificationReport.from_dict(data)


def test_optional_first_failure_survives_none() -> None:
    report = VerificationReport(
        goal_id="G2",
        status=VerificationStatus.VERIFIED,
        stages=(StageResult(name=StageName.DIFF_SCOPE, outcome=StageOutcome.PASSED),),
        first_failure=None,
    )

    restored = VerificationReport.from_dict(report.to_dict())

    assert restored.first_failure is None
    assert restored.status is VerificationStatus.VERIFIED
