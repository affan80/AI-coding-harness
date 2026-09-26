"""Issue #12: executor SHELL/VERIFY steps over the full factory registry.

These run against ``factory.build_registry`` — the controlled shell, the
read-only git tools, and the checkpoint/rollback tooling — proving the whole
execution surface works end to end.
"""

import asyncio
import subprocess

import pytest

from harness.core.models import PlanStep, StepKind
from harness.execution.executor import ExecutorLoop, rollback_checkpoint
from harness.execution.models import FailureKind
from harness.tools.factory import build_registry
from harness.tools.git_tools import deserialize_checkpoint


@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True,
                   capture_output=True)
    (tmp_path / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


@pytest.fixture()
def executor(repo) -> ExecutorLoop:
    from harness.tools.policy import CommandPolicy

    policy = CommandPolicy(default_timeout_seconds=2)
    return ExecutorLoop(build_registry(repo, allowed_scope=[], policy=policy))


def _run(executor: ExecutorLoop, step: PlanStep, **kwargs):
    return asyncio.run(executor.execute(step, **kwargs))


def test_shell_step_runs_command_and_reports_tool_evidence(executor):
    step = PlanStep(step_id="S3", goal_id="G1", title="list", kind=StepKind.SHELL,
                    detail="echo hello")
    outcome = _run(executor, step)
    assert outcome.ok, outcome.summary
    assert outcome.evidence[0].kind == "tool_call"
    assert "hello" in outcome.summary


def test_shell_step_failure_kinds_are_distinct(executor):
    nonzero = _run(executor, PlanStep(
        step_id="S4", goal_id="G1", title="fail", kind=StepKind.SHELL,
        detail="false"))
    assert nonzero.failed and nonzero.failure_kind is FailureKind.TOOL

    denied = _run(executor, PlanStep(
        step_id="S5", goal_id="G1", title="destructive", kind=StepKind.SHELL,
        detail="rm -rf /"))
    assert denied.failed and denied.failure_kind is FailureKind.DENIED

    slow = _run(executor, PlanStep(
        step_id="S6", goal_id="G1", title="slow", kind=StepKind.SHELL,
        detail="sleep 5"))
    assert slow.failed and slow.failure_kind is FailureKind.TIMEOUT


def test_shell_step_without_command_is_plan_failure(executor):
    outcome = _run(executor, PlanStep(step_id="S7", goal_id="G1", title="empty",
                                      kind=StepKind.SHELL))
    assert outcome.failed
    assert outcome.failure_kind is FailureKind.PLAN


def test_verify_step_produces_test_run_evidence(executor):
    ok = _run(executor, PlanStep(
        step_id="S8", goal_id="G1", title="check", kind=StepKind.VERIFY,
        detail="python3 -c \"print('tests pass')\""))
    assert ok.ok and ok.evidence[0].kind == "test_run"

    failing = _run(executor, PlanStep(
        step_id="S9", goal_id="G1", title="check", kind=StepKind.VERIFY,
        detail="python3 -c \"raise SystemExit(1)\""))
    assert failing.failed and failing.failure_kind is FailureKind.TEST


def test_edit_step_through_full_registry_records_checkpoint_evidence(executor, repo):
    from harness.execution.models import EditRequest

    original = (repo / "app.py").read_text()
    step = PlanStep(step_id="S10", goal_id="G1", title="patch", kind=StepKind.EDIT,
                    detail="app.py")
    edit = EditRequest(
        path="app.py",
        diff=(
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
            "-value = 1\n+value = 2\n"
        ),
    )
    outcome = _run(executor, step, edit=edit)
    assert outcome.ok, outcome.summary
    assert outcome.changed_files == ("app.py",)

    checkpoints = [
        ref for ref in outcome.evidence if ref.kind == "checkpoint"
    ]
    assert checkpoints, "a checkpoint must be created before the risky write"
    checkpoint_id = checkpoints[0].metadata["checkpoint_id"]

    # the mutation is visible in git diff
    diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout
    assert "+value = 2" in diff

    # recovery-policy hook: rollback restores only the session-owned file
    assert rollback_checkpoint(executor, checkpoint_id)
    assert (repo / "app.py").read_text() == original

    # unknown checkpoint ids are refused, never guessed
    assert not rollback_checkpoint(executor, "ckpt-does-not-exist")


def test_deserialize_checkpoint_round_trip(tmp_path):
    from harness.tools.git_tools import create_checkpoint

    (tmp_path / "f.txt").write_text("hello\n")
    result = create_checkpoint(tmp_path, ["f.txt"])
    data = result.data["checkpoint"]
    checkpoint = deserialize_checkpoint(data)
    assert checkpoint.snapshots == {"f.txt": "hello\n"}
