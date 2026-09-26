from harness.planning.models import ExecutionPlan, PlanStep, StepAction, StepStatus


class ValidationError(Exception):
    pass

class Planner:
    def __init__(self, allowed_paths: list[str], max_steps: int = 50):
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
                    # Enforce target scope constraint (normalized so "src"
                    # cannot be escaped by a sibling prefix like "src2/").
                    normalized = step.target.lstrip("./")
                    if not any(
                        normalized == p.strip("./")
                        or normalized.startswith(p.strip("./").rstrip("/") + "/")
                        for p in self.allowed_paths
                    ):
                        raise ValidationError(
                            f"Step {step.id} attempts to write to un-allowed target: {step.target}"
                        )

                if step.action in (StepAction.VERIFY, StepAction.AUDIT):
                    has_verification = True

            if not has_verification:
                raise ValidationError(
                    f"Goal {goal.id} lacks a verification path (verify or audit)."
                )

        # Cycle and unknown dependency detection
        for step in all_steps.values():
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

    def get_runnable_steps(self, plan: ExecutionPlan) -> list[PlanStep]:
        """All runnable steps in deterministic goal/step order (issue #49).

        A step is runnable when it is PENDING and every dependency exists
        and is COMPLETED — an unknown dependency blocks the step instead of
        silently counting as satisfied.
        """
        runnable: list[PlanStep] = []
        for goal in plan.goals:
            for step in goal.steps:
                if step.status is not StepStatus.PENDING:
                    continue
                if self._dependencies_satisfied(plan, step):
                    runnable.append(step)
        return runnable

    @staticmethod
    def _dependencies_satisfied(plan: ExecutionPlan, step: PlanStep) -> bool:
        for dep_id in step.dependencies:
            dep_step = plan.get_step(dep_id)
            if dep_step is None or dep_step.status is not StepStatus.COMPLETED:
                return False
        return True

    def get_next_runnable_step(self, plan: ExecutionPlan) -> PlanStep | None:
        runnable = self.get_runnable_steps(plan)
        return runnable[0] if runnable else None

    def _dependents_of(self, goal_steps: list[PlanStep], failed_id: str) -> set[str]:
        """The failed step plus everything transitively depending on it."""
        affected = {failed_id}
        changed = True
        while changed:
            changed = False
            for step in goal_steps:
                if step.id in affected:
                    continue
                if affected.intersection(step.dependencies):
                    affected.add(step.id)
                    changed = True
        return affected

    def replan(
        self,
        current_plan: ExecutionPlan,
        goal_id: str,
        new_steps: list[PlanStep],
        *,
        failed_step_id: str | None = None,
        failure_evidence: str = "",
    ) -> ExecutionPlan:
        """Bounded replacement plan after a classified failure (issue #50).

        Only the failed step and its transitive dependents are replaced;
        completed steps *and* unrelated pending steps of the goal are
        retained so progress and evidence survive. ``new_steps`` enters as
        PENDING (they are replacements, not completed work) and the whole
        plan is re-validated before returning, so the orchestrator can
        always identify the next runnable step deterministically.
        """
        if len(new_steps) > self.max_steps:
            raise ValidationError(
                f"replacement plan exceeds policy maximum of {self.max_steps} steps"
            )
        replaced = False
        for goal in current_plan.goals:
            if goal.id != goal_id:
                continue
            replaced = True
            if failed_step_id is None:
                affected: set[str] = set()
            else:
                affected = self._dependents_of(goal.steps, failed_step_id)

            retained: list[PlanStep] = []
            for step in goal.steps:
                if step.id in affected:
                    continue  # replaced by new_steps
                if step.status is StepStatus.FAILED:
                    step.status = StepStatus.PENDING  # retry-able requeue
                retained.append(step)
            for new_step in new_steps:
                new_step.status = StepStatus.PENDING
                retained.append(new_step)
            goal.steps = retained
        if not replaced:
            raise ValidationError(f"unknown goal {goal_id!r} in plan")

        self.validate_plan(current_plan)
        return current_plan
