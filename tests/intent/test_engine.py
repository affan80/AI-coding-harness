"""Issue #34: mixed objectives, scope/constraint attachment, bounded retries."""

import asyncio

import pytest

from harness.core.errors import SerializationError
from harness.core.models import UserRequest
from harness.intent.engine import IntentEngine, intent_response_schema
from harness.intent.schemas import GoalKind, IntentError
from harness.model.fake import FakeModelClient, ScriptedTurn

GOOD_PAYLOAD = {
    "objective": "Add JWT login, fix the password 500, audit the auth module",
    "root_goal_id": "G2",
    "goals": [
        {"goal_id": "G1", "title": "Diagnose the password 500", "kind": "fix",
         "acceptance_criteria": ["root cause identified"],
         "verification_criteria": ["reproducer exists"]},
        {"goal_id": "G2", "title": "Add JWT login endpoint", "kind": "feature",
         "depends_on": ["G1"],
         "acceptance_criteria": ["POST /login issues a JWT"],
         "verification_criteria": ["pytest tests/test_login.py"]},
        {"goal_id": "G3", "title": "Audit the auth module", "kind": "audit",
         "depends_on": ["G2"],
         "acceptance_criteria": ["findings have evidence"],
         "verification_criteria": ["audit report exists"],
         "is_assumption": True},
    ],
    "constraints": [],
    "assumptions": [
        {"assumption_id": "A1",
         "description": "JWT secret is provided via environment"},
    ],
    "completion_criteria": ["full suite green"],
}

MALFORMED_PAYLOAD = {
    "objective": "Add JWT login",
    "goals": [
        {"goal_id": "G1", "title": "Broken", "kind": "deploy-to-prod",
         "acceptance_criteria": [], "verification_criteria": []},
    ],
}


def _request(**overrides) -> UserRequest:
    fields = {
        "objective": GOOD_PAYLOAD["objective"],
        "repository_path": "/repo",
        "scope_paths": ("backend/", "tests/"),
        "constraints": ("no new dependencies",),
    }
    fields.update(overrides)
    return UserRequest(**fields)


def _engine(client, **kwargs) -> IntentEngine:
    return IntentEngine(client, **kwargs)


def test_mixed_objective_produces_ordered_typed_graph():
    client = FakeModelClient(script=[ScriptedTurn(structured=GOOD_PAYLOAD)])
    graph = asyncio.run(_engine(client).extract(_request()))

    assert [g.goal_id for g in graph.goals] == ["G1", "G2", "G3"]
    assert graph.goals[0].kind is GoalKind.FIX
    assert graph.goals[1].kind is GoalKind.FEATURE
    assert graph.goals[2].kind is GoalKind.AUDIT
    assert graph.goals[1].depends_on == ("G1",)
    assert graph.root_goal_id == "G2"
    # the model call used the goal_graph response schema
    assert client.calls[0].response_schema.name == "goal_graph"


def test_user_scope_and_constraints_attached_to_every_goal():
    client = FakeModelClient(script=[ScriptedTurn(structured=GOOD_PAYLOAD)])
    graph = asyncio.run(_engine(client).extract(_request()))

    attached = {"scope-1", "scope-2", "user-1"}
    for goal in graph.goals:
        assert attached <= set(goal.constraint_ids)
    descriptions = {c.description: c.source for c in graph.constraints}
    assert descriptions["approved scope: backend/"] == "user"
    assert descriptions["approved scope: tests/"] == "user"
    assert descriptions["no new dependencies"] == "user"
    # user facts are attached even though the model returned no constraints
    assert len(graph.constraints) == 3


def test_assumptions_stay_separately_labeled_from_requirements():
    client = FakeModelClient(script=[ScriptedTurn(structured=GOOD_PAYLOAD)])
    graph = asyncio.run(_engine(client).extract(_request()))

    assert graph.assumptions[0].description.startswith("JWT secret")
    assumed = [g for g in graph.goals if g.is_assumption]
    assert [g.goal_id for g in assumed] == ["G3"]
    required = [g for g in graph.goals if not g.is_assumption]
    assert {g.goal_id for g in required} == {"G1", "G2"}


def test_malformed_output_triggers_one_correction_retry():
    client = FakeModelClient(script=[
        ScriptedTurn(structured=MALFORMED_PAYLOAD),
        ScriptedTurn(structured=GOOD_PAYLOAD),
    ])
    spent: list[str] = []
    def allow_retry(_key: str) -> bool:
        spent.append(_key)
        return True

    graph = asyncio.run(_engine(client, register_retry=allow_retry).extract(_request()))

    assert len(graph.goals) == 3
    assert len(client.calls) == 2
    correction = client.calls[1].messages[-1].content
    assert "rejected by schema validation" in correction
    # the correction names the specific problems found
    assert "kind" in correction or "criteria" in correction
    assert spent == ["intent"]


def test_exhausted_retries_raise_structured_intent_error():
    client = FakeModelClient(script=[
        ScriptedTurn(structured=MALFORMED_PAYLOAD),
        ScriptedTurn(structured=MALFORMED_PAYLOAD),
    ])
    with pytest.raises(IntentError) as excinfo:
        asyncio.run(_engine(client).extract(_request()))
    assert excinfo.value.details["validation_problems"]
    assert len(client.calls) == 2  # bounded: initial + one correction


def test_retry_budget_exhaustion_stops_immediately():
    client = FakeModelClient(script=[ScriptedTurn(structured=MALFORMED_PAYLOAD)])
    with pytest.raises(IntentError):
        asyncio.run(_engine(client, register_retry=lambda _k: False).extract(_request()))
    assert len(client.calls) == 1


def test_scope_and_constraints_reach_the_model_prompt():
    client = FakeModelClient(script=[ScriptedTurn(structured=GOOD_PAYLOAD)])
    asyncio.run(_engine(client).extract(_request()))
    prompt = "\n".join(m.content for m in client.calls[0].messages)
    assert "backend/" in prompt and "tests/" in prompt
    assert "no new dependencies" in prompt
    # the schema requested structured output
    schema = client.calls[0].response_schema
    assert set(schema.schema["properties"]["goals"]["items"]["properties"][
        "kind"]["enum"]) == {k.value for k in GoalKind}


def test_response_schema_is_frozen_and_complete():
    schema = intent_response_schema()
    try:
        schema.name = "mutated"  # type: ignore[misc]
        raised = False
    except (TypeError, AttributeError):
        raised = True
    assert raised, "the response schema must be immutable"
    assert schema.schema["required"] == ["objective", "goals"]


def test_malformed_non_dict_payload_is_structured_failure():
    from harness.intent.schemas import parse_goal_payload

    with pytest.raises(SerializationError):
        parse_goal_payload(["not", "a", "dict"])
