"""Issue #11 executor tests: typed tool boundary and mutation evidence.

The registry here carries only the file tools (the shell/git tools and their
registrations land with issue #12), which also proves the executor degrades
cleanly — checkpoint/diff are best-effort, not hidden failures.
"""

import asyncio
import subprocess

import pytest

from harness.core.models import PlanStep, StepKind, StepStatus
from harness.execution.executor import ExecutorLoop
from harness.execution.models import EditRequest, FailureKind, StepOutcome
from harness.tools import files as files_tools
from harness.tools.files import file_hash
from harness.tools.registry import ToolRegistry, ToolSpec


@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True,
                   capture_output=True)
    src = tmp_path / "app.py"
    src.write_text("def flag():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


@pytest.fixture()
def registry(repo) -> ToolRegistry:
    registry = ToolRegistry(repo, allowed_scope=[])
    registry.register(ToolSpec("read_file", capability="read", mutates=False,
                               handler=files_tools.read_file))
    registry.register(ToolSpec("read_range", capability="read", mutates=False,
                               handler=files_tools.read_range))
    registry.register(ToolSpec("create_file", capability="patch", mutates=True,
                               handler=files_tools.create_file))
    registry.register(ToolSpec("apply_patch", capability="patch", mutates=True,
                               handler=files_tools.apply_patch))
    return registry


def _run(executor: ExecutorLoop, step: PlanStep, **kwargs) -> StepOutcome:
    return asyncio.run(executor.execute(step, **kwargs))


def _edit_step(path="app.py") -> PlanStep:
    return PlanStep(
        step_id="S2", goal_id="G1", title="fix flag", kind=StepKind.EDIT,
        detail=path, status=StepStatus.IN_PROGRESS,
    )


def _edit_request(path="app.py", **overrides) -> EditRequest:
    payload = {
        "path": path,
        "diff": (
            "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n"
            " def flag():\n-    return 1\n+    return 42\n"
        ),
    }
    payload.update(overrides)
    return EditRequest(**payload)


def test_inspect_step_reads_through_registry_with_evidence(repo, registry):
    executor = ExecutorLoop(registry)
    step = PlanStep(step_id="S1", goal_id="G1", title="look", kind=StepKind.INSPECT,
                    detail="app.py")
    outcome = _run(executor, step)
    assert outcome.ok
    assert outcome.summary.startswith("read app.py")
    ref = outcome.evidence[0]
    assert ref.kind == "tool_call"
    assert ref.metadata["goal_id"] == "G1"
    assert ref.metadata["step_id"] == "S1"


def test_edit_step_applies_patch_and_captures_mutation_evidence(repo, registry):
    executor = ExecutorLoop(registry)
    outcome = _run(executor, _edit_step(), edit=_edit_request())

    assert outcome.ok, outcome.summary
    assert outcome.changed_files == ("app.py",)
    kinds = {ref.kind for ref in outcome.evidence}
    assert "diff" in kinds
    patch_ref = next(ref for ref in outcome.evidence if "patched" in ref.description)
    application = patch_ref.metadata["tool_result"]["data"]["application"]
    assert application["changed_lines"] == [2]
    assert application["old_sha256"] != application["new_sha256"]
    assert application["patch_id"]

    diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True,
                          check=True).stdout
    assert "+    return 42" in diff  # minimal and visible


def test_edit_step_refuses_stale_hash_without_overwriting(repo, registry):
    executor = ExecutorLoop(registry)
    stale = file_hash(repo / "app.py")
    (repo / "app.py").write_text("def flag():\n    return 999\n")

    outcome = _run(executor, _edit_step(), edit=_edit_request(expected_old_hash=stale))

    assert outcome.failed
    assert outcome.failure_kind is FailureKind.PATCH_CONFLICT
    assert (repo / "app.py").read_text() == "def flag():\n    return 999\n"


def test_edit_step_rejects_out_of_scope_target(repo):
    restricted = ToolRegistry(repo, allowed_scope=["other.py"])
    restricted.register(ToolSpec("apply_patch", capability="patch", mutates=True,
                                 handler=files_tools.apply_patch))
    executor = ExecutorLoop(restricted)
    outcome = _run(executor, _edit_step(), edit=_edit_request())
    assert outcome.failed
    assert outcome.failure_kind is FailureKind.TOOL
    assert "scope" in outcome.summary
    # the file is untouched
    assert (repo / "app.py").read_text() == "def flag():\n    return 1\n"


def test_path_traversal_is_rejected_before_any_handler_runs(repo, registry):
    executor = ExecutorLoop(registry)
    step = PlanStep(step_id="S9", goal_id="G1", title="escape",
                    kind=StepKind.INSPECT, detail="../outside.py")
    outcome = _run(executor, step)
    assert outcome.failed
    assert "escapes the repository root" in outcome.summary


def test_edit_without_request_is_a_structured_plan_failure(repo, registry):
    executor = ExecutorLoop(registry)
    outcome = _run(executor, _edit_step())
    assert outcome.failed
    assert outcome.failure_kind is FailureKind.PLAN


def test_step_outcome_round_trips_through_serialization(repo, registry):
    executor = ExecutorLoop(registry)
    outcome = _run(executor, _edit_step(), edit=_edit_request())
    restored = StepOutcome.from_dict(outcome.to_dict())
    assert restored == outcome
    assert restored.evidence[0].metadata["step_id"] == "S2"


def test_missing_patch_target_reports_tool_failure(repo, registry):
    executor = ExecutorLoop(registry)
    outcome = _run(executor, _edit_step(path="ghost.py"),
                   edit=_edit_request(path="ghost.py"))
    assert outcome.failed
    assert outcome.failure_kind is FailureKind.TOOL
    assert "missing" in outcome.summary
