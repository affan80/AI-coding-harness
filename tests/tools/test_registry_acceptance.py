"""Issues #39/#40 acceptance: typed registry, permissions, policy, budgets.

The tool layer landed across the execution/tooling PRs; this module maps the
two issues' acceptance bullets 1:1 — a typed registry and ToolResult
contract (#39), and role permissions, scope, command policy, and budgets
enforced at the call boundary (#40).
"""

from pathlib import Path

import pytest

from harness.tools.factory import build_registry
from harness.tools.policy import PERMISSIONS, CommandPolicy, actor_allowed
from harness.tools.registry import ToolSpec
from harness.tools.result import ToolResult


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    pass\n")
    return tmp_path


def test_registry_is_typed_and_tools_are_registered(repo):
    registry = build_registry(repo, allowed_scope=[])
    # the PRD §10 MVP surface is registered and callable by name
    for name in ("read_file", "read_range", "create_file", "apply_patch",
                 "run_command", "git_status", "git_diff", "create_checkpoint"):
        assert name in registry.tools(), name


def test_tool_result_contract_is_structured(repo):
    registry = build_registry(repo, allowed_scope=[])
    result = registry.call("executor", "read_file", path="app.py")
    payload = result.to_dict()
    assert payload["ok"] is True
    assert isinstance(payload["summary"], str) and payload["summary"]
    assert "sha256" in payload["data"]
    # failures carry a machine-checkable kind, never just a message
    failure = registry.call("executor", "read_file", path="ghost.py")
    assert failure.ok is False
    assert failure.error_kind == "missing"


def test_role_permissions_are_enforced_at_the_boundary(repo):
    registry = build_registry(repo, allowed_scope=[])
    # verification may read but never patch
    assert registry.call("verification", "read_file", path="app.py").ok
    denied = registry.call(
        "verification", "apply_patch",
        path="app.py", diff="--- a/app.py\n+++ b/app.py\n", expected_old_hash="x",
    )
    assert not denied.ok and denied.error_kind == "denied"
    # the planner has no shell rights
    shell_denied = registry.call("planner", "run_command", command="echo hi")
    assert not shell_denied.ok and shell_denied.error_kind == "denied"
    # intent has no tool rights at all
    assert not actor_allowed("intent", "read")
    assert registry.call("intent", "read_file", path="app.py").error_kind == "denied"


def test_scope_guard_rejects_out_of_scope_paths(repo):
    registry = build_registry(repo, allowed_scope=["tests/"])
    outside = registry.call("executor", "read_file", path="app.py")
    assert not outside.ok
    assert outside.error_kind == "out_of_scope"
    inside = registry.call("executor", "read_file", path="tests/test_app.py")
    assert inside.ok


def test_path_traversal_is_rejected(repo):
    registry = build_registry(repo, allowed_scope=[])
    result = registry.call("executor", "read_file", path="../secrets.txt")
    assert not result.ok
    assert result.error_kind == "path_traversal"


def test_command_policy_blocks_destructive_commands(repo):
    registry = build_registry(repo, allowed_scope=[])
    result = registry.call("executor", "run_command", command="rm -rf /")
    assert not result.ok
    assert result.error_kind == "denied"


def test_command_timeouts_are_distinct_structured_results(tmp_path):
    from harness.tools.shell import run_command

    result = run_command(
        "sleep 5",
        policy=CommandPolicy(default_timeout_seconds=1),
        working_dir=tmp_path,
    )
    assert not result.ok
    assert result.error_kind == "timeout"


def test_permission_table_covers_every_working_role():
    for role in ("executor", "verification", "recovery", "audit",
                 "repository", "planner"):
        assert role in PERMISSIONS
    # roles are least-privilege: only the executor may patch
    assert "patch" in PERMISSIONS["executor"]
    for role in ("verification", "recovery", "planner", "repository"):
        assert "patch" not in PERMISSIONS[role]


def test_registry_rejects_unknown_tools_and_actors(repo):
    registry = build_registry(repo, allowed_scope=[])
    assert registry.call("executor", "teleport").error_kind == "denied"
    assert registry.call("", "read_file", path="app.py").error_kind == "denied"


def test_specs_carry_mutation_flags_for_the_evidence_layer(repo):
    registry = build_registry(repo, allowed_scope=[])
    spec = registry._tools["apply_patch"]
    assert spec.mutates is True
    assert registry._tools["read_file"].mutates is False
    # unknown-tool construction stays typed
    registry.register(ToolSpec("noop", capability="read", mutates=False,
                               handler=lambda: ToolResult(ok=True, summary="n")))
    assert registry.call("executor", "noop").ok
