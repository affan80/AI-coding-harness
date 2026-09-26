from typing import List, Optional, Set
from harness.planning.models import ExecutionPlan, Goal, PlanStep, StepAction, StepStatus

class ValidationError(Exception):
    pass

class Planner:
    def __init__(self, allowed_paths: List[str], max_steps: int = 50):
        # allowed_paths e.g. ["src/", "tests/", "docs/"]
        self.allowed_paths = allowed_paths
        self.max_steps = max_steps

    def validate_plan(self, plan: ExecutionPlan):
        total_steps = sum(len(g.steps) for g in plan.goals)
        if total_steps > self.max_steps:
            raise ValidationError(f"Plan exceeds policy maximum of {self.max_steps} steps.")

        all_steps = {}
        for goal in plan.goals:
            has_verification = False
            for step in goal.steps:
                if step.id in all_steps:
                    raise ValidationError(f"Duplicate step id: {step.id}")
                all_steps[step.id] = step
                
                if step.action == StepAction.PATCH:
                    # Enforce target scope constraint
                    if not any(step.target.startswith(p) for p in self.allowed_paths):
                        raise ValidationError(f"Step {step.id} attempts to write to un-allowed target: {step.target}")

                if step.action in (StepAction.VERIFY, StepAction.AUDIT):
                    has_verification = True

            if not has_verification:
                raise ValidationError(f"Goal {goal.id} lacks a verification path (verify or audit).")

        # Cycle and unknown dependency detection
        for step_id, step in all_steps.items():
            for dep in step.dependencies:
                if dep not in all_steps:
                    raise ValidationError(f"Step {step.id} depends on unknown step {dep}")

        visited = set()
        stack = set()
        
        def dfs(curr_id):
            if curr_id in stack:
                raise ValidationError(f"Circular dependency detected involving {curr_id}")
            if curr_id in visited:
                return
            
            stack.add(curr_id)
            for dep_id in all_steps[curr_id].dependencies:
                dfs(dep_id)
            stack.remove(curr_id)
            visited.add(curr_id)

        for step_id in all_steps:
            dfs(step_id)

    def get_next_runnable_step(self, plan: ExecutionPlan) -> Optional[PlanStep]:
        for goal in plan.goals:
            for step in goal.steps:
                if step.status == StepStatus.PENDING:
                    deps_completed = True
                    for dep_id in step.dependencies:
                        dep_step = plan.get_step(dep_id)
                        if dep_step and dep_step.status != StepStatus.COMPLETED:
                            deps_completed = False
                            break
                    if deps_completed:
                        return step
        return None

    def replan(self, current_plan: ExecutionPlan, goal_id: str, new_steps: List[PlanStep]) -> ExecutionPlan:
        """
        Replaces the steps of a specific goal with new_steps to recover from a failure.
        Completed steps in that goal are retained. The entire plan is re-validated.
        """
        for goal in current_plan.goals:
            if goal.id == goal_id:
                retained_steps = [s for s in goal.steps if s.status == StepStatus.COMPLETED]
                retained_ids = {s.id for s in retained_steps}
                
                for ns in new_steps:
                    if ns.id not in retained_ids:
                        retained_steps.append(ns)
                        
                goal.steps = retained_steps
        
        self.validate_plan(current_plan)
        return current_plan
