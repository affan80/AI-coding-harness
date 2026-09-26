from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Set

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
    dependencies: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING

@dataclass
class Goal:
    id: str
    description: str
    steps: List[PlanStep] = field(default_factory=list)
    
    def is_verified(self) -> bool:
        """Returns True if there is a completed verification step."""
        verify_steps = [s for s in self.steps if s.action in (StepAction.VERIFY, StepAction.AUDIT)]
        return any(s.status == StepStatus.COMPLETED for s in verify_steps)

@dataclass
class ExecutionPlan:
    goals: List[Goal] = field(default_factory=list)
    
    def get_step(self, step_id: str) -> Optional[PlanStep]:
        for goal in self.goals:
            for step in goal.steps:
                if step.id == step_id:
                    return step
        return None
