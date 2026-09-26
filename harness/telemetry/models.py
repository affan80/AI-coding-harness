"""Evidence records for tool activity (PRD §10 tool result contract)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from harness.core.models import TerminalOutcome


class RunStatus(StrEnum):
    """Status of a run directory (issue #29 exit-code contract).

    Core's ``TerminalOutcome`` classifies finished orchestrator sessions;
    ``PARTIAL`` additionally lets the evidence layer honestly report runs
    that produced evidence without reaching verification.
    """

    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def from_terminal_outcome(cls, outcome: TerminalOutcome) -> RunStatus:
        mapping = {
            TerminalOutcome.COMPLETED: cls.VERIFIED,
            TerminalOutcome.FAILED: cls.FAILED,
            TerminalOutcome.CANCELLED: cls.CANCELLED,
        }
        return mapping[outcome]


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to raw output stored under artifacts/, never inline."""

    path: str  # relative to the run directory
    sha256: str
    size: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ArtifactRef:
        return cls(path=data["path"], sha256=data["sha256"], size=data["size"])


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool invocation with its evidence reference (PRD §21; FR-12)."""

    id: str  # stable evidence id, e.g. "tool-0007"
    seq: int
    name: str
    args_summary: str
    status: str  # "ok" | "error" | "denied" | "timeout"
    started_at: str  # ISO-8601 UTC
    duration_ms: int
    summary: str  # concise result; raw output lives in the artifact
    truncated: bool
    artifact: ArtifactRef | None

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ToolCallRecord:
        artifact = data.get("artifact")
        return cls(
            id=data["id"],
            seq=data["seq"],
            name=data["name"],
            args_summary=data["args_summary"],
            status=data["status"],
            started_at=data["started_at"],
            duration_ms=data["duration_ms"],
            summary=data["summary"],
            truncated=data["truncated"],
            artifact=ArtifactRef.from_dict(artifact) if artifact else None,
        )
