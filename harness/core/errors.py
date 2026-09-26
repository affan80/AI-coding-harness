"""Structured errors shared across the harness.

Every error carries a machine-readable payload (``to_dict``) so callers —
the CLI/TUI, the evidence store, the orchestrator's terminal handling — can
persist and render failures without parsing exception strings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class HarnessError(Exception):
    """Base class for all structured harness errors."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this error."""
        payload: dict[str, Any] = {"error": type(self).__name__, "message": self.message}
        payload.update(self.details)
        return payload


class InvalidTransitionError(HarnessError):
    """A session state transition that the state machine does not allow."""

    @classmethod
    def for_states(cls, source: str, target: str, *, detail: str = "") -> InvalidTransitionError:
        message = detail or f"transition {source} -> {target} is not allowed"
        return cls(message, details={"from_state": source, "to_state": target, "detail": detail})


class BudgetExhaustedError(HarnessError):
    """A budget limit was reached; the requested work cannot proceed."""

    @classmethod
    def for_budget(
        cls, kind: str, limit: int, used: int, *, goal_id: str | None = None
    ) -> BudgetExhaustedError:
        scope = f" for goal {goal_id}" if goal_id else ""
        message = f"{kind} budget exhausted: used {used} of {limit}{scope}"
        details: dict[str, Any] = {"budget_kind": kind, "limit": limit, "used": used}
        if goal_id is not None:
            details["goal_id"] = goal_id
        return cls(message, details=details)


class SerializationError(HarnessError):
    """Session data could not be serialized or restored."""

    @classmethod
    def for_field(cls, field_name: str, value: object, *, detail: str = "") -> SerializationError:
        message = detail or f"invalid value for field {field_name!r}: {value!r}"
        return cls(message, details={"field": field_name, "value": repr(value)})


class NotFoundError(HarnessError):
    """A referenced entity (goal, step, ...) does not exist in the session."""

    @classmethod
    def for_id(cls, entity: str, entity_id: str) -> NotFoundError:
        return cls(
            f"unknown {entity} id {entity_id!r}",
            details={"entity": entity, "id": entity_id},
        )
