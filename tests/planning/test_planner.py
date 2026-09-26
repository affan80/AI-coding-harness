import unittest

from harness.planning.models import ExecutionPlan, Goal, PlanStep, StepAction, StepStatus
from harness.planning.planner import Planner, ValidationError


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = Planner(allowed_paths=["src/", "tests/"])

    def test_valid_plan(self):
        s1 = PlanStep("S1", StepAction.INSPECT, "src/main.py", "Inspect code", "Code read")
        s2 = PlanStep(
            "S2", StepAction.PATCH, "src/main.py", "Apply fix", "Fix applied", dependencies=["S1"]
        )
        s3 = PlanStep(
            "S3", StepAction.VERIFY, "tests/test_main.py", "Run tests", "Tests pass",
            dependencies=["S2"],
        )

        goal = Goal("G1", "Fix bug", steps=[s1, s2, s3])
        plan = ExecutionPlan(goals=[goal])

        # Should not raise
        self.planner.validate_plan(plan)

        # Next step should be S1 since it has no dependencies
        next_step = self.planner.get_next_runnable_step(plan)
        self.assertIsNotNone(next_step)
        self.assertEqual(next_step.id, "S1")

    def test_missing_verification(self):
        s1 = PlanStep("S1", StepAction.PATCH, "src/main.py", "Apply fix", "Fix applied")
        goal = Goal("G1", "Fix bug", steps=[s1])
        plan = ExecutionPlan(goals=[goal])

        with self.assertRaises(ValidationError) as context:
            self.planner.validate_plan(plan)
        self.assertIn("lacks a verification path", str(context.exception))

    def test_unallowed_target_path(self):
        s1 = PlanStep("S1", StepAction.PATCH, "etc/shadow", "Write file", "Done")
        s2 = PlanStep("S2", StepAction.VERIFY, "tests/test.py", "Test", "Pass", dependencies=["S1"])
        goal = Goal("G1", "Hack", steps=[s1, s2])
        plan = ExecutionPlan(goals=[goal])

        with self.assertRaises(ValidationError) as context:
            self.planner.validate_plan(plan)
        self.assertIn("attempts to write to un-allowed target", str(context.exception))

    def test_circular_dependency(self):
        s1 = PlanStep("S1", StepAction.INSPECT, "src/a.py", "A", "A", dependencies=["S2"])
        s2 = PlanStep("S2", StepAction.VERIFY, "tests/a.py", "A", "A", dependencies=["S1"])
        goal = Goal("G1", "Cycle", steps=[s1, s2])
        plan = ExecutionPlan(goals=[goal])

        with self.assertRaises(ValidationError) as context:
            self.planner.validate_plan(plan)
        self.assertIn("Circular dependency", str(context.exception))

    def test_next_runnable_step(self):
        s1 = PlanStep(
            "S1", StepAction.INSPECT, "src/main.py", "A", "A", status=StepStatus.COMPLETED
        )
        s2 = PlanStep(
            "S2", StepAction.PATCH, "src/main.py", "A", "A",
            dependencies=["S1"], status=StepStatus.PENDING,
        )
        s3 = PlanStep(
            "S3", StepAction.VERIFY, "tests/test.py", "A", "A",
            dependencies=["S2"], status=StepStatus.PENDING,
        )

        goal = Goal("G1", "Flow", steps=[s1, s2, s3])
        plan = ExecutionPlan(goals=[goal])

        # S1 is completed, so S2 is the next runnable step
        next_step = self.planner.get_next_runnable_step(plan)
        self.assertIsNotNone(next_step)
        self.assertEqual(next_step.id, "S2")

    def test_replan(self):
        s1 = PlanStep(
            "S1", StepAction.INSPECT, "src/main.py", "A", "A", status=StepStatus.COMPLETED
        )
        s2 = PlanStep(
            "S2", StepAction.PATCH, "src/main.py", "A", "A",
            dependencies=["S1"], status=StepStatus.FAILED,
        )
        s3 = PlanStep(
            "S3", StepAction.VERIFY, "tests/test.py", "A", "A", dependencies=["S2"]
        )
        goal = Goal("G1", "Flow", steps=[s1, s2, s3])
        plan = ExecutionPlan(goals=[goal])

        # We replace the failed step and subsequent steps
        new_s2 = PlanStep(
            "S2_NEW", StepAction.PATCH, "src/main.py", "A", "A", dependencies=["S1"]
        )
        new_s3 = PlanStep(
            "S3_NEW", StepAction.VERIFY, "tests/test.py", "A", "A", dependencies=["S2_NEW"]
        )

        new_plan = self.planner.replan(
            plan, "G1", [new_s2, new_s3], failed_step_id="S2"
        )

        # S1 should still be there
        self.assertIsNotNone(new_plan.get_step("S1"))
        self.assertEqual(new_plan.get_step("S1").status, StepStatus.COMPLETED)

        # S2_NEW and S3_NEW should be there
        self.assertIsNotNone(new_plan.get_step("S2_NEW"))
        self.assertIsNotNone(new_plan.get_step("S3_NEW"))

        # S2 and S3 should be gone
        self.assertIsNone(new_plan.get_step("S2"))
        self.assertIsNone(new_plan.get_step("S3"))

if __name__ == '__main__':
    unittest.main()
