"""The orchestrator state machine (PRD §15).

The transition table is plain data: adding a state or edge later means
editing one dict, not control flow. The orchestrator consults
:func:`assert_valid_transition` before every state change, so an invalid
move is rejected with a structured :class:`InvalidTransitionError` and the
session is left untouched.
"""

from __future__ import annotations

from collections.abc import Mapping

from harness.core.errors import InvalidTransitionError
from harness.core.models import OrchestrationState

TERMINAL_STATES: frozenset[OrchestrationState] = frozenset(
    {
        OrchestrationState.COMPLETED,
        OrchestrationState.FAILED,
        OrchestrationState.CANCELLED,
    }
)

#: Allowed edges. The failure path VERIFY -> DIAGNOSE -> REPLAN -> EXECUTE
#: implements the PRD recovery loop; AUDIT -> PLAN implements "more
#: confirmed work?". Unrecoverable failure and cancellation are not table
#: edges — they are orchestrator operations valid from any active state.
TRANSITIONS: Mapping[OrchestrationState, frozenset[OrchestrationState]] = {
    OrchestrationState.INITIALIZE: frozenset({OrchestrationState.UNDERSTAND}),
    OrchestrationState.UNDERSTAND: frozenset({OrchestrationState.INSPECT_REPOSITORY}),
    OrchestrationState.INSPECT_REPOSITORY: frozenset({OrchestrationState.BASELINE}),
    OrchestrationState.BASELINE: frozenset({OrchestrationState.PLAN}),
    OrchestrationState.PLAN: frozenset({OrchestrationState.EXECUTE}),
    OrchestrationState.EXECUTE: frozenset({OrchestrationState.VERIFY}),
    OrchestrationState.VERIFY: frozenset({OrchestrationState.AUDIT, OrchestrationState.DIAGNOSE}),
    OrchestrationState.DIAGNOSE: frozenset({OrchestrationState.REPLAN}),
    OrchestrationState.REPLAN: frozenset({OrchestrationState.EXECUTE}),
    OrchestrationState.AUDIT: frozenset({OrchestrationState.PLAN, OrchestrationState.FINALIZE}),
    OrchestrationState.FINALIZE: frozenset({OrchestrationState.COMPLETED}),
    OrchestrationState.COMPLETED: frozenset(),
    OrchestrationState.FAILED: frozenset(),
    OrchestrationState.CANCELLED: frozenset(),
}


def is_terminal(state: OrchestrationState) -> bool:
    """Return True if ``state`` ends the session."""
    return state in TERMINAL_STATES


def can_transition(source: OrchestrationState, target: OrchestrationState) -> bool:
    """Return True if the machine allows ``source -> target``."""
    return target in TRANSITIONS.get(source, frozenset())


def assert_valid_transition(source: OrchestrationState, target: OrchestrationState) -> None:
    """Raise :class:`InvalidTransitionError` unless ``source -> target`` is legal."""
    if not can_transition(source, target):
        raise InvalidTransitionError.for_states(source.value, target.value)
