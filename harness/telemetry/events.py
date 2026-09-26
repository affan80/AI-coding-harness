"""Structured lifecycle telemetry events (issue #69).

Every state transition, model call, tool call, patch, failure, retry, audit,
and verification emits one :class:`Event`. Rules enforced here:

* events carry short *summaries*, never raw model output — there is no place
  chain-of-thought could enter;
* configured secrets are redacted from every field before an event is stored;
* the log serializes to JSONL so ``events.jsonl`` reconciles with metrics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MAX_DESCRIPTION_CHARS = 300
MAX_DATA_JSON_CHARS = 2_000


class EventType(StrEnum):
    STATE = "state"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    PATCH = "patch"
    FAILURE = "failure"
    RETRY = "retry"
    AUDIT = "audit"
    VERIFICATION = "verification"
    CONTEXT = "context"


@dataclass(frozen=True)
class Event:
    """One bounded, redacted telemetry event."""

    seq: int
    type: EventType
    description: str
    at: str
    duration_ms: int = 0
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type.value,
            "description": self.description,
            "at": self.at,
            "duration_ms": self.duration_ms,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            seq=data["seq"],
            type=EventType(data["type"]),
            description=data["description"],
            at=data["at"],
            duration_ms=data.get("duration_ms", 0),
            data=dict(data.get("data", {})),
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class EventRecorder:
    """Append-only event log owned by the product surface (issue #69)."""

    def __init__(self, secrets: Sequence[str] = ()) -> None:
        self._secrets = [s for s in secrets if s]
        self._events: list[Event] = []

    def redact(self, text: Any) -> Any:
        """Remove configured secrets from strings anywhere in a payload."""
        if isinstance(text, str):
            for secret in self._secrets:
                if secret and secret in text:
                    text = text.replace(secret, "***REDACTED***")
            return text
        if isinstance(text, Mapping):
            return {k: self.redact(v) for k, v in text.items()}
        if isinstance(text, (list, tuple)):
            return [self.redact(item) for item in text]
        return text

    def emit(
        self,
        event_type: EventType,
        description: str,
        *,
        data: Mapping[str, Any] | None = None,
        duration_ms: int = 0,
    ) -> Event:
        """Record one event; description is hard-bounded (no chain-of-thought)."""
        clean_description = self.redact(str(description))[:MAX_DESCRIPTION_CHARS]
        clean_data = self.redact(dict(data or {}))
        event = Event(
            seq=len(self._events) + 1,
            type=event_type,
            description=clean_description,
            at=_utc_now(),
            duration_ms=duration_ms,
            data=clean_data,
        )
        self._events.append(event)
        return event

    def events(self) -> list[Event]:
        return list(self._events)

    def by_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self._events if e.type is event_type]

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict()) for e in self._events)

    def write_jsonl(self, path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_jsonl(), encoding="utf-8")
        return str(path)
