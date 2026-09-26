"""Issue #19: demo scenarios run deterministically and produce judge-ready evidence."""

import subprocess
import sys
from pathlib import Path

from demos.scenarios import (
    generate_large_repo,
    run_scenario_a,
    run_scenario_b,
    run_scenario_c,
)


def test_scenario_a_shows_failure_recovery_and_final_pass(tmp_path):
    result = run_scenario_a(tmp_path, tmp_path / "out")
    assert result.passed
    report = result.report_path.read_text(encoding="utf-8")
    assert "**Status: VERIFIED**" in report
    # the judge can see the initial failure, both attempts, and the final pass
    assert "FAIL — baseline reproducer" in report
    assert "attempt 1 (reordered arithmetic) failed" in report
    assert "attempt 2 (clamp to 0-100 range) passed" in report
    assert "PASS — after attempt 2 (final)" in report
    assert "`app.py`" in report  # changed file listed


def test_scenario_b_plans_edits_and_verifies(tmp_path):
    result = run_scenario_b(tmp_path, tmp_path / "out")
    assert result.passed
    report = result.report_path.read_text(encoding="utf-8")
    assert "**Status: VERIFIED**" in report
    assert "plan accepted" in report
    assert "`slugify.py`" in report and "`test_slugify.py`" in report
    assert "PASS — full suite" in report


def test_scenario_c_proves_discovered_vs_selected_and_retrieval(tmp_path):
    result = run_scenario_c(tmp_path, tmp_path / "out")
    assert result.passed
    report = result.report_path.read_text(encoding="utf-8")
    assert "discovered:" in report and "selected into context:" in report
    # repository exceeds the prompt budget: far more discovered than selected
    assert "selection ratio:" in report
    # the relevant file was retrieved at L2 after the metadata-only scan
    assert "PASS — retrieval of initially unloaded file" in report
    assert "db_pool" in result.summary


def test_generated_repo_exceeds_prompt_budget(tmp_path):
    root = generate_large_repo(tmp_path / "big")
    files = list((root / "src").glob("*.py"))
    tokens = sum(len(p.read_text()) // 4 for p in files)
    assert len(files) > 100
    assert tokens > 1_200  # larger than the scenario's context window


def test_one_command_runner_executes_all_scenarios(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-m", "demos.run_demo", "--scenario", "all",
         "--out", str(tmp_path / "out")],
        cwd=str(repo_root), capture_output=True, text=True, timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ALL SCENARIOS PASSED" in completed.stdout
    for scenario in ("scenario-a", "scenario-b", "scenario-c"):
        assert (tmp_path / "out" / scenario / "final-report.md").is_file()
