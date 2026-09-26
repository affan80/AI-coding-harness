"""MCP gateway and GitHub ingestion (issue #18, PRD §11).

External integrations are exposed as typed, permissioned tools through the
tool registry. Local file/search/git/test tools never depend on MCP: when a
server is unavailable the gateway reports a structured failure and the
harness keeps working offline (graceful degradation, issue #74).
"""

from harness.mcp.gateway import (
    McpGateway,
    McpOperation,
    McpResult,
    McpServer,
    McpUnavailableError,
)
from harness.mcp.github import GitHubMcpServer

__all__ = [
    "McpGateway",
    "McpOperation",
    "McpResult",
    "McpServer",
    "McpUnavailableError",
    "GitHubMcpServer",
]
