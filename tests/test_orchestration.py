"""Issue #78: one command completes an end-to-end source change without
bypassing orchestrator, policy, or evidence contracts."""

import asyncio
import json
from pathlib import Path

import pytest

from harness.core.models import Budget
from harness.model.fake import FakeModelClient, ScriptedTurn
from harness.orchestration import run_orchestrated

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"

WRONG_DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n-    return a - b\n+    return a * b\n"
)
# the repair diff applies against the file as the WRONG patch left it
RIGHT_DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n-    return a * b\n+    return a + b\n"
)

INTENT = {
    "objective": "fix the add() bug",
    "root_goal_id": "G1",
    "goals": [{
        "goal_id": "G1", "title": "Fix the add() subtraction bug",
        "kind": "fix",
        "acceptance_criteria": ["add(1, 2) == 3"],
        "verification_criteria": ["tests/test_app.py passes"],
    }],
    "constraints": [],
    "assumptions": [],
    "completion_criteria": ["targeted tests pass"],
}

PLAN = {
    "steps": [
        {"id": "S1", "action": "inspect", "target": "app.py", "goal_id": "G1"},
        {"id": "S2", "action": "patch", "target": "app.py", "goal_id": "G1",
         "depends_on": ["S1"]},
        {"id": "S3", "action": "verify", "target": "tests/test_app.py",
         "goal_id": "G1", "depends_on": ["S2"]},
    ],
}

RECOVERY_PLAN = {
    "failure_class": "test",
    "root_cause": "repair used multiplication instead of addition",
    "repair_summary": "apply the addition fix to app.py",
    "repair_actions": [{
        "kind": "patch", "target": "app.py",
        "detail": "return a + b",
    }],
    "reverify_stages": ["targeted_tests", "related_tests", "full_suite",
                        "diff_scope"],
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(BUGGY)
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (root / "conftest.py").write_text("")  # puts repo root on the inner pytest path
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    return root


def _client() -> FakeModelClient:
    return FakeModelClient(script=[
        ScriptedTurn(structured=INTENT),    # intent extraction
        ScriptedTurn(structured=PLAN),      # plan proposal
        ScriptedTurn(structured={"diff": WRONG_DIFF}),   # first (wrong) patch
        ScriptedTurn(structured=RECOVERY_PLAN),          # recovery proposal
        ScriptedTurn(structured={"diff": RIGHT_DIFF}),   # corrected patch
    ])


def _request(repo: Path):
    from harness.core.models import UserRequest

    return UserRequest(
        objective="fix the add() bug",
        repository_path=str(repo),
        scope_paths=("app.py", "tests/"),
    )


def test_end_to_end_source_change_completes(tmp_path, repo):
    lines: list[str] = []
    result = asyncio.run(run_orchestrated(
        _request(repo), client=_client(),
        budget=Budget(max_model_calls=12, max_iterations=25),
        runs_root=tmp_path / "runs", out=lines.append,
    ))

    # the source change actually landed, through the tools layer only
    assert (repo / "app.py").read_text() == FIXED
    assert result.exit_code == 0
    assert result.status == "VERIFIED"
    assert result.goals_completed == result.goals_total == 1
    assert "app.py" in result.changed_files
    assert result.recovery_attempts >= 1  # the wrong first patch was recovered
    assert any("Status: VERIFIED" in line for line in result.final_lines)


def test_no_contract_is_bypassed(tmp_path, repo):
    lines: list[str] = []
    result = asyncio.run(run_orchestrated(
        _request(repo), client=_client(),
        runs_root=tmp_path / "runs", out=lines.append,
    ))

    run_dir = Path(result.run_dir)
    # evidence contracts: events, verification, and the final report exist
    assert (run_dir / "events.jsonl").is_file()
    assert (run_dir / "verification.json").is_file()
    assert (run_dir / "final-report.md").is_file()
    events = "\n".join(
        (run_dir / "events.jsonl").read_text().splitlines()
    )
    # orchestrator contract: every state transition is recorded
    for state in ("UNDERSTAND", "INSPECT_REPOSITORY", "PLAN", "EXECUTE",
                  "VERIFY", "DIAGNOSE", "FINALIZE"):
        assert state in events, f"missing transition through {state}"
    # budget contract: model calls were consumed through the orchestrator
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["model_calls"] >= 4
    report = (run_dir / "final-report.md").read_text()
    assert "**Status: VERIFIED**" in report
    assert "`app.py`" in report


def test_budget_exhaustion_fails_without_a_source_change(tmp_path, repo):
    tiny = FakeModelClient(script=[
        ScriptedTurn(structured=INTENT),
        ScriptedTurn(structured=PLAN),
        ScriptedTurn(structured={"diff": RIGHT_DIFF}),
    ])
    result = asyncio.run(run_orchestrated(
        _request(repo), client=tiny,
        budget=Budget(max_model_calls=2, max_iterations=25),
        runs_root=tmp_path / "runs", out=lambda _line: None,
    ))

    assert result.exit_code != 0
    assert result.status == "FAILED"
    # nothing was patched: the budget ran out before the diff turn
    assert (repo / "app.py").read_text() == BUGGY
    report = Path(result.report_path).read_text()
    assert "**Status: FAILED**" in report
