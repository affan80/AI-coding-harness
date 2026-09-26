"""Core session models, budgets, and the orchestrator state machine.

This package is Person A's shared-contract surface (``docs/TEAM_PLAN.md``):
other workstreams import the models and the orchestrator from here and never
reach into private internals.
"""

from harness.core.budgets import BudgetKind, consume, has_headroom, limit_for, used_for
from harness.core.errors import (
    BudgetExhaustedError,
    HarnessError,
    InvalidTransitionError,
    NotFoundError,
    SerializationError,
)
from harness.core.models import (
    SCHEMA_VERSION,
    Budget,
    BudgetUsage,
    CheckpointPolicy,
    EvidenceRef,
    Goal,
    GoalStatus,
    OrchestrationState,
    PlanStep,
    Session,
    SessionStatus,
    StepKind,
    StepStatus,
    TerminalInfo,
    TerminalOutcome,
    TerminalReason,
    TransitionRecord,
    UserRequest,
    VerificationDepth,
    WritePolicy,
    new_session_id,
)
from harness.core.orchestrator import Orchestrator
from harness.core.state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    assert_valid_transition,
    can_transition,
    is_terminal,
)

__all__ = [
    "SCHEMA_VERSION",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "Budget",
    "BudgetExhaustedError",
    "BudgetKind",
    "BudgetUsage",
    "CheckpointPolicy",
    "EvidenceRef",
    "Goal",
    "GoalStatus",
    "HarnessError",
    "InvalidTransitionError",
    "NotFoundError",
    "OrchestrationState",
    "Orchestrator",
    "PlanStep",
    "SerializationError",
    "Session",
    "SessionStatus",
    "StepKind",
    "StepStatus",
    "TerminalInfo",
    "TerminalOutcome",
    "TerminalReason",
    "TransitionRecord",
    "UserRequest",
    "VerificationDepth",
    "WritePolicy",
    "assert_valid_transition",
    "can_transition",
    "consume",
    "has_headroom",
    "is_terminal",
    "limit_for",
    "new_session_id",
    "used_for",
]
