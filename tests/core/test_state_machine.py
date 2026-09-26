"""Unit checks for the orchestrator state machine (issue #22)."""

from __future__ import annotations

import pytest

from harness.core.errors import InvalidTransitionError
from harness.core.models import OrchestrationState
from harness.core.state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    assert_valid_transition,
    can_transition,
    is_terminal,
)

S = OrchestrationState

#: The PRD §15 happy path, INITIALIZE through COMPLETED.
HAPPY_PATH = [
    S.INITIALIZE,
    S.UNDERSTAND,
    S.INSPECT_REPOSITORY,
    S.BASELINE,
    S.PLAN,
    S.EXECUTE,
    S.VERIFY,
    S.AUDIT,
    S.FINALIZE,
    S.COMPLETED,
]


class TestTransitionTable:
    def test_table_covers_every_state(self):
        assert set(TRANSITIONS) == set(OrchestrationState)

    def test_terminal_states_have_no_outgoing_edges(self):
        for terminal in TERMINAL_STATES:
            assert TRANSITIONS[terminal] == frozenset()

    def test_happy_path_edges_are_legal(self):
        edges = [(HAPPY_PATH[i], HAPPY_PATH[i + 1]) for i in range(len(HAPPY_PATH) - 1)]
        for source, target in edges:
            assert can_transition(source, target), f"{source} -> {target} must be legal"

    def test_recovery_loop_edges_are_legal(self):
        # PRD §15: VERIFY --failure--> DIAGNOSE -> REPLAN -> EXECUTE
        recovery_edges = [(S.VERIFY, S.DIAGNOSE), (S.DIAGNOSE, S.REPLAN), (S.REPLAN, S.EXECUTE)]
        for source, target in recovery_edges:
            assert can_transition(source, target), f"{source} -> {target} must be legal"

    def test_audit_loop_edge_is_legal(self):
        # PRD §15: AUDIT --more confirmed work--> PLAN
        assert can_transition(S.AUDIT, S.PLAN)
        assert can_transition(S.AUDIT, S.FINALIZE)


class TestInvalidTransitions:
    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (S.INITIALIZE, S.EXECUTE),  # skipping the whole pipeline
            (S.PLAN, S.VERIFY),  # skipping EXECUTE
            (S.EXECUTE, S.FINALIZE),  # skipping VERIFY
            (S.VERIFY, S.FINALIZE),  # success must pass through AUDIT
            (S.DIAGNOSE, S.PLAN),  # diagnosis must go through REPLAN
            (S.UNDERSTAND, S.INITIALIZE),  # no backwards edges
            (S.COMPLETED, S.PLAN),  # terminal is absorbing
            (S.FAILED, S.REPLAN),
        ],
    )
    def test_rejected_with_structured_error(self, source, target):
        assert not can_transition(source, target)
        with pytest.raises(InvalidTransitionError) as excinfo:
            assert_valid_transition(source, target)
        payload = excinfo.value.to_dict()
        assert payload["error"] == "InvalidTransitionError"
        assert payload["from_state"] == source.value
        assert payload["to_state"] == target.value


class TestTerminality:
    def test_terminal_states_are_recognized(self):
        assert {S.COMPLETED, S.FAILED, S.CANCELLED} == set(TERMINAL_STATES)
        assert is_terminal(S.COMPLETED)
        assert is_terminal(S.FAILED)
        assert is_terminal(S.CANCELLED)
        assert not is_terminal(S.EXECUTE)
