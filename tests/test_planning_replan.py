"""Issue #50: replan from material evidence without losing progress."""

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
        Goal(id="G1", description="fix auth", steps=[
            PlanStep(id="S1", action=StepAction.INSPECT, target="src/auth.py",
                     expected_evidence="tool_call", completion_criteria="understood",
                     status=StepStatus.COMPLETED),
            PlanStep(id="S2", action=StepAction.PATCH, target="src/auth.py",
                     expected_evidence="diff", completion_criteria="fixed",
                     dependencies=["S1"], status=StepStatus.FAILED),
            PlanStep(id="S3", action=StepAction.VERIFY, target="tests/test_auth.py",
                     expected_evidence="test_run", completion_criteria="tests pass",
                     dependencies=["S2"]),
        ]),
        Goal(id="G2", description="docs", steps=[
            PlanStep(id="S4", action=StepAction.INSPECT, target="docs/auth.md",
                     expected_evidence="tool_call", completion_criteria="read"),
            PlanStep(id="S5", action=StepAction.VERIFY, target="docs/",
                     expected_evidence="report", completion_criteria="docs checked"),
        ]),
    ])


def _planner() -> Planner:
    return Planner(allowed_paths=["src/", "tests/", "docs/"])


def _replacement() -> list[PlanStep]:
    return [
        PlanStep(id="S2b", action=StepAction.PATCH, target="src/auth.py",
                 expected_evidence="diff", completion_criteria="fixed differently"),
        PlanStep(id="S3b", action=StepAction.VERIFY, target="tests/test_auth.py",
                 expected_evidence="test_run", completion_criteria="tests pass",
                 dependencies=["S2b"]),
    ]


def test_replan_replaces_only_failed_step_and_its_dependents():
    planner = _planner()
    plan = _plan()
    new_plan = planner.replan(
        plan, "G1", _replacement(),
        failed_step_id="S2", failure_evidence="assert 500 == 401",
    )

    step_ids = {g.id: {s.id for s in g.steps} for g in new_plan.goals}
    # failed step and its transitive dependents are replaced...
    assert "S2" not in step_ids["G1"] and "S3" not in step_ids["G1"]
    assert {"S2b", "S3b"} <= step_ids["G1"]
    # ...completed work is retained with its status...
    assert new_plan.get_step("S1").status is StepStatus.COMPLETED
    # ...and unrelated pending steps of the same goal are kept, not dropped
    assert "S4" in step_ids["G2"]
    assert new_plan.get_step("S4").status is StepStatus.PENDING


def test_replan_produces_deterministic_next_runnable_step():
    planner = _planner()
    plan = _plan()
    new_plan = planner.replan(
        plan, "G1", _replacement(), failed_step_id="S2",
    )

    # S1 is completed; S2b has no unfinished dependencies and enters as PENDING
    next_step = planner.get_next_runnable_step(new_plan)
    assert next_step is not None
    assert next_step.id == "S2b"


def test_replacement_steps_enter_as_pending():
    planner = _planner()
    plan = _plan()
    replacement = _replacement()
    replacement[0].status = StepStatus.COMPLETED  # caller tries to smuggle status

    new_plan = planner.replan(plan, "G1", replacement, failed_step_id="S2")

    assert new_plan.get_step("S2b").status is StepStatus.PENDING


def test_replan_requeues_an_unanchored_failed_step_for_retry():
    planner = _planner()
    plan = _plan()
    # no failed_step_id and no replacement steps: transient failure, requeue
    new_plan = planner.replan(plan, "G1", [])

    assert new_plan.get_step("S2").status is StepStatus.PENDING
    assert new_plan.get_step("S1").status is StepStatus.COMPLETED
    next_step = planner.get_next_runnable_step(new_plan)
    assert next_step is not None and next_step.id == "S2"


def test_replan_is_bounded_by_policy():
    planner = Planner(allowed_paths=["src/"], max_steps=2)
    too_many = [PlanStep(id=f"X{i}", action=StepAction.INSPECT,
                         target="src/x.py", expected_evidence="t",
                         completion_criteria="c") for i in range(5)]
    with pytest.raises(ValidationError) as excinfo:
        planner.replan(_plan(), "G1", too_many, failed_step_id="S2")
    assert "policy maximum" in str(excinfo.value)


def test_replan_rejects_unknown_goal():
    with pytest.raises(ValidationError) as excinfo:
        _planner().replan(_plan(), "G404", _replacement(), failed_step_id="S2")
    assert "unknown goal" in str(excinfo.value)


def test_replanned_plan_still_passes_full_validation():
    planner = _planner()
    plan = _plan()
    new_plan = planner.replan(plan, "G1", _replacement(), failed_step_id="S2")
    planner.validate_plan(new_plan)  # must not raise
