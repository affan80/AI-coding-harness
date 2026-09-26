"""Issue #33: typed intent/goal-graph schemas and their validation boundaries."""

import pytest

from harness.core.errors import SerializationError
from harness.core.models import GoalStatus
from harness.intent.schemas import (
    Assumption,
    Constraint,
    Goal,
    GoalGraph,
    GoalKind,
    IntentError,
    parse_goal_payload,
)


def _mixed_graph() -> GoalGraph:
    return GoalGraph(
        objective="Ship auth: add login, fix the 500, audit the module",
        root_goal_id="G1",
        goals=(
            Goal(goal_id="G1", title="Ship authentication", kind=GoalKind.FEATURE,
                 acceptance_criteria=("login endpoint exists",),
                 verification_criteria=("pytest tests/test_login.py",)),
            Goal(goal_id="G2", title="Create migration for users table",
                 kind=GoalKind.CREATE, depends_on=("G1",),
                 acceptance_criteria=("migration applies cleanly",),
                 verification_criteria=("alembic upgrade head",)),
            Goal(goal_id="G3", title="Fix invalid password 500", kind=GoalKind.FIX,
                 depends_on=("G1",),
                 acceptance_criteria=("invalid password returns 401",),
                 verification_criteria=("pytest -k invalid_password",),
                 constraint_ids=("C1",)),
            Goal(goal_id="G4", title="Audit auth module", kind=GoalKind.AUDIT,
                 depends_on=("G3",),
                 acceptance_criteria=("findings have evidence",),
                 verification_criteria=("audit report exists",)),
            Goal(goal_id="G5", title="Add regression tests", kind=GoalKind.TEST,
                 depends_on=("G3",),
                 acceptance_criteria=("regression test exists",),
                 verification_criteria=("pytest -k regression",)),
            Goal(goal_id="G6", title="Verify full suite", kind=GoalKind.VERIFY,
                 depends_on=("G4", "G5"),
                 acceptance_criteria=("suite green",),
                 verification_criteria=("pytest -q",)),
            Goal(goal_id="G7", title="Extract auth helpers", kind=GoalKind.REFACTOR,
                 depends_on=("G6",),
                 acceptance_criteria=("no behavior change",),
                 verification_criteria=("pytest -q",)),
            Goal(goal_id="G8", title="Reduce login latency", kind=GoalKind.OPTIMIZE,
                 depends_on=("G7",),
                 acceptance_criteria=("login < 200ms",),
                 verification_criteria=("benchmark",),
                 is_assumption=True),
        ),
        constraints=(
            Constraint(constraint_id="C1", description="no new dependencies",
                       source="user"),
        ),
        assumptions=(Assumption(assumption_id="A1",
                                description="JWT secret comes from env"),),
        completion_criteria=("full suite green", "audit findings triaged"),
    )


def test_mixed_objective_serializes_without_prose_parsing():
    graph = _mixed_graph()
    kinds = {g.kind for g in graph.goals}
    assert kinds >= {
        GoalKind.CREATE, GoalKind.FIX, GoalKind.AUDIT, GoalKind.REFACTOR,
        GoalKind.TEST, GoalKind.VERIFY,
    }
    data = graph.to_dict()
    # every goal is fully typed: kind enum, dependency ids, criteria lists
    goal_data = {g["goal_id"]: g for g in data["goals"]}
    assert goal_data["G3"]["kind"] == "fix"
    assert goal_data["G6"]["depends_on"] == ["G4", "G5"]
    assert goal_data["G2"]["kind"] == "create"
    restored = GoalGraph.from_dict(data)
    assert restored == graph


def test_user_requirements_and_assumptions_stay_separately_labeled():
    graph = _mixed_graph()
    assert graph.goals[2].constraint_ids == ("C1",)  # user constraint attached
    assert graph.user_constraints()[0].description == "no new dependencies"
    assert graph.assumptions[0].description == "JWT secret comes from env"
    assumed = [g for g in graph.goals if g.is_assumption]
    assert [g.goal_id for g in assumed] == ["G8"]


def test_validation_rejects_unknown_dependency_and_constraint():
    graph = GoalGraph(
        objective="obj",
        goals=(
            Goal(goal_id="G1", title="t", kind=GoalKind.FIX,
                 depends_on=("G99",),
                 acceptance_criteria=("a",), verification_criteria=("v",),
                 constraint_ids=("C404",)),
        ),
    )
    problems = graph.validate()
    assert any("unknown goal G99" in p for p in problems)
    assert any("unknown constraint C404" in p for p in problems)


def test_validation_rejects_cycles_missing_criteria_and_bad_root():
    graph = GoalGraph(
        objective="obj", root_goal_id="GX",
        goals=(
            Goal(goal_id="G1", title="t", kind=GoalKind.FIX, depends_on=("G2",),
                 acceptance_criteria=("a",), verification_criteria=("v",)),
            Goal(goal_id="G2", title="t", kind=GoalKind.FIX, depends_on=("G1",),
                 acceptance_criteria=(), verification_criteria=()),
        ),
    )
    problems = graph.validate()
    assert any("cycle" in p for p in problems)
    assert any("root goal" in p for p in problems)
    assert any("no acceptance criteria" in p for p in problems)
    assert any("no verification criteria" in p for p in problems)
    with pytest.raises(SerializationError):
        GoalGraph.from_dict(graph.to_dict())


def test_from_dict_rejects_unknown_kind_and_status():
    payload = {
        "objective": "obj",
        "goals": [{"goal_id": "G1", "title": "t", "kind": "deploy-to-prod",
                   "acceptance_criteria": ["a"], "verification_criteria": ["v"]}],
    }
    with pytest.raises(SerializationError) as excinfo:
        GoalGraph.from_dict(payload)
    assert "kind" in str(excinfo.value.details)

    payload["goals"][0]["kind"] = "fix"
    payload["goals"][0]["status"] = "WIBBLE"
    with pytest.raises(SerializationError):
        GoalGraph.from_dict(payload)


def test_parse_goal_payload_rejects_non_objects_and_empty_goals():
    with pytest.raises(SerializationError):
        parse_goal_payload("just prose")
    with pytest.raises(SerializationError):
        parse_goal_payload({"objective": "obj", "goals": []})
    with pytest.raises(SerializationError):
        parse_goal_payload(None)


def test_pending_goals_and_lookup():
    graph = _mixed_graph()
    assert graph.goal("G3").kind is GoalKind.FIX
    with pytest.raises(KeyError):
        graph.goal("nope")
    assert all(g.status is GoalStatus.PENDING for g in graph.pending_goals())
    assert isinstance(IntentError("x"), Exception)
