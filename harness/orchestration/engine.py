"""The integrated orchestrated command path (issue #78; PRD §§24, 28, 30).

Wires every workstream through one engine, without bypassing any contract:

- **core orchestrator**: state transitions, budget consumption, and the
  authoritative session come only from ``Orchestrator``;
- **intent** (#34): the model extracts the goal graph;
- **repository profile** (#4/#8): the bounded inventory profiles the repo;
- **planning** (#48-#50): the model proposes steps, the planner validates
  policy and picks the next runnable step;
- **tools/execution** (#11-#13): the only path that mutates the repository
  (file tools, controlled shell) with checkpoints and evidence;
- **verification** (#14/#62): the deterministic ladder with the diff/scope
  gate;
- **recovery** (#63-#65): classification, loop detection, budget gates,
  and model-proposed repairs through the merged recovery engine;
- **evidence/telemetry** (#2/#17): every event lands in the run directory
  and the final report.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.cli.main import ExitCode
from harness.cli.render import render_final_summary
from harness.context.manager import (
    ContextItem,
    ContextManager,
    Level,
    Priority,
)
from harness.context.manager import (
    ModelCapabilities as CtxCapabilities,
)
from harness.core.budgets import BudgetKind
from harness.core.errors import BudgetExhaustedError
from harness.core.models import (
    Budget,
    GoalStatus,
    StepKind,
    TerminalReason,
    UserRequest,
)
from harness.core.models import (
    Goal as CoreGoal,
)
from harness.core.models import (
    PlanStep as CoreStep,
)
from harness.core.models import (
    StepStatus as CoreStepStatus,
)
from harness.core.orchestrator import Orchestrator
from harness.core.state_machine import OrchestrationState
from harness.execution.executor import ExecutorLoop
from harness.execution.models import EditRequest
from harness.intent.engine import IntentEngine
from harness.model.types import Message, ModelClient, ResponseSchema
from harness.planning.models import (
    ExecutionPlan,
    StepAction,
)
from harness.planning.models import (
    Goal as PlanningGoal,
)
from harness.planning.models import (
    PlanStep as PlanningStep,
)
from harness.planning.models import (
    StepStatus as PlanningStepStatus,
)
from harness.planning.planner import Planner
from harness.recovery.classifier import FailureInput, collect_failure_evidence
from harness.recovery.engine import (
    RecoveryDecision,
    RecoveryRequest,
    evaluate_recovery,
)
from harness.recovery.loops import LoopDetector
from harness.recovery.models import AttemptRecord
from harness.repository.profile import profile_repository
from harness.telemetry.events import EventRecorder, EventType
from harness.telemetry.metrics import UsageMetrics
from harness.telemetry.report import (
    STATUS_FAILED,
    STATUS_VERIFIED,
    CheckResult,
    ReportInput,
    generate_report,
)
from harness.telemetry.store import RunStore
from harness.tools.factory import build_registry
from harness.verification import (
    GoalKind as VerifyGoalKind,
)
from harness.verification import (
    StageOutcome,
    VerificationInput,
    run_verification_ladder,
)
from harness.verification.adapters import select_adapter

_MAX_CONTEXT_TOKENS = 4_000

_PLAN_SCHEMA = ResponseSchema(
    name="execution_plan",
    schema={
        "type": "object",
        "required": ["steps"],
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "action", "target", "goal_id"],
                    "properties": {
                        "id": {"type": "string"},
                        "action": {"type": "string", "enum": [
                            "inspect", "patch", "command", "verify",
                        ]},
                        "target": {"type": "string"},
                        "goal_id": {"type": "string"},
                        "depends_on": {"type": "array",
                                       "items": {"type": "string"}},
                    },
                },
            },
        },
    },
)

_DIFF_SCHEMA = ResponseSchema(
    name="patch_diff",
    schema={
        "type": "object",
        "required": ["diff"],
        "properties": {"diff": {"type": "string"}},
    },
)

_ACTION_TO_KIND: dict[str, StepKind] = {
    "inspect": StepKind.INSPECT,
    "patch": StepKind.EDIT,
    "command": StepKind.SHELL,
    "verify": StepKind.VERIFY,
    "audit": StepKind.VERIFY,
}


@dataclass
class IntegratedResult:
    """Outcome of one orchestrated session."""

    exit_code: int
    status: str
    goals_completed: int
    goals_total: int
    changed_files: list[str]
    checks_passed: int
    checks_total: int
    recovery_attempts: int
    report_path: str
    run_dir: str
    final_lines: list[str] = field(default_factory=list)


class _BudgetedClient:
    """Model-client wrapper enforcing the orchestrator's call budget."""

    def __init__(self, client: ModelClient, orch: Orchestrator,
                 recorder: EventRecorder) -> None:
        self._client = client
        self._orch = orch
        self._recorder = recorder

    async def generate(self, messages, tools=None, response_schema=None):
        if not self._orch.has_budget_headroom(BudgetKind.MODEL_CALLS):
            raise BudgetExhaustedError.for_budget(
                BudgetKind.MODEL_CALLS.value,
                self._orch.session.budget.max_model_calls,
                self._orch.session.usage.model_calls,
            )
        self._orch.consume_model_call()
        response = await self._client.generate(
            messages, tools=tools, response_schema=response_schema
        )
        self._recorder.emit(
            EventType.MODEL_CALL,
            f"model call finished ({response.finish_reason})",
            data={"input_tokens": response.usage.input_tokens,
                  "output_tokens": response.usage.output_tokens},
            duration_ms=response.latency_ms,
        )
        return response


class _StrClient:
    """Adapts the shared client to the recovery layer's str-returning protocol."""

    def __init__(self, client: ModelClient) -> None:
        self._client = client

    async def generate(self, messages: list[dict], *, response_schema=None) -> str:
        converted = [
            Message(role=m["role"], content=m["content"]) for m in messages
        ]
        # a schema must be requested for structured output to come back
        schema = response_schema or ResponseSchema(
            name="recovery_plan", schema={"type": "object"}
        )
        response = await self._client.generate(converted, response_schema=schema)
        if response.structured is not None:
            return json.dumps(response.structured)
        return response.text or ""


class IntegratedEngine:
    """Runs one full orchestrated session from INITIALIZE to COMPLETED."""

    def __init__(
        self,
        request: UserRequest,
        *,
        budget: Budget | None = None,
        client: ModelClient,
        runs_root: str | Path = "runs",
        out: Callable[[str], None] = print,
        enable_audit: bool = False,
    ) -> None:
        self.request = request
        self.budget = budget or Budget()
        self.client = client
        self.runs_root = Path(runs_root)
        self.out = out
        self.enable_audit = enable_audit

        self.repo_root = Path(request.repository_path)
        self.orch = Orchestrator.start(request, budget=self.budget)
        self.store = RunStore.start(request, runs_root=self.runs_root)
        self.recorder = EventRecorder()
        self.budgeted = _BudgetedClient(client, self.orch, self.recorder)

        scope = list(request.scope_paths)
        self.registry = build_registry(self.repo_root, scope)
        self.executor = ExecutorLoop(self.registry)
        self.planner = Planner(allowed_paths=scope)
        self.intent = IntentEngine(self.budgeted)
        self.context = ContextManager(
            CtxCapabilities(max_context_tokens=_MAX_CONTEXT_TOKENS,
                            max_output_tokens=1_000)
        )
        self.loop_detector = LoopDetector()

        self.planning_plan = ExecutionPlan()
        self.step_goal: dict[str, str] = {}
        self.diff_parts: list[str] = []
        self.changed_files: list[str] = []
        self.checks: list[CheckResult] = []
        self.recovery_attempts = 0
        self.failure_history: dict[str, list[dict[str, Any]]] = {}
        self.active_failure: dict[str, Any] | None = None
        self.failure_note = ""
        self._pending_recovery: tuple[str, dict[str, Any], tuple[str, Any]] = (
            "", {}, ("replan", None)
        )

    # -- public entry ---------------------------------------------------------

    async def run(self) -> IntegratedResult:
        """Drive the state machine until a terminal outcome."""
        while self.orch.session.terminal is None:
            if not self.orch.has_budget_headroom(BudgetKind.ITERATIONS):
                return await self._finish_failed(
                    "iteration budget exhausted", TerminalReason.BUDGET_EXHAUSTED
                )
            self.orch.consume_iteration()
            self.store.append_event(
                "state",
                f"entered {self.orch.session.state.value}",
                state=self.orch.session.state.value,
            )
            handler = self._handlers().get(self.orch.session.state)
            if handler is None:
                return await self._finish_failed(
                    f"no handler for {self.orch.session.state.value}",
                    TerminalReason.INTERNAL_ERROR,
                )
            try:
                await handler()
            except BudgetExhaustedError:
                return await self._finish_failed(
                    "model call budget exhausted", TerminalReason.BUDGET_EXHAUSTED
                )
        return self._build_result()

    def _handlers(self) -> dict[OrchestrationState, Callable[[], Any]]:
        return {
            OrchestrationState.INITIALIZE: self._on_initialize,
            OrchestrationState.UNDERSTAND: self._on_understand,
            OrchestrationState.INSPECT_REPOSITORY: self._on_inspect,
            OrchestrationState.BASELINE: self._on_baseline,
            OrchestrationState.PLAN: self._on_plan,
            OrchestrationState.EXECUTE: self._on_execute,
            OrchestrationState.VERIFY: self._on_verify,
            OrchestrationState.DIAGNOSE: self._on_diagnose,
            OrchestrationState.REPLAN: self._on_replan,
            OrchestrationState.AUDIT: self._on_audit,
            OrchestrationState.FINALIZE: self._on_finalize,
        }

    # -- state handlers -------------------------------------------------------

    async def _on_initialize(self) -> None:
        self.orch.transition(OrchestrationState.UNDERSTAND)

    async def _on_understand(self) -> None:
        graph = await self.intent.extract(self.request)
        self.orch.record_goals([
            CoreGoal(
                goal_id=g.goal_id,
                title=g.title,
                description=g.description,
                acceptance_criteria=g.acceptance_criteria,
            )
            for g in graph.goals
        ])
        if not self.orch.session.goals:
            self.orch.fail(TerminalReason.PLAN_FAILED, "intent produced no goals")
            return
        self._event("state", f"goal graph accepted: "
                    f"{[g.goal_id for g in self.orch.session.goals]}")
        self.orch.transition(OrchestrationState.INSPECT_REPOSITORY)

    async def _on_inspect(self) -> None:
        profile = profile_repository(self.repo_root)
        self.store.write_document("repository.json", profile.to_dict())
        self.context.add_item(ContextItem(
            id="l0:repository",
            content=json.dumps(profile.to_dict())[:1_000],
            priority=Priority.P6_METADATA,
            level=Level.L0_METADATA,
            reason="repository profile",
        ))
        self.orch.transition(OrchestrationState.BASELINE)

    async def _on_baseline(self) -> None:
        self._event("state", "baseline recorded (no prior checks configured)")
        self.orch.transition(OrchestrationState.PLAN)

    async def _on_plan(self) -> None:
        pending = [
            g for g in self.orch.session.goals if g.status is GoalStatus.PENDING
        ]
        if not pending:
            self.orch.transition(OrchestrationState.FINALIZE)
            return
        raw_steps = await self._propose_plan(pending)
        self.planning_plan = self._to_planning_plan(pending, raw_steps)
        self.planner.validate_plan(self.planning_plan)
        core_steps = [
            self._to_core_step(step, self.step_goal[step.id])
            for goal in self.planning_plan.goals
            for step in goal.steps
        ]
        self.orch.record_plan(core_steps)
        self._event("state", f"plan accepted with {len(core_steps)} step(s)")
        self.orch.transition(OrchestrationState.EXECUTE)

    async def _on_execute(self) -> None:
        step = self.planner.get_next_runnable_step(self.planning_plan)
        if step is None:
            pending = self._pending_planning_steps()
            if pending:
                await self._enter_diagnose(pending[0], "unreachable pending step")
            else:
                self.orch.transition(OrchestrationState.VERIFY)
            return

        if step.action is StepAction.VERIFY:
            # The deterministic ladder is the verifier; the plan's verify
            # step just marks that the goal is ready for it.
            step.status = PlanningStepStatus.COMPLETED
            self.orch.update_step_status(step.id, CoreStepStatus.COMPLETED)
            self.orch.transition(OrchestrationState.VERIFY)
            return

        core_step = self._to_core_step(step, self.step_goal[step.id])
        if step.action is StepAction.PATCH:
            diff = await self._propose_diff(step)
            outcome = await self.executor.execute(
                core_step, edit=EditRequest(path=step.target, diff=diff)
            )
            self.diff_parts.append(diff)
        else:
            outcome = await self.executor.execute(core_step, command=step.target)

        if outcome.ok:
            step.status = PlanningStepStatus.COMPLETED
            self.orch.update_step_status(
                step.id, CoreStepStatus.COMPLETED
            )
            self.changed_files = sorted(
                set(self.changed_files) | set(outcome.changed_files)
            )
            for ref in outcome.evidence:
                if ref.path:
                    self.store.append_event(
                        "evidence", ref.description, data={"path": ref.path}
                    )
            self._event("tool_call", f"step {step.id} ({step.action.value}) completed")
        else:
            step.status = PlanningStepStatus.FAILED
            self.orch.update_step_status(step.id, CoreStepStatus.FAILED)
            # The machine routes failures through VERIFY; carry the failure
            # so _on_verify skips the ladder and goes straight to diagnosis.
            self._event("failure", f"step {step.id} failed: {outcome.summary}")
            self.active_failure = {
                "goal_id": self.step_goal.get(step.id, ""),
                "step_id": step.id,
                "stage": "",
                "summary": outcome.summary,
                "failure_class": outcome.failure_kind.value.lower(),
                "stage_result": None,
            }
            self.orch.transition(OrchestrationState.VERIFY)

    async def _on_verify(self) -> None:
        if self.active_failure is not None and self.active_failure.get("step_id"):
            # a failed step was routed here: go straight to diagnosis
            failure = self.active_failure
            self.failure_note = f"failed at execution: {failure['summary'][:160]}"
            self._event("failure", self.failure_note)
            self.orch.transition(OrchestrationState.DIAGNOSE,
                                 reason=self.failure_note[:100])
            return
        goal = self._next_pending_goal()
        if goal is None:
            self.orch.transition(OrchestrationState.FINALIZE)
            return
        verify_targets = [
            s.target for s in self._steps_of_goal(goal.goal_id)
            if s.action is StepAction.VERIFY and s.target
        ]
        report = run_verification_ladder(
            VerificationInput(
                goal_id=goal.goal_id,
                goal_kind=VerifyGoalKind.BUG_FIX
                if "fix" in goal.title.lower() else VerifyGoalKind.FEATURE,
                targeted_tests=tuple(verify_targets),
                diff_text="\n".join(self.diff_parts) or None,
                allowed_scope=tuple(self.request.scope_paths),
            ),
            self.repo_root,
            adapter=select_adapter(self.repo_root),
            store=self.store,
        )
        for stage in report.stages:
            self.checks.append(CheckResult(
                name=stage.name.value,
                command=stage.command or stage.name.value,
                passed=stage.outcome is StageOutcome.PASSED,
                output_summary=stage.summary[:200],
                exit_code=stage.exit_code,
            ))
        if report.status.value == "verified":
            self._mark_goal_verified(goal.goal_id)
            self._event("verification", f"goal {goal.goal_id} verified")
            # The machine routes VERIFY -> AUDIT -> FINALIZE.
            self.orch.transition(OrchestrationState.AUDIT)
            return
        first = report.first_failure
        await self._enter_diagnose(
            None,
            first.summary if first else "verification failed",
            goal_id=goal.goal_id,
            stage=first.name.value if first else "",
            stage_result=first,
        )

    async def _on_diagnose(self) -> None:
        failure = self.active_failure
        if failure is None:
            self.orch.transition(OrchestrationState.EXECUTE)
            return
        goal_id = failure["goal_id"]
        self.failure_history.setdefault(goal_id, []).append(dict(failure))

        decision, _usage = await self._evaluate(goal_id)
        if decision.decision in (RecoveryDecision.RETRY, RecoveryDecision.REPLAN):
            self.orch.consume_retry(goal_id)  # budget through the orchestrator
        self.recovery_attempts += 1
        self._event("retry", f"decision {decision.decision.value}: "
                    f"{decision.reason}")

        if decision.decision is RecoveryDecision.STOP:
            self.orch.fail(
                TerminalReason.VERIFICATION_FAILED,
                f"recovery stopped: {decision.reason}",
            )
            return
        pending_action = None
        if decision.decision is RecoveryDecision.ROLLBACK:
            pending_action = ("rollback", None)
        elif decision.decision is RecoveryDecision.RETRY and decision.plan:
            pending_action = ("repair", decision.plan)
        elif decision.decision is RecoveryDecision.RETRY:
            pending_action = ("requeue", None)
        else:  # REPLAN without a plan: bounded replacement for the goal
            pending_action = ("replan", None)
        self._pending_recovery = (goal_id, failure, pending_action)
        self.orch.transition(OrchestrationState.REPLAN)

    async def _on_replan(self) -> None:
        goal_id, failure, action = self._pending_recovery
        kind, plan = action
        if kind == "rollback":
            rolled = self._rollback_last_patch()
            self._event("retry", f"rollback applied: {rolled}")
        elif kind == "repair" and plan is not None:
            await self._apply_repair(goal_id, plan)
        elif kind == "requeue":
            failed_id = failure.get("step_id")
            if failed_id:
                step = self.planning_plan.get_step(failed_id)
                if step is not None:
                    step.status = PlanningStepStatus.PENDING
        else:  # replan
            await self._replan_goal(goal_id, failure)
        self.active_failure = None
        self.orch.transition(OrchestrationState.EXECUTE)

    async def _on_audit(self) -> None:
        self.orch.consume_audit_round()
        self._event("audit", "audit passed through in the integrated path")
        self.orch.transition(OrchestrationState.FINALIZE)

    async def _on_finalize(self) -> None:
        goals = self.orch.session.goals
        if goals and all(g.status is GoalStatus.COMPLETED for g in goals):
            self.orch.complete()
        else:
            self.orch.fail(
                TerminalReason.VERIFICATION_FAILED,
                "not all goals reached verified",
            )

    # -- diagnosis / recovery -------------------------------------------------

    async def _enter_diagnose(
        self,
        step: PlanningStep | None,
        summary: str,
        *,
        goal_id: str | None = None,
        stage: str = "",
        stage_result=None,
        failure_class: str = "tool",
    ) -> None:
        resolved_goal = goal_id if goal_id is not None else (
            self.step_goal.get(step.id, "") if step is not None else ""
        )
        self.active_failure = {
            "goal_id": resolved_goal,
            "step_id": step.id if step is not None else "",
            "stage": stage,
            "summary": summary,
            "failure_class": failure_class,
            "stage_result": stage_result,
        }
        self.failure_note = f"failed at {stage or 'execution'}: {summary[:160]}"
        self._event("failure", self.failure_note)
        self.orch.transition(
            OrchestrationState.DIAGNOSE, reason=self.failure_note[:100]
        )

    async def _evaluate(self, goal_id: str):
        failure = self.active_failure or {}
        stage_result = failure.get("stage_result")
        failure_input = FailureInput(
            goal_id=goal_id,
            stage=stage_result.name if stage_result is not None else None,
            patch_failed=failure.get("failure_class") in ("patch", "PATCH"),
            latest_diff="\n".join(self.diff_parts) or None,
        )
        evidence = collect_failure_evidence(failure_input)
        plan = None
        try:
            from harness.recovery.planner import propose_recovery_plan

            plan = await propose_recovery_plan(
                evidence, _StrClient(self.client),
                tuple(self.request.scope_paths),
            )
        except Exception as exc:  # noqa: BLE001 - recovery continues without plan
            self._event("retry", f"recovery plan unavailable: {exc}")

        request = RecoveryRequest(
            goal_id=goal_id,
            failure_class=evidence.failure_class,
            fingerprint=(
                f"{failure.get('step_id', '')}:{failure.get('stage', '')}:"
                f"{plan.repair_summary if plan else failure.get('summary', '')}"
            ),
            plan=plan,
            prior_attempts=self._attempt_records(goal_id),
        )
        usage = self.orch.session.usage
        return evaluate_recovery(
            request, self.orch.session.budget, usage, self.loop_detector
        )

    def _attempt_records(self, goal_id: str) -> tuple[AttemptRecord, ...]:
        return tuple(
            AttemptRecord(
                attempt=index + 1,
                action=record.get("summary", "attempt")[:120],
                fingerprint=(
                    f"{record.get('step_id', '')}:{record.get('stage', '')}"
                ),
                outcome=f"failed: {record.get('stage', 'step')}",
            )
            for index, record in enumerate(
                self.failure_history.get(goal_id, [])
            )
        )

    async def _apply_repair(self, goal_id: str, plan) -> None:
        for action in plan.repair_actions:
            if action.kind != "patch":
                continue
            diff = await self._propose_diff_for_repair(action)
            core_step = CoreStep(
                step_id=f"repair-{self.recovery_attempts}-{Path(action.target).stem}",
                goal_id=goal_id, title="recovery patch",
                kind=StepKind.EDIT, detail=action.target,
            )
            outcome = await self.executor.execute(
                core_step, edit=EditRequest(path=action.target, diff=diff)
            )
            if outcome.ok:
                self.diff_parts.append(diff)
                self.changed_files = sorted(
                    set(self.changed_files) | set(outcome.changed_files)
                )
        self._event("retry", f"repair applied: {plan.repair_summary}")

    async def _replan_goal(self, goal_id: str, failure: dict[str, Any]) -> None:
        try:
            pending = [
                g for g in self.orch.session.goals
                if g.goal_id == goal_id and g.status is GoalStatus.PENDING
            ]
            raw_steps = await self._propose_plan(
                pending or list(self.orch.session.goals)
            )
        except Exception as exc:  # noqa: BLE001 - dry script degrades to retry
            self._event("state", f"replan proposal unavailable: {exc}")
            raw_steps = []
        replacement = [
            PlanningStep(
                id=raw["id"],
                action=StepAction(raw["action"]),
                target=raw["target"],
                expected_evidence="evidence",
                completion_criteria=raw.get("completion_criteria", ""),
                dependencies=list(raw.get("depends_on", [])),
            )
            for raw in raw_steps
        ]
        failed_id = failure.get("step_id") or None
        self.planning_plan = self.planner.replan(
            self.planning_plan, goal_id, replacement,
            failed_step_id=failed_id,
            failure_evidence=failure.get("summary", ""),
        )
        self._event("state", f"goal {goal_id} replanned with "
                    f"{len(replacement)} replacement step(s)")

    async def _propose_diff_for_repair(self, action) -> str:
        payload = await self.budgeted.generate(
            [Message(role="system", content=(
                "Produce a corrected minimal unified diff (git format). "
                'Return {"diff": "..."} only.'
            )),
            Message(role="user", content=json.dumps({
                "file": action.target,
                "repair": action.detail,
                "failure": self.active_failure.get("summary", "")
                if self.active_failure else "",
            }))],
            response_schema=_DIFF_SCHEMA,
        )
        return (payload.structured or {}).get("diff", "")

    # -- small bridges ---------------------------------------------------------

    def _event(self, kind: str, message: str, **data: Any) -> None:
        self.recorder.emit(EventType(kind), message, data=data or None)
        self.store.append_event(kind, message, data=dict(data or {}))

    async def _finish_failed(
        self, detail: str, reason: TerminalReason = TerminalReason.VERIFICATION_FAILED
    ) -> IntegratedResult:
        self.orch.fail(reason, detail)
        return self._build_result()

    def _pending_planning_steps(self) -> list[PlanningStep]:
        return [
            s for g in self.planning_plan.goals for s in g.steps
            if s.status is PlanningStepStatus.PENDING
        ]

    def _steps_of_goal(self, goal_id: str) -> list[PlanningStep]:
        for goal in self.planning_plan.goals:
            if goal.id == goal_id:
                return goal.steps
        return []

    def _planning_step_for_goal(self, goal_id: str) -> PlanningStep | None:
        steps = self._steps_of_goal(goal_id)
        return steps[0] if steps else None

    def _next_pending_goal(self):
        for goal in self.orch.session.goals:
            if goal.status is GoalStatus.PENDING:
                return goal
        return None

    def _mark_goal_verified(self, goal_id: str) -> None:
        for step in self._steps_of_goal(goal_id):
            if step.status is PlanningStepStatus.PENDING:
                step.status = PlanningStepStatus.COMPLETED
        self.orch.update_goal_status(goal_id, GoalStatus.COMPLETED)

    def _to_planning_plan(
        self, goals: list[CoreGoal], raw_steps: list[dict[str, Any]]
    ) -> ExecutionPlan:
        by_goal: dict[str, list[PlanningStep]] = {}
        for raw in raw_steps:
            step = PlanningStep(
                id=raw["id"],
                action=StepAction(raw["action"]),
                target=raw["target"],
                expected_evidence="evidence",
                completion_criteria=raw.get(
                    "completion_criteria", f"{raw['action']} {raw['target']}"
                ),
                dependencies=list(raw.get("depends_on", [])),
            )
            goal_id = raw["goal_id"]
            by_goal.setdefault(goal_id, []).append(step)
            self.step_goal[step.id] = goal_id
        return ExecutionPlan(goals=[
            PlanningGoal(
                id=goal.goal_id, description=goal.title,
                steps=by_goal.get(goal.goal_id, []),
            )
            for goal in goals
        ])

    def _to_core_step(self, step: PlanningStep, goal_id: str) -> CoreStep:
        return CoreStep(
            step_id=step.id,
            goal_id=goal_id,
            title=step.completion_criteria[:60],
            kind=_ACTION_TO_KIND[step.action.value],
            detail=step.target,
        )

    async def _propose_plan(self, goals: list[CoreGoal]) -> list[dict[str, Any]]:
        response = await self.budgeted.generate(
            [Message(role="system", content=(
                'Propose a minimal execution plan as JSON: {"steps": [{"id", '
                '"action" (inspect|patch|command|verify), "target", "goal_id", '
                '"depends_on"}]}. Order dependencies first; every goal needs '
                "a verify step; targets must stay inside the allowed scope."
            )),
            Message(role="user", content=json.dumps({
                "goals": [
                    {"goal_id": g.goal_id, "title": g.title,
                     "criteria": list(g.acceptance_criteria)}
                    for g in goals
                ],
                "allowed_scope": list(self.request.scope_paths) or "entire repo",
            }))],
            response_schema=_PLAN_SCHEMA,
        )
        return (response.structured or {}).get("steps", [])

    async def _propose_diff(self, step: PlanningStep) -> str:
        response = await self.budgeted.generate(
            [Message(role="system", content=(
                "Produce a minimal unified diff (git format) for the file. "
                'Return {"diff": "..."} only.'
            )),
            Message(role="user", content=json.dumps({
                "file": step.target,
                "goal": step.completion_criteria,
                "repository_path": str(self.repo_root),
            }))],
            response_schema=_DIFF_SCHEMA,
        )
        return (response.structured or {}).get("diff", "")

    # -- finalization -----------------------------------------------------------

    def _build_result(self) -> IntegratedResult:
        session = self.orch.session
        goals = session.goals
        completed = sum(1 for g in goals if g.status is GoalStatus.COMPLETED)
        from harness.core.models import TerminalOutcome

        verified = (
            session.terminal is not None
            and session.terminal.outcome is TerminalOutcome.COMPLETED
        )
        status = STATUS_VERIFIED if verified else STATUS_FAILED
        report = generate_report(ReportInput(
            session_id=session.session_id,
            objective=session.request.objective,
            status=status,
            detail=session.terminal.reason.value if session.terminal else "",
            goals=[
                {"id": g.goal_id, "title": g.title, "status": g.status.value,
                 "acceptance_criteria": list(g.acceptance_criteria)}
                for g in goals
            ],
            changed_files=self.changed_files,
            checks=self.checks,
            limitations=([self.failure_note] if self.failure_note else []),
            metrics=UsageMetrics.from_events(self.recorder.events()),
            events=self.recorder.events(),
            evidence=[{"ref_id": "ev-run", "kind": "artifact",
                       "description": "run directory",
                       "path": str(self.store.run_dir)}],
        ))
        report_path = self.store.run_dir / "final-report.md"
        report_path.write_text(report, encoding="utf-8")
        self.store.write_document(
            "metrics.json",
            UsageMetrics.from_events(self.recorder.events()).to_dict(),
        )
        from harness.core.models import SessionStatus

        self.store.finalize(
            SessionStatus.VERIFIED if status == STATUS_VERIFIED
            else SessionStatus.FAILED,
            stop_reason=session.terminal.reason.value if session.terminal else "",
        )

        final_lines = render_final_summary(
            status=status, goals_completed=completed,
            goals_total=len(goals), changed_files=self.changed_files,
            checks_passed=sum(1 for c in self.checks if c.passed),
            checks_total=len(self.checks), retries=self.recovery_attempts,
            report_path=str(report_path),
        )
        for line in final_lines:
            self.out(line)

        exit_code = (
            ExitCode.VERIFIED.value if verified
            else ExitCode.PARTIAL.value if completed else ExitCode.FAILED.value
        )
        return IntegratedResult(
            exit_code=exit_code, status=status, goals_completed=completed,
            goals_total=len(goals), changed_files=list(self.changed_files),
            checks_passed=sum(1 for c in self.checks if c.passed),
            checks_total=len(self.checks),
            recovery_attempts=self.recovery_attempts,
            report_path=str(report_path), run_dir=str(self.store.run_dir),
            final_lines=final_lines,
        )


async def run_orchestrated(
    request: UserRequest,
    *,
    client: ModelClient,
    budget: Budget | None = None,
    runs_root: str | Path = "runs",
    out: Callable[[str], None] = print,
    enable_audit: bool = False,
) -> IntegratedResult:
    """One command, end to end (issue #78)."""
    engine = IntegratedEngine(
        request, budget=budget, client=client, runs_root=runs_root, out=out,
        enable_audit=enable_audit,
    )
    return await engine.run()
