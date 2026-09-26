"""Issue #73: minimal GitHub issue/PR ingestion with thread summarization."""

import pytest

from harness.mcp.github import (
    GitHubMcpServer,
    parse_github_ref,
    summarize_thread,
)


@pytest.fixture()
def server(monkeypatch) -> GitHubMcpServer:
    srv = GitHubMcpServer(token="ghp_test_token", api_base="https://fake.api")
    monkeypatch.setattr(srv, "_request", _fake_request)
    return srv


def _fake_request(url: str, *, expect_list: bool = False):
    if url.endswith("/issues/123"):
        return {
            "number": 123,
            "title": "Login returns 500 on invalid password",
            "state": "open",
            "html_url": "https://github.com/acme/web/issues/123",
            "labels": [{"name": "bug"}, {"name": "auth"}],
            "body": "Repro: POST /login with wrong password. Expected 401, got 500.",
            "comments": 2,
            "comments_url": url + "/comments",
        }
    if url.endswith("/pulls/55"):
        return {
            "number": 55,
            "title": "Map InvalidCredentialsError to 401",
            "state": "open",
            "html_url": "https://github.com/acme/web/pull/55",
            "labels": [],
            "body": "Adds an exception handler in the login route.",
            "changed_files": 3,
            "additions": 21,
            "deletions": 4,
            "comments": 1,
            "comments_url": url + "/comments",
        }
    if url.endswith("/comments"):
        return [
            {"user": {"login": "alice"}, "body": "Reproduced on staging. " * 50},
            {"user": {"login": "bob"}, "body": "Root cause: missing exception handler."},
        ]
    raise AssertionError(f"unexpected url {url}")


def test_parses_urls_and_short_refs():
    assert parse_github_ref("https://github.com/acme/web/issues/123") == {
        "owner": "acme", "repo": "web", "number": "123",
    }
    assert parse_github_ref("https://github.com/acme/web/pull/55") == {
        "owner": "acme", "repo": "web", "number": "55",
    }
    assert parse_github_ref("acme/web#77") == {
        "owner": "acme", "repo": "web", "number": "77",
    }
    assert parse_github_ref("not a github ref") is None


def test_summarize_thread_keeps_count_and_latest_only():
    summary = summarize_thread([
        {"user": {"login": "alice"}, "body": "first"},
        {"user": {"login": "bob"}, "body": "second"},
    ])
    assert summary["comment_count"] == 2
    assert summary["latest_comment"]["author"] == "bob"
    assert summary["latest_comment"]["excerpt"] == "second"
    # earlier comments are dropped entirely — never copied into context
    assert "first" not in str(summary)


def test_fetch_issue_returns_smallest_useful_resource_with_url(server):
    result = server.invoke("fetch_issue", {"reference": "acme/web#123"})
    assert result.ok
    assert result.resource == "https://github.com/acme/web/issues/123"
    data = result.data
    assert data["title"] == "Login returns 500 on invalid password"
    assert data["state"] == "open"
    assert data["labels"] == ["bug", "auth"]
    assert "Expected 401" in data["body_excerpt"]
    assert len(data["body_excerpt"]) <= 1500
    assert data["thread"]["comment_count"] == 2
    assert data["thread"]["latest_comment"]["author"] == "bob"
    assert "Root cause" in data["thread"]["latest_comment"]["excerpt"]


def test_fetch_pull_request_reports_change_size(server):
    result = server.invoke("fetch_pull_request", {
        "reference": "https://github.com/acme/web/pull/55",
    })
    assert result.ok
    assert result.data["changed_files"] == 3
    assert result.data["additions"] == 21
    assert result.resource.endswith("/pull/55")


def test_unparseable_reference_is_a_clean_structured_failure(server):
    result = server.invoke("fetch_issue", {"reference": "definitely not a ref"})
    assert not result.ok
    assert "could not parse" in result.summary
    assert result.resource == ""


def test_token_never_appears_in_results_or_errors(server, monkeypatch):
    def reject(url, *, expect_list=False):
        from harness.mcp.gateway import McpUnavailableError

        raise McpUnavailableError("GitHub rejected the request (credentials)")

    monkeypatch.setattr(server, "_request", reject)
    result = server.invoke("fetch_issue", {"reference": "acme/web#123"})
    assert not result.ok
    assert "ghp_test_token" not in result.summary
    assert "ghp_test_token" not in str(result.to_dict())


def test_operations_declared_read_only(server):
    assert all(op.read_only for op in server.operations())
    assert {op.name for op in server.operations()} == {
        "fetch_issue", "fetch_pull_request",
    }
