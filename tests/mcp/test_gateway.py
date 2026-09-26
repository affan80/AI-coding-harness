"""Issues #72/#74: MCP gateway — permissioned typed tools, provenance, fallback."""

import pytest

from harness.mcp.gateway import (
    McpGateway,
    McpOperation,
    McpResult,
    McpUnavailableError,
)
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.tools.result import ToolResult


class FakeServer:
    """Deterministic in-memory MCP server for tests."""

    name = "fake"

    def __init__(self, *, fail=False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def operations(self):
        return [
            McpOperation(name="search_docs", description="doc search",
                         read_only=True, resource_kind="doc"),
            McpOperation(name="create_comment", description="write a comment",
                         read_only=False, resource_kind="comment"),
        ]

    def invoke(self, operation, arguments):
        self.calls.append((operation, dict(arguments)))
        if self.fail:
            raise McpUnavailableError("connection refused")
        if operation == "search_docs":
            return McpResult(
                ok=True, summary="2 docs match 'auth'", server=self.name,
                operation=operation, resource="docs://auth-001",
                data={"matches": ["auth-001", "auth-002"]},
            )
        return McpResult(ok=True, summary="comment created", server=self.name,
                         operation=operation, resource="comment-9")


class FlakyServer(FakeServer):
    name = "flaky"

    def operations(self):
        raise McpUnavailableError("server list failed")


@pytest.fixture()
def gateway() -> McpGateway:
    return McpGateway(servers=[FakeServer()])


def test_registers_servers_and_discovers_capabilities(gateway):
    assert gateway.registered_servers() == ["fake"]
    assert gateway.operations_for("fake") == ["create_comment", "search_docs"]
    assert gateway.operations_for("nope") == []


def test_results_record_server_operation_resource_provenance(gateway):
    result = gateway.invoke("executor", "fake", "search_docs", {"q": "auth"})
    assert result.ok
    provenance = result.data["provenance"]
    assert provenance == {
        "server": "fake",
        "operation": "search_docs",
        "resource": "docs://auth-001",
    }


def test_write_operations_disabled_without_authorization(gateway):
    server = gateway._servers["fake"].server
    result = gateway.invoke("executor", "fake", "create_comment", {"body": "hi"})
    assert not result.ok
    assert result.error_kind == "mcp_writes_disabled"
    assert "separate authorization" in result.summary
    assert server.calls == []  # the write never reached the server


def test_write_operations_available_when_authorized():
    server = FakeServer()
    gateway = McpGateway()
    gateway.register(server, allow_writes=True)
    result = gateway.invoke("executor", "fake", "create_comment", {"body": "hi"})
    assert result.ok
    assert server.calls == [("create_comment", {"body": "hi"})]


def test_unavailable_server_degrades_without_breaking_local_tools(gateway):
    failing = FakeServer(fail=True)
    failing.name = "flaky"
    gateway.register(failing)

    failure = gateway.invoke("executor", "flaky", "search_docs", {})
    assert not failure.ok
    assert failure.error_kind == "mcp_unavailable"
    assert "connection refused" in failure.summary

    # local tools keep working: register a plain local tool and call it
    registry = ToolRegistry("/tmp", allowed_scope=[])
    gateway.register_on_registry(registry)
    registry.register(
        ToolSpec(
            name="local_read", capability="read", mutates=False,
            handler=lambda path: ToolResult(ok=True, summary=f"read {path}"),
        )
    )
    local = registry.call("executor", "local_read", path="app.py")
    # The registry resolves relative paths against the repo root (#92).
    assert local.ok and "app.py" in local.summary


def test_registry_integration_exposes_read_only_ops_as_typed_tools(gateway):
    registry = ToolRegistry("/tmp", allowed_scope=[])
    tool_names = gateway.register_on_registry(registry)
    # only the read-only operation is exposed; writes stay unregistered
    assert tool_names == ["mcp_fake_search_docs"]
    assert "mcp_fake_create_comment" not in registry.tools()

    result = registry.call("executor", "mcp_fake_search_docs", q="auth")
    assert result.ok
    assert result.data["provenance"]["server"] == "fake"
    # actors without the capability are denied at the registry boundary
    denied = registry.call("intent", "mcp_fake_search_docs", q="auth")
    assert not denied.ok and denied.error_kind == "denied"


def test_unknown_server_and_operation_are_structured_failures(gateway):
    missing_server = gateway.invoke("executor", "ghost", "search_docs", {})
    assert missing_server.error_kind == "mcp_unavailable"

    missing_op = gateway.invoke("executor", "fake", "teleport", {})
    assert missing_op.error_kind == "mcp_unknown_operation"


def test_capability_discovery_failure_is_contained():
    gateway = McpGateway()
    with pytest.raises(McpUnavailableError):
        gateway.register(FlakyServer())
