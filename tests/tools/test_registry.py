"""Issues #39/#40: tool boundary — schemas, normalization, and trust boundary.

Denied or invalid calls must return structured evidence and never touch the
repository or the OS; every executed call returns the documented ToolResult
shape.
"""

import pytest

from harness.tools.factory import build_registry
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.tools.result import DENIED, OUT_OF_SCOPE, TRAVERSAL, ToolResult

RESULT_KEYS = {
    "ok", "summary", "error_kind", "data",
    "artifacts", "truncated", "duration_ms",
}


def _marker_handler(markers: dict, name: str):
    def handler(**kwargs):
        markers[name] = markers.get(name, 0) + 1
        return ToolResult(ok=True, summary=f"{name} ran")
    return handler


def _ok(value: str = "done") -> ToolResult:
    return ToolResult(ok=True, summary=value)


# ---------------------------------------------------------------- #39 shape


def test_successful_call_returns_documented_result_shape(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    registry = build_registry(tmp_path, allowed_scope=[])
    result = registry.call("executor", "read_file", path="app.py")
    assert result.ok
    assert result.to_dict().keys() == RESULT_KEYS
    assert result.duration_ms >= 0
    assert result.data["content"] == "print('hi')\n"


def test_unknown_tool_is_a_structured_denial(tmp_path):
    registry = ToolRegistry(tmp_path, [])
    result = registry.call("executor", "no_such_tool")
    assert not result.ok
    assert result.error_kind == "denied"
    assert result.to_dict().keys() == RESULT_KEYS


def test_handler_exception_is_normalized_into_tool_result(tmp_path):
    registry = ToolRegistry(tmp_path, [])
    registry.register(ToolSpec(
        "explode", capability="read", mutates=False,
        handler=lambda: (_ for _ in ()).throw(ValueError("boom")),
    ))
    result = registry.call("executor", "explode")
    assert not result.ok
    assert result.error_kind == "handler_error"
    assert "boom" in result.summary


# ------------------------------------------------------- #39 input schemas


def test_schema_rejects_unknown_argument_without_running(tmp_path):
    markers: dict = {}
    registry = ToolRegistry(tmp_path, [])
    registry.register(ToolSpec(
        "greet", capability="read", mutates=False,
        handler=_marker_handler(markers, "greet"),
        input_schema={"target": str},
    ))
    result = registry.call("executor", "greet", target="x", evil="1")
    assert result.error_kind == "invalid_input"
    assert "evil" in result.summary
    assert markers == {}, "handler must not run on schema violation"


def test_schema_rejects_wrong_argument_type(tmp_path):
    markers: dict = {}
    registry = ToolRegistry(tmp_path, [])
    registry.register(ToolSpec(
        "greet", capability="read", mutates=False,
        handler=_marker_handler(markers, "greet"),
        input_schema={"target": str},
    ))
    result = registry.call("executor", "greet", target=42)
    assert result.error_kind == "invalid_input"
    assert markers == {}


def test_schema_rejects_missing_required_argument(tmp_path):
    markers: dict = {}
    registry = ToolRegistry(tmp_path, [])
    registry.register(ToolSpec(
        "greet", capability="read", mutates=False,
        handler=_marker_handler(markers, "greet"),
        input_schema={"target": str, "greeting": (str, type(None))},
    ))
    result = registry.call("executor", "greet")  # greeting optional, name not
    assert result.error_kind == "invalid_input"
    assert "target" in result.summary
    assert markers == {}

    ok = registry.call("executor", "greet", target="a")  # optional omitted: fine
    assert ok.ok


# -------------------------------------------------- #40 trust boundary


def test_unauthorized_actor_never_reaches_the_handler(tmp_path):
    markers: dict = {}
    registry = ToolRegistry(tmp_path, [])
    registry.register(ToolSpec(
        "greet", capability="patch", mutates=True,
        handler=_marker_handler(markers, "greet"),
        input_schema={"target": str},
    ))
    result = registry.call("planner", "greet", target="x")  # planner lacks patch
    assert result.error_kind == "denied"
    assert markers == {}


def test_traversal_write_is_rejected_and_touches_nothing(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    canary = outside / "escape.txt"
    registry = build_registry(tmp_path, allowed_scope=[])
    result = registry.call(
        "executor", "create_file", path=str(outside / "escape.txt"),
        content="pwned",
    )
    assert result.error_kind == "path_traversal"
    assert not canary.exists()
    assert list(tmp_path.rglob("*")) == [], "repository untouched"


def test_out_of_scope_write_is_rejected_and_touches_nothing(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "key.txt").write_text("keep me")
    registry = build_registry(tmp_path, allowed_scope=["./src"])
    result = registry.call(
        "executor", "create_file", path="secrets/key.txt", content="x"
    )
    assert result.error_kind == "out_of_scope"
    assert (tmp_path / "secrets" / "key.txt").read_text() == "keep me"


def test_command_policy_denial_leaves_the_filesystem_untouched(tmp_path):
    canary = tmp_path / "precious.txt"
    canary.write_text("keep me")
    registry = build_registry(tmp_path, allowed_scope=[])
    result = registry.call(
        "executor", "run_command", command="rm -rf precious.txt"
    )
    assert result.error_kind == "denied"
    assert canary.read_text() == "keep me"
    assert list(tmp_path.iterdir()) == [canary], "no side effects on disk"


def test_over_budget_timeout_is_rejected_before_spawn(tmp_path):
    registry = build_registry(tmp_path, allowed_scope=[])
    result = registry.call(
        "executor", "run_command",
        command="touch spawned.txt", timeout_seconds=10_000,
    )
    assert result.error_kind == "denied"
    assert "policy maximum" in result.summary
    assert not (tmp_path / "spawned.txt").exists(), "command never ran"


def test_exhausted_call_budget_rejects_before_execution(tmp_path):
    markers: dict = {}
    registry = ToolRegistry(tmp_path, [], call_budget={"executor": 2})
    registry.register(ToolSpec(
        "count", capability="read", mutates=False,
        handler=_marker_handler(markers, "count"),
    ))
    assert registry.call("executor", "count").ok
    assert registry.call("executor", "count").ok
    third = registry.call("executor", "count")
    assert third.error_kind == "budget_exhausted"
    assert "2/2" in third.summary
    assert markers == {"count": 2}, "handler ran only for budgeted calls"


def test_denied_calls_do_not_consume_call_budget(tmp_path):
    markers: dict = {}
    registry = ToolRegistry(tmp_path, [], call_budget={"executor": 1})
    registry.register(ToolSpec(
        "count", capability="read", mutates=False,
        handler=_marker_handler(markers, "count"),
    ))
    registry.register(ToolSpec(
        "write_thing", capability="patch", mutates=True,
        handler=_marker_handler(markers, "write_thing"),
    ))
    # Denied call (planner lacks patch) must not spend executor's budget:
    assert registry.call("planner", "write_thing").error_kind == "denied"
    assert registry.call("executor", "count").ok
    assert registry.calls_used["executor"] == 1


@pytest.mark.parametrize("actor,capability", [
    ("intent", "read"),
    ("planner", "patch"),
    ("recovery", "patch"),
])
def test_permission_matrix_denials_are_structured(tmp_path, actor, capability):
    registry = ToolRegistry(tmp_path, [])
    registry.register(ToolSpec(
        "tool", capability=capability, mutates=False,
        handler=lambda: _ok(),
    ))
    result = registry.call(actor, "tool")
    assert result.error_kind == "denied"
    assert capability in result.summary


# ------------------------------------------------- #39/#40 regression pins
# (from the first tools acceptance slice; kept alongside the schema suite)

def test_tool_registry_registration():
    registry = ToolRegistry("/repo", allowed_scope=[])
    spec = ToolSpec("my_tool", "read", False, lambda: ToolResult(ok=True, summary="ok"))
    registry.register(spec)
    assert "my_tool" in registry.tools()


def test_tool_registry_out_of_scope():
    registry = ToolRegistry("/repo", allowed_scope=["src/"])
    resolved, error = registry.validate_path("tests/test_foo.py")
    assert resolved is None
    assert error is not None
    assert error.error_kind == OUT_OF_SCOPE
    assert "outside the approved scope" in error.summary


def test_tool_registry_in_scope():
    registry = ToolRegistry("/repo", allowed_scope=["src/"])
    resolved, error = registry.validate_path("src/foo.py")
    assert error is None
    assert resolved is not None
    assert str(resolved).endswith("src/foo.py")


def test_tool_registry_traversal():
    registry = ToolRegistry("/repo", allowed_scope=[])
    resolved, error = registry.validate_path("../outside.py")
    assert resolved is None
    assert error is not None
    assert error.error_kind == TRAVERSAL


def test_call_missing_tool():
    registry = ToolRegistry("/repo", allowed_scope=[])
    res = registry.call("executor", "unknown_tool")
    assert res.error_kind == DENIED
    assert "unknown tool" in res.summary


def test_call_permission_denied():
    registry = ToolRegistry("/repo", allowed_scope=[])
    spec = ToolSpec("my_tool", "patch", True, lambda: ToolResult(ok=True, summary="ok"))
    registry.register(spec)
    res = registry.call("intent", "my_tool")
    assert res.error_kind == DENIED
    assert "missing capability" in res.summary
