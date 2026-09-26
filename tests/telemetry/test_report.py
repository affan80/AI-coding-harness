"""Issue #71: honest final evidence reports for every terminal outcome."""

import pytest

from harness.telemetry.events import EventRecorder, EventType
from harness.telemetry.metrics import UsageMetrics
from harness.telemetry.report import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_VERIFIED,
    CheckResult,
    ReportInput,
    generate_report,
    write_report,
)


def _events():
    recorder = EventRecorder(secrets=["sk-secret-key"])
    recorder.emit(EventType.STATE, "INITIALIZE -> UNDERSTAND")
    recorder.emit(EventType.PATCH, "patched app/auth.py (p-1234abcd)")
    recorder.emit(EventType.FAILURE, "targeted test failed with sk-secret-key")
    recorder.emit(EventType.RETRY, "repair with corrected 401 mapping",
                  data={"recovered": True})
    recorder.emit(EventType.VERIFICATION, "full suite 142/142 passed")
    return recorder.events()


def _verified_input() -> ReportInput:
    return ReportInput(
        session_id="sess-abc",
        objective="Fix invalid password handling",
        status=STATUS_VERIFIED,
        goals=[{
            "id": "G1",
            "title": "Invalid passwords return 401",
            "status": "COMPLETED",
            "acceptance_criteria": ["401 on bad password", "200 on good password"],
        }],
        changed_files=["app/auth.py", "tests/test_auth.py"],
        checks=[
            CheckResult(name="targeted tests", command="pytest tests/test_auth.py -q",
                        passed=True, exit_code=0, output_summary="3 passed"),
            CheckResult(name="full suite", command="pytest -q",
                        passed=True, exit_code=0, output_summary="142 passed"),
        ],
        recovery=[
            "attempt 1 failed: mapped to 500 instead of 401",
            "attempt 2 (materially different): handle InvalidCredentialsError in route",
        ],
        metrics=UsageMetrics(model_calls=6, tool_calls=11, patches=2, failures=1,
                             retries=1, recovery_attempts=1, verifications=2,
                             input_tokens=5200, output_tokens=1400,
                             context_discovered_files=512,
                             context_discovered_tokens=98_000,
                             context_selected_files=8,
                             context_selected_tokens=6_100),
        events=_events(),
        evidence=[{"ref_id": "ev-1", "kind": "diff",
                   "description": "patched app/auth.py", "path": "runs/s/patches.diff"}],
    )


def test_verified_report_contains_every_judge_checkable_fact():
    report = generate_report(_verified_input())
    assert "# Final report — session sess-abc" in report
    assert "**Status: VERIFIED**" in report
    assert "Fix invalid password handling" in report
    assert "[COMPLETED] G1" in report
    assert "401 on bad password" in report
    assert "`app/auth.py`" in report and "`tests/test_auth.py`" in report
    assert "PASS — targeted tests" in report and "exit 0" in report
    assert "PASS — full suite" in report
    assert "512 files / 98000 tokens" in report
    assert "8 files / 6100 tokens" in report
    assert "selection ratio: 6.2%" in report
    assert "attempt 2 (materially different)" in report
    assert "runs/s/patches.diff" in report


def test_no_secrets_or_reasoning_leak_into_report():
    report = generate_report(_verified_input())
    assert "sk-secret-key" not in report


def test_partial_report_states_what_ran_and_what_did_not():
    data = _verified_input()
    data.status = STATUS_PARTIAL
    data.detail = "retry budget exhausted before goal G2"
    data.checks = [
        CheckResult(name="targeted tests", command="pytest tests/test_auth.py -q",
                    passed=True, exit_code=0, output_summary="3 passed"),
    ]
    data.goals.append({"id": "G2", "title": "Add RBAC", "status": "PENDING",
                       "acceptance_criteria": []})
    report = generate_report(data)
    assert "**Status: PARTIAL**" in report
    assert "retry budget exhausted" in report
    assert "[PENDING] G2" in report
    assert "no deterministic checks were run" not in report  # one check ran


def test_failed_report_is_explicit_about_failures():
    data = _verified_input()
    data.status = STATUS_FAILED
    data.checks = [
        CheckResult(name="full suite", command="pytest -q", passed=False,
                    exit_code=1, output_summary="3 failed, 139 passed"),
    ]
    data.limitations = ["environment lacks network access; MCP disabled"]
    report = generate_report(data)
    assert "**Status: FAILED**" in report
    assert "FAIL — full suite" in report and "exit 1" in report
    assert "3 failed, 139 passed" in report
    assert "environment lacks network access" in report


def test_invalid_status_is_rejected_and_report_persists(tmp_path):
    with pytest.raises(ValueError):
        ReportInput(session_id="s", objective="o", status="SORT_OF_FINE")
    path = write_report(tmp_path / "runs" / "sess-abc",
                        generate_report(_verified_input()))
    assert path.name == "final-report.md"
    assert path.read_text(encoding="utf-8").startswith("# Final report")
