"""Read-only GitHub issue/PR ingestion as an MCP server (issue #73).

Fetches the *smallest useful* resource: title, state, labels, a trimmed body,
and a summarized comment thread (count + latest comment) — never a full
thread. The canonical URL/id is preserved on every result so the original
evidence stays retrievable (PRD §11 context policy).

The token comes from the ``HARNESS_GITHUB_TOKEN`` environment variable and is
only ever sent as a request header; it never appears in results or errors.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from harness.mcp.gateway import McpOperation, McpResult, McpUnavailableError

API_BASE = "https://api.github.com"
TOKEN_ENV = "HARNESS_GITHUB_TOKEN"
BODY_LIMIT_CHARS = 1_500
MAX_COMMENTS_FETCHED = 5

ISSUE_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:issues|pull)/(?P<number>\d+)"
)
HASH_RE = re.compile(r"^(?P<owner>[^/#\s]+)/(?P<repo>[^/#\s]+)#(?P<number>\d+)$")


def parse_github_ref(reference: str) -> dict[str, str] | None:
    """Parse an issue/PR URL or ``owner/repo#123`` into its coordinates."""
    match = ISSUE_URL_RE.search(reference)
    if match:
        return match.groupdict()
    match = HASH_RE.match(reference.strip())
    if match:
        return match.groupdict()
    return None


def summarize_thread(comments: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize a comment thread: count + latest author/excerpt only."""
    return {
        "comment_count": len(comments),
        "latest_comment": {
            "author": comments[-1].get("user", {}).get("login", "unknown")
            if comments
            else None,
            "excerpt": (comments[-1].get("body", "") or "")[:300] if comments else "",
        },
    }


class GitHubMcpServer:
    """Read-only GitHub MCP server: fetch_issue / fetch_pull_request."""

    name = "github"

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self._token = token if token is not None else os.environ.get(TOKEN_ENV, "")
        self._api_base = api_base

    def operations(self) -> list[McpOperation]:
        return [
            McpOperation(
                name="fetch_issue",
                description="Fetch one GitHub issue: metadata, trimmed body, "
                "summarized thread.",
                read_only=True,
                resource_kind="issue",
            ),
            McpOperation(
                name="fetch_pull_request",
                description="Fetch one GitHub pull request: metadata, trimmed "
                "body, changed-file count.",
                read_only=True,
                resource_kind="pull_request",
            ),
        ]

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> McpResult:
        if operation == "fetch_issue":
            return self._fetch(arguments, kind="issues")
        if operation == "fetch_pull_request":
            return self._fetch(arguments, kind="pulls")
        raise KeyError(f"unknown github operation: {operation}")

    # -- internals -------------------------------------------------------------

    def _fetch(self, arguments: Mapping[str, Any], *, kind: str) -> McpResult:
        reference = str(arguments.get("reference", ""))
        coords = parse_github_ref(reference)
        if coords is None:
            return McpResult(
                ok=False,
                summary=(
                    f"could not parse GitHub reference {reference!r}; use an "
                    "issue/PR URL or owner/repo#123"
                ),
                server=self.name,
                operation=kind,
            )
        resource = (
            f"{self._api_base}/repos/{coords['owner']}/{coords['repo']}/"
            f"{kind}/{coords['number']}"
        )
        payload = self._request(resource)
        data: dict[str, Any] = {
            "title": payload.get("title", ""),
            "state": payload.get("state", ""),
            "url": payload.get("html_url", resource),
            "number": payload.get("number", int(coords["number"])),
            "labels": [label.get("name", "") for label in payload.get("labels", [])],
            "body_excerpt": (payload.get("body") or "")[:BODY_LIMIT_CHARS],
        }
        if kind == "pulls":
            data["changed_files"] = payload.get("changed_files", 0)
            data["additions"] = payload.get("additions", 0)
            data["deletions"] = payload.get("deletions", 0)

        comments_url = payload.get("comments_url")
        comments: list[Mapping[str, Any]] = []
        if comments_url:
            try:
                comments = self._request(comments_url, expect_list=True)
            except McpUnavailableError:
                comments = []  # thread summary is best-effort; resource is kept
        data["thread"] = summarize_thread(comments)

        return McpResult(
            ok=True,
            summary=(
                f"{kind[:-1]} #{data['number']}: {data['title']} [{data['state']}] "
                f"({data['thread']['comment_count']} comments, body excerpt kept)"
            ),
            server=self.name,
            operation=kind,
            resource=data["url"],
            data=data,
        )

    def _request(self, url: str, *, expect_list: bool = False) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise McpUnavailableError(
                    "GitHub rejected the request (credentials/permissions)",
                    details={"status": exc.code},
                ) from None
            if exc.code == 404:
                raise McpUnavailableError(
                    "GitHub resource not found", details={"status": 404}
                ) from None
            raise McpUnavailableError(
                f"GitHub API error ({exc.code})", details={"status": exc.code}
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else "timeout"
            raise McpUnavailableError(
                f"GitHub API unreachable: {reason}"
            ) from None
