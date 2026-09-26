import pytest
from pathlib import Path
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.tools.result import ToolResult, DENIED, OUT_OF_SCOPE, TRAVERSAL

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

def test_call_success():
    registry = ToolRegistry("/repo", allowed_scope=[])
    spec = ToolSpec("my_tool", "read", False, lambda path: ToolResult(ok=True, summary=f"read {path}"))
    registry.register(spec)
    res = registry.call("executor", "my_tool", path="foo.txt")
    assert res.ok
    assert "read " in res.summary
