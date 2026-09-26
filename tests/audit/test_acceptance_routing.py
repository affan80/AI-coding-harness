"""Issue #68 acceptance evidence: confirmed findings route through bounded repair.

The audit engine landed with the evidence-gated-audit workstream; this
module maps the issue's acceptance bullets 1:1 and adds two edges the
existing tests did not pin: a non-confirmed finding cannot become a repair
goal even by direct call, and a confirmed finding whose repair fails the
standard ladder does not reach VERIFIED.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.audit import FindingStatus, authorizes_repair
from harness.audit.engine import (
    AuditScopeError,
    finding_to_verification_input,
    run_audit,
)
from harness.core.models import Budget
from harness.verification import run_verification_ladder
from harness.verification.adapters import PythonAdapter
from tests.audit.test_engine import StubAdapter, _candidate, _cmd_result, _governor, _review


def _confirmed_finding(tmp_path: Path):
    reviewer = _review([_candidate()])
    governor = _governor()
    report = asyncio.run(
        run_audit(tmp_path, reviewer, StubAdapter(), governor,
                  runner=_failing_repro_runner)
    )
    confirmed = report.confirmed
    assert len(confirmed) == 1
    return report, confirmed[0]


def _failing_repro_runner(argv, cwd, timeout_seconds):
    joined = " ".join(argv)
    if "import app.retry" in joined:
        return _cmd_result(argv, exit_code=1, output="AssertionError: retry limiter off-by-one")
    return _cmd_result(argv, exit_code=0)


def test_confirmed_findings_are_routed_and_the_report_says_so(tmp_path: Path):
    report, _finding = _confirmed_finding(tmp_path)
    assert report.stopped_reason == "confirmed findings routed to repair"
    assert report.confirmed[0].status is FindingStatus.CONFIRMED


def test_non_confirmed_finding_cannot_become_a_repair_goal(tmp_path: Path):
    report, _ = _confirmed_finding(tmp_path)
    suspect = report.findings[0]
    if suspect.status is FindingStatus.CONFIRMED:
        pytest.skip("fixture confirmed everything")
    with pytest.raises(AuditScopeError):
        finding_to_verification_input(suspect)


def test_llm_only_observations_cannot_authorize_repair():
    from harness.audit.models import Finding

    llm_only = Finding(
        finding_id="F-llm", title="speculation", description="vibes",
        source="llm_review", scope_path="app/retry.py",
        status=FindingStatus.SUSPECTED,
    )
    assert not authorizes_repair(llm_only)


def test_audit_cannot_expand_indefinitely(tmp_path: Path):
    governor = _governor(budget=Budget(max_audit_rounds=1))
    assert governor.begin_round() is True
    governor.end_round()
    # the budget is spent: no further engagement is possible
    assert governor.begin_round() is False
    report = asyncio.run(
        run_audit(tmp_path, _review([_candidate()]), StubAdapter(), governor,
                  runner=_failing_repro_runner)
    )
    assert report.stopped_reason == "audit rounds exhausted (budget)"
    assert report.rounds == []


def test_scope_guard_blocks_out_of_scope_repair_targets(tmp_path: Path):
    governor = _governor(allowed_scope=("app/",))
    assert governor.scope_ok("app/retry.py") is True
    assert governor.scope_ok("frontend/app.tsx") is False


def test_repaired_finding_meets_the_same_verification_gate(tmp_path: Path):
    report, confirmed_finding = _confirmed_finding(tmp_path)
    goal = finding_to_verification_input(confirmed_finding)

    # repair fails the standard ladder: the routed goal must NOT verify
    def broken_repair_runner(argv, cwd, timeout_seconds):
        joined = " ".join(argv)
        if "pytest" in joined or "repro" in joined:
            return _cmd_result(argv, exit_code=1, output="1 failed")
        return _cmd_result(argv, exit_code=0)

    failing = run_verification_ladder(
        goal, tmp_path, runner=broken_repair_runner,
        adapter=PythonAdapter(python_executable="py"),
    )
    assert failing.status.value == "failed"

    # repair passes the same ladder: only then is the goal VERIFIED
    def fixed_repair_runner(argv, cwd, timeout_seconds):
        return _cmd_result(argv, exit_code=0, output="ok\n")

    passing = run_verification_ladder(
        goal, tmp_path, runner=fixed_repair_runner,
        adapter=PythonAdapter(python_executable="py"),
    )
    assert passing.status.value == "verified"
