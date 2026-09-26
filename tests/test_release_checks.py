"""Issue #79: release acceptance checks cover security, governance, honesty."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.environ.get("HARNESS_RELEASE_CHECKS"):
    # Recursion guard: the release script re-runs the test suite; these
    # acceptance tests must not re-enter the script.
    pytest.skip("release checks are already running", allow_module_level=True)

from scripts.release_checks import (  # noqa: E402
    AcceptanceSummary,
    check_evidence_integrity,
    check_failure_reporting,
    check_secret_scan,
)


def test_full_acceptance_run_passes_from_a_clean_state(tmp_path):
    from scripts.release_checks import run_acceptance

    summary = run_acceptance(skip_demos=True)  # demos covered by their own suite
    assert summary.ok, summary.render()
    names = [c.name for c in summary.checks]
    assert "full automated test suite" in names
    assert "secret scan over tracked files" in names
    # every unbuilt criterion is recorded as an explicit limitation
    limitations = [c for c in summary.checks if c.limitation]
    assert limitations, "unexercised criteria must be recorded, not hidden"


def test_secret_scan_finds_credential_material(tmp_path, monkeypatch):
    import scripts.release_checks as rc

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / "leaked.txt").write_text("token: ghp_" + "a" * 36 + "\n")
    (fake_root / "clean.py").write_text("x = 1\n")
    monkeypatch.setattr(rc, "REPO_ROOT", fake_root)

    report = check_secret_scan()
    assert not report.passed
    assert "GitHub personal access token" in report.detail


def test_failure_reporting_check_requires_honest_failed_sessions(tmp_path):
    report = check_failure_reporting(tmp_path)
    assert report.passed
    assert "FAILED" in report.detail


def test_evidence_integrity_check_requires_reconstruction(tmp_path):
    report = check_evidence_integrity(tmp_path)
    assert report.passed
    assert "reconstruction ok" in report.detail


def test_summary_fails_when_any_check_fails():
    summary = AcceptanceSummary()
    summary.checks.append(
        __import__("scripts.release_checks", fromlist=["CheckReport"]).CheckReport(
            name="broken thing", passed=False, detail="it broke",
        )
    )
    assert not summary.ok
    assert "[FAIL] broken thing: it broke" in summary.render()


def test_script_entry_point_exits_zero_from_clean_state():
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.release_checks", "--skip-demos"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=900, check=False,
    )
    assert completed.returncode == 0, completed.stdout[-800:]
    assert "RELEASE ACCEPTANCE: PASSED" in completed.stdout
    assert "KNOWN-LIMITATION" in completed.stdout  # honest recording
