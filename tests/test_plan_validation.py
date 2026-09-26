"""Issue #49: dependency/policy validation and runnable-step identification."""

import pytest

from harness.planning.models import (
    ExecutionPlan,
    Goal,
    PlanStep,
    StepAction,
    StepStatus,
)
from harness.planning.planner import Planner, ValidationError


def _plan() -> ExecutionPlan:
    return ExecutionPlan(goals=[
        Goal(id="G1", description="fix", steps=[
            PlanStep(id="S1", action=StepAction.INSPECT, target="src/auth.py",
                     expected_evidence="tool_call",
                     completion_criteria="understood"),
            PlanStep(id="S2", action=StepAction.PATCH, target="src/auth.py",
                     expected_evidence="diff",
                     completion_criteria="fixed", dependencies=["S1"]),
            PlanStep(id="S3", action=StepAction.VERIFY, target="tests/test_auth.py",
                     expected_evidence="test_run",
                     completion_criteria="tests pass", dependencies=["S2"]),
        ]),
    ])


def _planner() -> Planner:
    return Planner(allowed_paths=["src/", "tests/"])


def test_all_independent_runnable_steps_are_identified_in_order():
    plan = _plan()
    runnable = _planner().get_runnable_steps(plan)
    assert [s.id for s in runnable] == ["S1"]  # S2/S3 blocked by deps

    plan.get_step("S1").status = StepStatus.COMPLETED
    runnable = _planner().get_runnable_steps(plan)
    assert [s.id for s in runnable] == ["S2"]

    plan.get_step("S2").status = StepStatus.COMPLETED
    runnable = _planner().get_runnable_steps(plan)
    assert [s.id for s in runnable] == ["S3"]

    plan.get_step("S3").status = StepStatus.COMPLETED
    assert _planner().get_runnable_steps(plan) == []


def test_independent_steps_across_goals_come_out_together():
    plan = ExecutionPlan(goals=[
        Goal(id="G1", description="a", steps=[
            PlanStep(id="S1", action=StepAction.INSPECT, target="src/a.py",
                     expected_evidence="t", completion_criteria="c"),
        ]),
        Goal(id="G2", description="b", steps=[
            PlanStep(id="S2", action=StepAction.INSPECT, target="src/b.py",
                     expected_evidence="t", completion_criteria="c"),
        ]),
    ])
    runnable = _planner().get_runnable_steps(plan)
    assert [s.id for s in runnable] == ["S1", "S2"]


def test_missing_dependency_blocks_instead_of_counting_as_satisfied():
    plan = _plan()
    step = plan.get_step("S2")
    step.dependencies = ["S1", "GHOST"]
    # validate_plan rejects the unknown dependency outright...
    with pytest.raises(ValidationError):
        _planner().validate_plan(plan)
    # ...and even before validation, the blocked step is not runnable
    runnable_ids = [s.id for s in _planner().get_runnable_steps(plan)]
    assert runnable_ids == ["S1"]  # S2 with the ghost dependency stays out


def test_failed_dependency_blocks_dependent_steps():
    plan = _plan()
    plan.get_step("S1").status = StepStatus.FAILED
    assert _planner().get_runnable_steps(plan) == []


def test_scope_matching_is_normalized_not_prefix_loose():
    planner = Planner(allowed_paths=["src/"])
    plan = ExecutionPlan(goals=[Goal(id="G1", description="d", steps=[
        PlanStep(id="S1", action=StepAction.PATCH, target="src2/evil.py",
                 expected_evidence="diff", completion_criteria="x"),
    ])])

    with pytest.raises(ValidationError) as excinfo:
        planner.validate_plan(plan)
    assert "un-allowed target" in str(excinfo.value)


def test_every_goal_must_have_deterministic_verification():
    plan = ExecutionPlan(goals=[
        Goal(id="G1", description="d", steps=[
            PlanStep(id="S1", action=StepAction.PATCH, target="src/a.py",
                     expected_evidence="diff", completion_criteria="x"),
        ]),
        Goal(id="G2", description="d2", steps=[
            PlanStep(id="S2", action=StepAction.AUDIT, target="src/",
                     expected_evidence="report", completion_criteria="y"),
        ]),
    ])
    # G1 has no verify/audit step: rejected even though G2 has one
    with pytest.raises(ValidationError) as excinfo:
        _planner().validate_plan(plan)
    assert "G1" in str(excinfo.value)
