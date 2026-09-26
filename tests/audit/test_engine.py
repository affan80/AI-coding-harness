"""Issue #68 — bounded audit rounds, scope guard, and repair routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness.adapters.base import AdapterCommand, CommandKind
from harness.audit import AuditReport, FindingStatus, authorizes_repair
from harness.audit.engine import (
    AuditGovernor,
    AuditScopeError,
    finding_to_verification_input,
    parse_review,
    run_audit,
)
from harness.core.models import Budget, BudgetUsage
from harness.model.fake import FakeModelClient, ScriptedTurn
from harness.verification import run_verification_ladder
from harness.verification.adapters import PythonAdapter


class StubAdapter:
    """Deterministic adapter: emits the baseline checks the test configures."""

    def __init__(self, lint=None, typecheck=None):
        self._lint = lint or []
        self._typecheck = typecheck or []

    def detect(self, root):
        return True

    def syntax_command(self, root, changed_files):
        return None

    def build_or_typecheck_command(self, root, changed_files):
        return None

    def lint_commands(self, root):
        return self._lint

    def typecheck_commands(self, root):
        return self._typecheck

    def target_test_command(self, root, tests):
        return None

    def full_test_command(self, root):
        return None


def _review(findings: list[dict]) -> FakeModelClient:
    return FakeModelClient([ScriptedTurn(text=json.dumps({"findings": findings}))])


def _candidate(**overrides) -> dict:
    defaults = {
        "title": "off-by-one in retry limiter",
        "description": "retry limiter allows one extra attempt",
        "scope_path": "app/retry.py",
        "proposed_reproducer": "python -c \"import app.retry; assert False\"",
    }
    defaults.update(overrides)
    return defaults


def _governor(**overrides) -> AuditGovernor:
    defaults = dict(
        budget=Budget(max_audit_rounds=3),
        usage=BudgetUsage(),
        allowed_scope=("app/",),
    )
    defaults.update(overrides)
    return AuditGovernor(**defaults)


def test_parse_review_drops_out_of_scope_and_malformed() -> None:
    candidates = parse_review(
        json.dumps(
            {
                "findings": [
                    _candidate(),
                    _candidate(title="outside", scope_path="frontend/x.ts"),
                    {"title": "incomplete"},
                    _candidate(title="", description="no title"),
                ]
            }
        ),
        allowed_scope=("app/",),
    )

    assert len(candidates) == 1
    assert candidates[0].scope_path == "app/retry.py"


def test_llm_only_finding_stays_suspected_and_never_routes(tmp_path: Path) -> None:
    reviewer = _review([_candidate(proposed_reproducer=None)])
    governor = _governor()

    report = asyncio.run(
        run_audit(tmp_path, reviewer, StubAdapter(), governor, runner=_all_pass_runner())
    )

    findings = report.findings
    assert len(findings) == 1
    assert findings[0].status is FindingStatus.SUSPECTED
    assert findings[0].source == "llm_review"
    assert not authorizes_repair(findings[0])
    with pytest.raises(AuditScopeError):
        finding_to_verification_input(findings[0])


def test_baseline_check_failure_is_deterministic_and_confirmed(tmp_path: Path) -> None:
    lint = AdapterCommand(
        kind=CommandKind.LINT, argv=["ruff", "check", "."], description="ruff"
    )

    def runner(argv, cwd, timeout_seconds):
        if "ruff" in argv:
            return _cmd_result(argv, exit_code=1, output="app/retry.py:1: F401 unused import")
        return _cmd_result(argv, exit_code=0)

    reviewer = _review([])
    governor = _governor()

    report = asyncio.run(
        run_audit(tmp_path, reviewer, StubAdapter(lint=[lint]), governor, runner=runner)
    )

    findings = report.findings
    assert len(findings) == 1
    assert findings[0].source == "baseline_check"
    assert findings[0].status is FindingStatus.CONFIRMED
    assert authorizes_repair(findings[0])
    # Deterministic tool evidence attached.
    assert findings[0].evidence[-1].kind == "tool_result"


def test_reproducer_gate_confirms_and_routes_to_standard_verification(
    tmp_path: Path,
) -> None:
    # The proposed reproducer FAILS on the buggy tree, demonstrating the
    # defect; after the (simulated) repair the same command must pass.
    reviewer = _review([_candidate()])
    governor = _governor()

    report = asyncio.run(
        run_audit(tmp_path, reviewer, StubAdapter(), governor, runner=_failing_repro_runner())
    )

    confirmed = report.confirmed
    assert len(confirmed) == 1
    goal = finding_to_verification_input(confirmed[0])

    assert goal.goal_id == f"audit-{confirmed[0].finding_id}"
    assert goal.goal_kind.value == "audit"
    assert goal.allowed_scope == ("app/retry.py",)
    assert goal.reproducer_command == confirmed[0].proposed_reproducer

    # The routed goal runs through the SAME deterministic ladder as
    # user-requested work; VERIFIED only after every required stage passes.
    def post_repair_runner(argv, cwd, timeout_seconds):
        return _cmd_result(argv, exit_code=0, output="ok\n")

    ladder_report = run_verification_ladder(
        goal, tmp_path, runner=post_repair_runner, adapter=PythonAdapter(python_executable="py")
    )
    assert ladder_report.status.value == "verified"


def test_audit_rounds_are_bounded_by_governance_budget(tmp_path: Path) -> None:
    from harness.core.budgets import BudgetKind, consume

    # A fully consumed AUDIT_ROUNDS budget blocks any further engagement.
    budget = Budget(max_audit_rounds=2)
    usage = consume(budget, consume(budget, BudgetUsage(), BudgetKind.AUDIT_ROUNDS),
                    BudgetKind.AUDIT_ROUNDS)
    governor = _governor(budget=budget, usage=usage)

    report = asyncio.run(
        run_audit(tmp_path, _review([_candidate()]), StubAdapter(), governor,
                  runner=_failing_repro_runner())
    )

    assert report.rounds == []
    assert "exhausted" in report.stopped_reason
    from harness.core.errors import BudgetExhaustedError

    with pytest.raises(BudgetExhaustedError):
        consume(budget, governor.usage, BudgetKind.AUDIT_ROUNDS)


def test_audit_stops_when_a_round_confirms_nothing(tmp_path: Path) -> None:
    reviewer = _review([_candidate(proposed_reproducer=None)])

    report = asyncio.run(
        run_audit(tmp_path, reviewer, StubAdapter(), _governor(), runner=_all_pass_runner())
    )

    assert len(report.rounds) == 1
    assert "no confirmed findings" in report.stopped_reason


def test_out_of_scope_findings_never_enter_the_report(tmp_path: Path) -> None:
    reviewer = _review([_candidate(scope_path="frontend/leak.ts")])

    report = asyncio.run(
        run_audit(tmp_path, reviewer, StubAdapter(), _governor(), runner=_all_pass_runner())
    )

    assert report.findings == []
    assert all(f.scope_path.startswith("app/") for f in report.findings)


def test_findings_persist_to_the_run_directory(tmp_path: Path) -> None:
    from harness.core.models import UserRequest
    from harness.telemetry import RunStore

    store = RunStore.start(
        UserRequest(repository_path=str(tmp_path), objective="audit"),
        runs_root=tmp_path / "runs",
    )

    report = asyncio.run(
        run_audit(
            tmp_path, _review([_candidate()]), StubAdapter(), _governor(),
            runner=_failing_repro_runner(), store=store,
        )
    )

    persisted = json.loads((store.run_dir / "findings.json").read_text())
    restored = AuditReport.from_dict(persisted)
    assert restored.stopped_reason == report.stopped_reason
    assert [f.to_dict() for f in restored.confirmed] == [
        f.to_dict() for f in report.confirmed
    ]


# -- helpers -----------------------------------------------------------------


def _cmd_result(argv, exit_code: int, output: str = ""):
    from harness.execution.runner import CommandResult

    return CommandResult(
        argv=tuple(argv),
        exit_code=exit_code,
        stdout=output if exit_code == 0 else "",
        stderr="" if exit_code == 0 else output,
        duration_ms=5,
        timed_out=False,
    )


def _all_pass_runner():
    def runner(argv, cwd, timeout_seconds):
        return _cmd_result(argv, exit_code=0, output="ok\n")

    return runner


def _failing_repro_runner():
    def runner(argv, cwd, timeout_seconds):
        joined = " ".join(argv)
        if "assert False" in joined:
            return _cmd_result(argv, exit_code=1, output="AssertionError: defect demonstrated")
        return _cmd_result(argv, exit_code=0, output="ok\n")

    return runner
