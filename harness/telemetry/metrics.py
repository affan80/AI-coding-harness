"""Usage metrics derived from the event log (issue #70).

Metrics are *derived* from recorded events, so live totals always reconcile
with the persisted ``events.jsonl`` / ``tool-calls.jsonl``. Rendering is a
handful of short lines suitable for the CLI/TUI status bar.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from harness.telemetry.events import Event, EventType

CONTEXT_KEYS = ("discovered_files", "discovered_tokens", "selected_files", "selected_tokens")


@dataclass
class UsageMetrics:
    """Session-level usage totals (PRD §12.9, §26)."""

    model_calls: int = 0
    tool_calls: int = 0
    patches: int = 0
    failures: int = 0
    retries: int = 0
    recovery_attempts: int = 0
    audits: int = 0
    verifications: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float | None = None
    context_discovered_files: int = 0
    context_discovered_tokens: int = 0
    context_selected_files: int = 0
    context_selected_tokens: int = 0
    compaction_events: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def apply(self, event: Event) -> None:
        """Fold one event into the totals (the reconciliation rule)."""
        if event.type is EventType.MODEL_CALL:
            self.model_calls += 1
            self.latency_ms += event.duration_ms
            self.input_tokens += int(event.data.get("input_tokens", 0))
            self.output_tokens += int(event.data.get("output_tokens", 0))
            cost = event.data.get("cost_usd")
            if cost is not None:
                self.cost_usd = (self.cost_usd or 0.0) + float(cost)
        elif event.type is EventType.TOOL_CALL:
            self.tool_calls += 1
            self.latency_ms += event.duration_ms
        elif event.type is EventType.PATCH:
            self.patches += 1
        elif event.type is EventType.FAILURE:
            self.failures += 1
        elif event.type is EventType.RETRY:
            self.retries += 1
            if event.data.get("recovered"):
                self.recovery_attempts += 1
        elif event.type is EventType.AUDIT:
            self.audits += 1
        elif event.type is EventType.VERIFICATION:
            self.verifications += 1
        elif event.type is EventType.CONTEXT:
            for key in CONTEXT_KEYS:
                if key in event.data:
                    setattr(self, f"context_{key}", int(event.data[key]))
            if event.data.get("compaction"):
                self.compaction_events += 1

    @classmethod
    def from_events(cls, events: Iterable[Event]) -> UsageMetrics:
        metrics = cls()
        for event in events:
            metrics.apply(event)
        return metrics

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "patches": self.patches,
            "failures": self.failures,
            "retries": self.retries,
            "recovery_attempts": self.recovery_attempts,
            "audits": self.audits,
            "verifications": self.verifications,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "context_discovered_files": self.context_discovered_files,
            "context_discovered_tokens": self.context_discovered_tokens,
            "context_selected_files": self.context_selected_files,
            "context_selected_tokens": self.context_selected_tokens,
            "compaction_events": self.compaction_events,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UsageMetrics:
        metrics = cls()
        for key, value in data.items():
            if hasattr(metrics, key) and key != "extra":
                setattr(metrics, key, value)
        return metrics

    def render_lines(self) -> list[str]:
        """Concise live view for the CLI/TUI (issue #70)."""
        lines = [
            f"model calls: {self.model_calls}   tool calls: {self.tool_calls}   "
            f"patches: {self.patches}",
            f"tokens: {self.input_tokens} in / {self.output_tokens} out "
            f"(total {self.total_tokens})",
            f"latency: {self.latency_ms} ms   failures: {self.failures}   "
            f"recovery attempts: {self.recovery_attempts}",
        ]
        if self.cost_usd is not None:
            lines.append(f"cost: ${self.cost_usd:.4f}")
        lines.append(
            f"context: {self.context_selected_files}/{self.context_discovered_files} "
            f"files, {self.context_selected_tokens}/{self.context_discovered_tokens} "
            f"tokens selected"
        )
        if self.compaction_events:
            lines.append(f"compactions: {self.compaction_events}")
        return lines
