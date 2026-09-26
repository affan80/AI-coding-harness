"""Structured tool result contract (PRD §10).

Every tool returns this shape; large output is summarized here and kept in
full as an artifact on disk, never streamed into model context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Distinct, machine-checkable error kinds (issue #54: distinct results).
OK = "ok"
DENIED = "denied"  # command policy / permission rejection (never executed)
TIMEOUT = "timeout"  # command exceeded its budget and was terminated
NONZERO_EXIT = "nonzero_exit"
TRUNCATED = "truncated"  # output cap hit; full output kept as artifact
STALE_HASH = "stale_hash"  # file changed under us; write refused
OUT_OF_SCOPE = "out_of_scope"
TRAVERSAL = "path_traversal"
MISSING = "missing"
CONFLICT = "conflict"  # e.g. create_file on an existing path
ENVIRONMENT = "environment"  # missing tool/dependency on this machine
SPAWN_ERROR = "spawn_error"


@dataclass
class ToolResult:
    ok: bool
    summary: str
    error_kind: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)  # paths to full output
    truncated: bool = False
    duration_ms: int = 0

    @property
    def failed(self) -> bool:
        return not self.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "error_kind": self.error_kind,
            "data": self.data,
            "artifacts": list(self.artifacts),
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def failure(
        cls, kind: str, summary: str, **kwargs: Any
    ) -> ToolResult:
        return cls(ok=False, summary=summary, error_kind=kind, **kwargs)
