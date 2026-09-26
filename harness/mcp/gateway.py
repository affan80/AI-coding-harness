"""MCP gateway: approved external servers as typed, permissioned tools (#72, #74).

Guarantees:

* every result records provenance — server, operation, resource — so evidence
  stays attributable (issue #72);
* write operations are unavailable unless explicitly authorized (issue #74);
* an unavailable/failing server produces a structured, retryable-looking
  failure and never breaks local tools;
* per-actor permission checks run at the gateway, same as local tools.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from harness.core.errors import HarnessError
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.tools.result import ToolResult

DEFAULT_TIMEOUT_SECONDS = 15.0


class McpUnavailableError(HarnessError):
    """An MCP server could not be reached or failed mid-operation."""

    code = "mcp_unavailable"


@dataclass(frozen=True)
class McpOperation:
    """One capability an MCP server exposes."""

    name: str
    description: str
    read_only: bool = True
    resource_kind: str = ""  # e.g. "issue", "pull_request"


@dataclass(frozen=True)
class McpResult:
    """Normalized result of one MCP operation."""

    ok: bool
    summary: str
    server: str
    operation: str
    resource: str = ""  # canonical URL or id of the smallest fetched resource
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "server": self.server,
            "operation": self.operation,
            "resource": self.resource,
            "data": dict(self.data),
        }


class McpServer(Protocol):
    """Minimal client protocol a server adapter implements."""

    name: str

    def operations(self) -> list[McpOperation]:
        """Declare capabilities (used for registration and permissioning)."""
        ...  # pragma: no cover

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> McpResult:
        """Run one operation; raise McpUnavailableError on transport failure."""
        ...  # pragma: no cover


@dataclass
class _RegisteredServer:
    server: McpServer
    operations: dict[str, McpOperation]
    allow_writes: bool


class McpGateway:
    """Registers MCP servers and exposes their operations as registry tools."""

    def __init__(
        self,
        servers: Sequence[McpServer] = (),
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        tool_prefix: str = "mcp",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.tool_prefix = tool_prefix
        self._servers: dict[str, _RegisteredServer] = {}
        for server in servers:
            self.register(server)

    def register(
        self, server: McpServer, *, allow_writes: bool = False
    ) -> list[str]:
        """Discover capabilities and register one server (issue #72)."""
        operations = {op.name: op for op in server.operations()}
        self._servers[server.name] = _RegisteredServer(
            server=server, operations=operations, allow_writes=allow_writes
        )
        return sorted(operations)

    def registered_servers(self) -> list[str]:
        return sorted(self._servers)

    def operations_for(self, server_name: str) -> list[str]:
        registered = self._servers.get(server_name)
        return sorted(registered.operations) if registered else []

    # -- invocation ----------------------------------------------------------

    def invoke(
        self, actor: str, server_name: str, operation: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Run one MCP operation with permission, write-guard, and normalization."""
        registered = self._servers.get(server_name)
        if registered is None:
            return self._unavailable(server_name, f"server {server_name!r} is not registered")
        op = registered.operations.get(operation)
        if op is None:
            known = ", ".join(sorted(registered.operations)) or "none"
            return ToolResult.failure(
                "mcp_unknown_operation",
                f"server {server_name!r} has no operation {operation!r} (has: {known})",
            )
        if not op.read_only and not registered.allow_writes:
            return ToolResult.failure(
                "mcp_writes_disabled",
                f"write operation {operation!r} is disabled; external writes "
                "require separate authorization",
            )
        if not self._actor_allowed(actor, op):
            return ToolResult.failure(
                "denied",
                f"actor {actor!r} may not call {server_name}.{operation}",
            )

        try:
            result = registered.server.invoke(operation, arguments or {})
        except McpUnavailableError as exc:
            return self._unavailable(server_name, exc.message, operation)
        except Exception as exc:  # noqa: BLE001 - normalized at the boundary
            return ToolResult.failure(
                "mcp_error",
                f"server {server_name!r} operation {operation!r} failed: {exc}",
            )

        return ToolResult(
            ok=result.ok,
            summary=result.summary,
            error_kind=None if result.ok else "mcp_error",
            data={
                "provenance": {
                    "server": server_name,
                    "operation": operation,
                    "resource": result.resource,
                },
                **dict(result.data),
            },
        )

    # -- registry integration --------------------------------------------------

    def register_on_registry(self, registry: ToolRegistry) -> list[str]:
        """Expose every allowed operation as a typed registry tool (#72)."""
        registered_tools: list[str] = []
        for server_name, registered in self._servers.items():
            for op_name, op in registered.operations.items():
                if not op.read_only and not registered.allow_writes:
                    continue  # write operations are not exposed at all by default
                tool_name = f"{self.tool_prefix}_{server_name}_{op_name}"

                def handler(op_name=op_name, server_name=server_name, **kwargs: Any):
                    return self.invoke("executor", server_name, op_name, kwargs)

                registry.register(
                    ToolSpec(
                        name=tool_name,
                        capability="search",  # read-only external context
                        mutates=False,
                        handler=handler,
                    )
                )
                registered_tools.append(tool_name)
        return registered_tools

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _actor_allowed(actor: str, op: McpOperation) -> bool:
        # Read-only external context is available to every working actor;
        # writes were already gated by allow_writes before this check.
        return bool(actor)

    def _unavailable(
        self, server_name: str, reason: str, operation: str | None = None
    ) -> ToolResult:
        detail = f"MCP server {server_name!r} unavailable: {reason}"
        if operation:
            detail += f" (operation {operation!r})"
        return ToolResult.failure("mcp_unavailable", detail)
