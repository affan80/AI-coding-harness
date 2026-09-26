from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StepAction(Enum):
    INSPECT = "inspect"
    PATCH = "patch"
    COMMAND = "command"
    VERIFY = "verify"
    AUDIT = "audit"
    FINALIZE = "finalize"

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class PlanStep:
    id: str
    action: StepAction
    target: str
    expected_evidence: str
    completion_criteria: str
    dependencies: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING

@dataclass
class Goal:
    id: str
    description: str
    steps: list[PlanStep] = field(default_factory=list)

    def is_verified(self) -> bool:
        """Returns True if there is a completed verification step."""
        verify_steps = [s for s in self.steps if s.action in (StepAction.VERIFY, StepAction.AUDIT)]
        return any(s.status == StepStatus.COMPLETED for s in verify_steps)

@dataclass
class ExecutionPlan:
    goals: list[Goal] = field(default_factory=list)

    def get_step(self, step_id: str) -> PlanStep | None:
        for goal in self.goals:
            for step in goal.steps:
                if step.id == step_id:
                    return step
        return None


# ---------------------------------------------------------------------------
# Serialization and structural validation (issue #48)
# ---------------------------------------------------------------------------


class StepSchemaError(ValueError):
    """A plan document violated the step schema."""


def _require_text(mapping: dict, key: str, model: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StepSchemaError(f"{model} field {key!r} must be a non-empty string")
    return value


def plan_to_dict(plan: ExecutionPlan) -> dict:
    """Serialize the whole plan (goals -> steps) for the run directory."""
    return {
        "goals": [
            {
                "id": goal.id,
                "description": goal.description,
                "steps": [_step_to_dict(step) for step in goal.steps],
            }
            for goal in plan.goals
        ],
    }


def plan_from_dict(data: dict) -> ExecutionPlan:
    """Rebuild a plan from ``plan_to_dict`` output, validating every step."""
    if not isinstance(data, dict) or not isinstance(data.get("goals"), list):
        raise StepSchemaError("plan document must contain a 'goals' list")
    plan = ExecutionPlan()
    for goal_data in data["goals"]:
        goal = Goal(
            id=_require_text(goal_data, "id", "Goal"),
            description=goal_data.get("description", ""),
        )
        for step_data in goal_data.get("steps", []):
            goal.steps.append(_step_from_dict(step_data))
        plan.goals.append(goal)
    return plan


def _step_to_dict(step: PlanStep) -> dict:
    return {
        "id": step.id,
        "action": step.action.value,
        "target": step.target,
        "expected_evidence": step.expected_evidence,
        "completion_criteria": step.completion_criteria,
        "dependencies": list(step.dependencies),
        "status": step.status.value,
    }


def _step_from_dict(data: dict) -> PlanStep:
    action_raw = _require_text(data, "action", "PlanStep")
    try:
        action = StepAction(action_raw)
    except ValueError:
        raise StepSchemaError(
            f"unknown step action {action_raw!r}; "
            f"known: {[a.value for a in StepAction]}"
        ) from None
    status_raw = data.get("status", StepStatus.PENDING.value)
    try:
        status = StepStatus(status_raw)
    except ValueError:
        raise StepSchemaError(f"unknown step status {status_raw!r}") from None
    step = PlanStep(
        id=_require_text(data, "id", "PlanStep"),
        action=action,
        target=_require_text(data, "target", "PlanStep"),
        expected_evidence=data.get("expected_evidence", ""),
        completion_criteria=data.get("completion_criteria", ""),
        dependencies=list(data.get("dependencies", [])),
        status=status,
    )
    _validate_step(step)
    return step


def _validate_step(step: PlanStep) -> None:
    """Write steps must name an allowed target; inspect/verify need one too."""
    if step.action in (StepAction.PATCH, StepAction.COMMAND):
        if ".." in Path(step.target).parts:
            raise StepSchemaError(
                f"step {step.id} target {step.target!r} contains path traversal"
            )
    if step.action == StepAction.PATCH and not step.completion_criteria:
        raise StepSchemaError(
            f"write step {step.id} must state completion criteria"
        )

