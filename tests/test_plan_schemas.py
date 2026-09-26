"""Issue #48: structured execution plan and step schemas."""

import pytest

from harness.planning.models import (
    ExecutionPlan,
    Goal,
    PlanStep,
    StepAction,
    StepSchemaError,
    StepStatus,
    plan_from_dict,
    plan_to_dict,
)


def _plan() -> ExecutionPlan:
    return ExecutionPlan(goals=[
        Goal(
            id="G1",
            description="Fix invalid password handling",
            steps=[
                PlanStep(
                    id="S1", action=StepAction.INSPECT, target="src/auth.py",
                    expected_evidence="tool_call",
                    completion_criteria="flow understood",
                ),
                PlanStep(
                    id="S2", action=StepAction.PATCH, target="src/auth.py",
                    expected_evidence="diff",
                    completion_criteria="401 returned for bad password",
                    dependencies=["S1"],
                ),
                PlanStep(
                    id="S3", action=StepAction.VERIFY, target="tests/test_auth.py",
                    expected_evidence="test_run",
                    completion_criteria="targeted tests pass",
                    dependencies=["S2"],
                ),
            ],
        ),
        Goal(id="G2", description="Regression check"),
    ])


def test_plan_round_trips_through_serialization():
    plan = _plan()
    restored = plan_from_dict(plan_to_dict(plan))

    original = {(g.id, s.id): s for g in plan.goals for s in g.steps}
    restored_steps = {(g.id, s.id): s for g in restored.goals for s in g.steps}
    assert set(original) == set(restored_steps)
    for key, step in original.items():
        other = restored_steps[key]
        assert other.action is step.action
        assert other.target == step.target
        assert other.dependencies == step.dependencies
        assert other.status is step.status
        assert other.expected_evidence == step.expected_evidence
        assert other.completion_criteria == step.completion_criteria


def test_goal_verification_semantics_are_preserved():
    plan = _plan()
    restored = plan_from_dict(plan_to_dict(plan))
    assert restored.goals[0].is_verified() is False  # S3 not completed yet
    restored.get_step("S3").status = StepStatus.COMPLETED
    assert restored.goals[0].is_verified() is True


def test_unknown_action_and_status_are_schema_errors():
    payload = {
        "goals": [{
            "id": "G1",
            "steps": [{
                "id": "S1", "action": "detonate", "target": "src/x.py",
            }],
        }],
    }
    with pytest.raises(StepSchemaError) as excinfo:
        plan_from_dict(payload)
    assert "unknown step action" in str(excinfo.value)

    payload["goals"][0]["steps"][0]["action"] = "inspect"
    payload["goals"][0]["steps"][0]["status"] = "WIBBLE"
    with pytest.raises(StepSchemaError):
        plan_from_dict(payload)


def test_write_steps_require_target_and_completion_criteria():
    payload = {
        "goals": [{
            "id": "G1",
            "steps": [{
                "id": "S1", "action": "patch", "target": "src/auth.py",
                "completion_criteria": "",
            }],
        }],
    }
    with pytest.raises(StepSchemaError) as excinfo:
        plan_from_dict(payload)
    assert "completion criteria" in str(excinfo.value)

    payload["goals"][0]["steps"][0]["completion_criteria"] = "fixed"
    payload["goals"][0]["steps"][0]["target"] = "../outside.py"
    with pytest.raises(StepSchemaError) as excinfo:
        plan_from_dict(payload)
    assert "path traversal" in str(excinfo.value)


def test_missing_required_fields_are_schema_errors():
    payload = {"goals": [{"id": "", "steps": []}]}
    with pytest.raises(StepSchemaError):
        plan_from_dict(payload)
    with pytest.raises(StepSchemaError):
        plan_from_dict({"no-goals-here": True})
    with pytest.raises(StepSchemaError):
        plan_from_dict({
            "goals": [{"id": "G1", "steps": [
                {"id": "S1", "action": "inspect", "target": ""}
            ]}],
        })
