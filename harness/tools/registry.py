"""Typed tool registry: every repository interaction goes through here.

The registry enforces (1) the per-agent capability table and (2) target-path
validation for mutating tools, so no write can bypass the tool boundary and
actors cannot invoke tools they have no right to (issue #51, FR-07, FR-15).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .policy import actor_allowed
from .result import DENIED, OUT_OF_SCOPE, TRAVERSAL, ToolResult


@dataclass
class ToolSpec:
    name: str
    capability: str  # read | patch | shell | git | tests | search
    mutates: bool
    handler: Callable[..., ToolResult]


class ToolRegistry:
    """Named tool table with permission checks at the call boundary."""

    def __init__(self, repo_root: Path | str, allowed_scope: list[str]) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.allowed_scope = list(allowed_scope)  # empty = whole repository
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def tools(self) -> list[str]:
        return sorted(self._tools)

    def validate_path(self, raw_path: str) -> tuple[Path | None, ToolResult | None]:
        """Resolve ``raw_path`` inside the repo; reject traversal/scope escapes."""
        candidate = Path(raw_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.repo_root / candidate).resolve()
        try:
            resolved.relative_to(self.repo_root)
        except ValueError:
            return None, ToolResult.failure(
                TRAVERSAL,
                f"path {raw_path!r} escapes the repository root",
            )
        if not self._in_scope(resolved):
            return None, ToolResult.failure(
                OUT_OF_SCOPE,
                f"path {raw_path!r} is outside the approved scope "
                f"{self.allowed_scope}",
            )
        return resolved, None

    def _in_scope(self, resolved: Path) -> bool:
        if not self.allowed_scope:
            return True
        rel = resolved.relative_to(self.repo_root).as_posix()
        return any(
            rel == scope.strip("./") or rel.startswith(scope.strip("./").rstrip("/") + "/")
            for scope in self.allowed_scope
        )

    def call(self, actor: str, name: str, **kwargs: Any) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult.failure(DENIED, f"unknown tool: {name}")
        if not actor_allowed(actor, spec.capability):
            return ToolResult.failure(
                DENIED,
                f"actor {actor!r} may not use tool {name!r} "
                f"(missing capability {spec.capability!r})",
            )
        # Path validation runs for every path-bearing tool, read or write:
        # traversal and scope escapes are rejected before any handler runs.
        if "path" in kwargs:
            resolved, error = self.validate_path(str(kwargs["path"]))
            if error is not None:
                return error
            kwargs["path"] = resolved
        return spec.handler(**kwargs)
