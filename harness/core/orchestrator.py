"""The orchestrator: the only writer of authoritative session state.

Every component reads the frozen :class:`~harness.core.models.Session`;
only this class produces the session's next version. It

* validates every state change against the transition table and rejects
  invalid moves with a structured error, leaving the session untouched;
* records an append-only :class:`~harness.core.models.TransitionRecord`
  for each accepted change;
* enforces budgets — a consumer that hits its limit terminates the session
  as FAILED with a structured terminal reason and a budget-snapshot
  evidence ref, per PRD §20;
* persists *why* a session stopped via
  :class:`~harness.core.models.TerminalInfo`, and serializes/restores the
  whole session so a run can be paused and resumed.

Budget consumers (``consume_model_call`` and friends) are invoked by the
orchestrator on behalf of the active stage; stages never touch session
state or counters directly.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from harness.core import budgets as budget_math
from harness.core.budgets import BudgetKind
from harness.core.errors import BudgetExhaustedError, InvalidTransitionError, NotFoundError
from harness.core.models import (
    Budget,
    EvidenceRef,
    Goal,
    GoalStatus,
    OrchestrationState,
    PlanStep,
    Session,
    StepStatus,
    TerminalInfo,
    TerminalOutcome,
    TerminalReason,
    TransitionRecord,
    UserRequest,
)
from harness.core.state_machine import assert_valid_transition, is_terminal

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Orchestrator:
    """Drives one session through the state machine."""

    def __init__(self, session: Session, *, clock: Clock | None = None) -> None:
        self._session = session
        self._clock: Clock = clock or _utc_now

    # -- construction -------------------------------------------------------

    @classmethod
    def start(
        cls,
        request: UserRequest,
        *,
        budget: Budget | None = None,
        session_id: str | None = None,
        clock: Clock | None = None,
    ) -> Orchestrator:
        """Create a new session in INITIALIZE and wrap it in an orchestrator."""
        session = Session.create(
            request,
            budget=budget,
            session_id=session_id,
            at=clock().isoformat() if clock else None,
        )
        return cls(session, clock=clock)

    @classmethod
    def restore(cls, data: Mapping[str, Any], *, clock: Clock | None = None) -> Orchestrator:
        """Rebuild an orchestrator (and its session) from serialized state."""
        return cls(Session.from_dict(data), clock=clock)

    # -- reading ------------------------------------------------------------

    @property
    def session(self) -> Session:
        """The current authoritative session (read-only value object)."""
        return self._session

    def has_budget_headroom(self, kind: BudgetKind, goal_id: str | None = None) -> bool:
        """Return True if one more unit of ``kind`` would still be allowed."""
        return budget_math.has_headroom(self._session.budget, self._session.usage, kind, goal_id)

    # -- state transitions --------------------------------------------------

    def transition(
        self,
        target: OrchestrationState,
        *,
        reason: str = "",
        evidence: Sequence[EvidenceRef] = (),
    ) -> Session:
        """Move the session to ``target`` along a legal edge.

        Terminal states are reached only through :meth:`complete`,
        :meth:`fail` and :meth:`cancel`, which attach the structured
        terminal reason. On an illegal edge the session is left unchanged
        and :class:`InvalidTransitionError` is raised.
        """
        self._ensure_active()
        if is_terminal(target):
            raise InvalidTransitionError.for_states(
                self._session.state.value,
                target.value,
                detail="terminal states are reached via complete()/fail()/cancel()",
            )
        try:
            assert_valid_transition(self._session.state, target)
        except InvalidTransitionError as exc:
            exc.details["session_id"] = self._session.session_id
            raise

        return self._record(target, reason=reason, evidence=evidence)

    def complete(
        self,
        *,
        reason: TerminalReason = TerminalReason.ALL_GOALS_VERIFIED,
        detail: str = "",
        evidence: Sequence[EvidenceRef] = (),
    ) -> Session:
        """Finish the session successfully from FINALIZE."""
        self._ensure_active()
        try:
            assert_valid_transition(self._session.state, OrchestrationState.COMPLETED)
        except InvalidTransitionError as exc:
            exc.details["session_id"] = self._session.session_id
            raise
        return self._to_terminal(
            TerminalOutcome.COMPLETED,
            reason,
            detail,
            evidence,
            record_reason=detail or reason.value,
        )

    def fail(
        self,
        reason: TerminalReason,
        detail: str,
        evidence: Sequence[EvidenceRef] = (),
    ) -> Session:
        """Terminate the session as FAILED from any active state."""
        self._ensure_active()
        return self._to_terminal(
            TerminalOutcome.FAILED, reason, detail, evidence, record_reason=detail
        )

    def cancel(self, detail: str = "", evidence: Sequence[EvidenceRef] = ()) -> Session:
        """Terminate the session as CANCELLED from any active state."""
        self._ensure_active()
        return self._to_terminal(
            TerminalOutcome.CANCELLED,
            TerminalReason.USER_CANCELLED,
            detail,
            evidence,
            record_reason=detail or TerminalReason.USER_CANCELLED.value,
        )

    # -- budget consumers ---------------------------------------------------

    def consume_model_call(self) -> Session:
        """Charge one model call; exhaust the budget -> session fails."""
        return self._consume(BudgetKind.MODEL_CALLS)

    def consume_iteration(self) -> Session:
        """Charge one EXECUTE round; exhaust the budget -> session fails."""
        return self._consume(BudgetKind.ITERATIONS)

    def consume_audit_round(self) -> Session:
        """Charge one audit round; exhaust the budget -> session fails."""
        return self._consume(BudgetKind.AUDIT_ROUNDS)

    def consume_retry(self, goal_id: str) -> Session:
        """Charge one retry of ``goal_id``; exhaust the budget -> session fails."""
        return self._consume(BudgetKind.RETRIES_PER_GOAL, goal_id=goal_id)

    # -- session content ----------------------------------------------------

    def record_goals(self, goals: Sequence[Goal]) -> Session:
        """Replace the goal graph (UNDERSTAND/PLAN output)."""
        self._ensure_active()
        ids = [goal.goal_id for goal in goals]
        if len(ids) != len(set(ids)):
            raise ValueError("record_goals received duplicate goal_id values")
        return self._replace(goals=tuple(goals))

    def record_plan(self, steps: Sequence[PlanStep]) -> Session:
        """Replace the current execution plan (PLAN/REPLAN output)."""
        self._ensure_active()
        ids = [step.step_id for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("record_plan received duplicate step_id values")
        return self._replace(steps=tuple(steps))

    def update_goal_status(self, goal_id: str, status: GoalStatus) -> Session:
        """Set one goal's status."""
        self._ensure_active()
        if not any(goal.goal_id == goal_id for goal in self._session.goals):
            raise NotFoundError.for_id("goal", goal_id)
        goals = tuple(
            dataclasses.replace(goal, status=status) if goal.goal_id == goal_id else goal
            for goal in self._session.goals
        )
        return self._replace(goals=goals)

    def update_step_status(self, step_id: str, status: StepStatus) -> Session:
        """Set one plan step's status."""
        self._ensure_active()
        if not any(step.step_id == step_id for step in self._session.steps):
            raise NotFoundError.for_id("step", step_id)
        steps = tuple(
            dataclasses.replace(step, status=status) if step.step_id == step_id else step
            for step in self._session.steps
        )
        return self._replace(steps=steps)

    # -- internals ----------------------------------------------------------

    def _consume(self, kind: BudgetKind, goal_id: str | None = None) -> Session:
        self._ensure_active()
        try:
            next_usage = budget_math.consume(
                self._session.budget, self._session.usage, kind, goal_id
            )
        except BudgetExhaustedError as exc:
            self._to_terminal(
                TerminalOutcome.FAILED,
                TerminalReason.BUDGET_EXHAUSTED,
                exc.message,
                evidence=(self._budget_evidence(exc),),
                record_reason=exc.message,
            )
            raise
        return self._replace(usage=next_usage)

    def _budget_evidence(self, exc: BudgetExhaustedError) -> EvidenceRef:
        return EvidenceRef(
            kind="budget_snapshot",
            description=exc.message,
            metadata=dict(exc.details),
        )

    def _to_terminal(
        self,
        outcome: TerminalOutcome,
        reason: TerminalReason,
        detail: str,
        evidence: Sequence[EvidenceRef],
        *,
        record_reason: str,
    ) -> Session:
        """Attach terminal info and move to the outcome state.

        Reachable from any active state: unrecoverable failure and
        cancellation are orchestrator policy, not pipeline edges (see
        ``state_machine.TRANSITIONS``). Callers must ensure the session is
        still active.
        """
        target = _outcome_state(outcome)
        self._session = self._session_with(
            state=target,
            terminal=TerminalInfo(
                outcome=outcome,
                reason=reason,
                detail=detail,
                evidence=tuple(evidence),
            ),
            record=TransitionRecord(
                seq=len(self._session.history) + 1,
                from_state=self._session.state,
                to_state=target,
                at=self._clock().isoformat(),
                reason=record_reason,
                evidence=tuple(evidence),
            ),
        )
        return self._session

    def _record(
        self,
        target: OrchestrationState,
        *,
        reason: str,
        evidence: Sequence[EvidenceRef],
    ) -> Session:
        self._session = self._session_with(
            state=target,
            record=TransitionRecord(
                seq=len(self._session.history) + 1,
                from_state=self._session.state,
                to_state=target,
                at=self._clock().isoformat(),
                reason=reason,
                evidence=tuple(evidence),
            ),
        )
        return self._session

    def _session_with(
        self, *, state: OrchestrationState, record: TransitionRecord, **extra: Any
    ) -> Session:
        return dataclasses.replace(
            self._session,
            state=state,
            updated_at=record.at,
            history=(*self._session.history, record),
            **extra,
        )

    def _replace(self, **changes: Any) -> Session:
        self._session = dataclasses.replace(
            self._session, updated_at=self._clock().isoformat(), **changes
        )
        return self._session

    def _ensure_active(self) -> None:
        if is_terminal(self._session.state):
            raise InvalidTransitionError.for_states(
                self._session.state.value,
                self._session.state.value,
                detail=(
                    f"session {self._session.session_id} is terminal; no further changes allowed"
                ),
            )


def _outcome_state(outcome: TerminalOutcome) -> OrchestrationState:
    return OrchestrationState[outcome.name]
