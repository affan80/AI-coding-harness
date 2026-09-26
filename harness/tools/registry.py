"""Typed tool registry: every repository interaction goes through here.

The registry enforces (1) the per-agent capability table, (2) typed input
validation against each tool's declared schema, (3) target-path validation
for mutating tools, and (4) normalization of handler failures, so every call
returns the documented ToolResult shape and no write can bypass the tool
boundary (issues #39/#51, FR-07, FR-15).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .policy import actor_allowed
from .result import (
    BUDGET_EXHAUSTED,
    DENIED,
    HANDLER_ERROR,
    INVALID_INPUT,
    OUT_OF_SCOPE,
    TRAVERSAL,
    ToolResult,
)

# A schema maps a keyword argument to the type(s) it accepts. Handlers with
# optional arguments declare ``None`` explicitly in the allowed types.
Schema = dict[str, type | tuple[type, ...]]


@dataclass
class ToolSpec:
    name: str
    capability: str  # read | patch | shell | git | tests | search
    mutates: bool
    handler: Callable[..., ToolResult]
    # Declared kwargs → accepted type(s); ``None`` in the tuple marks the
    # argument optional. An empty schema skips validation (legacy specs).
    input_schema: Schema = field(default_factory=dict)


class ToolRegistry:
    """Named tool table with permission and schema checks at the call boundary."""

    def __init__(
        self,
        repo_root: Path | str,
        allowed_scope: list[str],
        *,
        call_budget: dict[str, int] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.allowed_scope = list(allowed_scope)  # empty = whole repository
        # Per-actor tool-call budget (FR-14): actor → max executed calls.
        # Denials never consume budget; an exhausted actor is rejected
        # before any handler runs.
        self.call_budget = dict(call_budget) if call_budget else None
        self.calls_used: dict[str, int] = dict.fromkeys(self.call_budget or {}, 0)
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

    def _validate_input(
        self, spec: ToolSpec, kwargs: dict[str, Any]
    ) -> ToolResult | None:
        """Check kwargs against the tool's declared schema before execution."""
        if not spec.input_schema:
            return None  # legacy spec without a declared schema
        unknown = set(kwargs) - set(spec.input_schema)
        if unknown:
            return ToolResult.failure(
                INVALID_INPUT,
                f"tool {spec.name!r} got unexpected argument(s): "
                f"{sorted(unknown)}; schema: {sorted(spec.input_schema)}",
            )
        missing = [
            key
            for key, allowed in spec.input_schema.items()
            if key not in kwargs and type(None) not in _allowed(allowed)
        ]
        if missing:
            return ToolResult.failure(
                INVALID_INPUT,
                f"tool {spec.name!r} is missing required argument(s): {missing}",
            )
        for key, allowed in spec.input_schema.items():
            if key not in kwargs:
                continue
            value = kwargs[key]
            if value is not None and not isinstance(value, _allowed(allowed)):
                return ToolResult.failure(
                    INVALID_INPUT,
                    f"argument {key!r} of tool {spec.name!r} must be "
                    f"one of {_type_names(allowed)}, got {type(value).__name__}",
                )
        return None

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
        input_error = self._validate_input(spec, kwargs)
        if input_error is not None:
            return input_error
        # Path validation runs for every path-bearing tool, read or write:
        # traversal and scope escapes are rejected before any handler runs.
        if "path" in kwargs:
            resolved, error = self.validate_path(str(kwargs["path"]))
            if error is not None:
                return error
            kwargs["path"] = resolved
        if self.call_budget is not None:
            used = self.calls_used.get(actor, 0)
            limit = self.call_budget.get(actor)
            if limit is not None and used >= limit:
                return ToolResult.failure(
                    BUDGET_EXHAUSTED,
                    f"actor {actor!r} exhausted its tool-call budget "
                    f"({used}/{limit}); nothing was executed",
                )
        started = time.monotonic()
        try:
            result = spec.handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 — boundary must normalize
            return ToolResult.failure(
                HANDLER_ERROR,
                f"tool {spec.name!r} failed: {type(exc).__name__}: {exc}",
            )
        if self.call_budget is not None and actor in self.call_budget:
            self.calls_used[actor] = self.calls_used.get(actor, 0) + 1
        if result.duration_ms == 0:
            result.duration_ms = int((time.monotonic() - started) * 1000)
        return result


def _allowed(allowed: type | tuple[type, ...]) -> tuple[type, ...]:
    return allowed if isinstance(allowed, tuple) else (allowed,)


def _type_names(allowed: type | tuple[type, ...]) -> list[str]:
    return [t.__name__ for t in _allowed(allowed)]
