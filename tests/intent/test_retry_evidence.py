"""Issue #35: invalid output never reaches planning; failures carry evidence."""

import asyncio

import pytest

from harness.core.errors import SerializationError
from harness.core.models import UserRequest
from harness.intent.engine import IntentEngine
from harness.intent.schemas import GoalGraph, IntentError, parse_goal_payload
from harness.model.fake import FakeModelClient, ScriptedTurn

MALFORMED = {
    "objective": "obj",
    "goals": [{
        "goal_id": "G1", "title": "t", "kind": "not-a-kind",
        "acceptance_criteria": [], "verification_criteria": [],
    }],
}


def _request() -> UserRequest:
    return UserRequest(objective="obj", repository_path="/repo")


def test_invalid_output_never_reaches_planning():
    """The engine returns a GoalGraph or raises — malformed payloads cannot
    leak through as a graph object planning could consume."""
    seen_graphs: list[GoalGraph] = []

    original_parse = parse_goal_payload

    def spying_parse(payload):
        try:
            graph = original_parse(payload)
        except SerializationError:
            raise
        seen_graphs.append(graph)
        return graph

    client = FakeModelClient(script=[
        ScriptedTurn(structured=MALFORMED),
        ScriptedTurn(structured=MALFORMED),
    ])
    engine = IntentEngine(client)
    # patch at the module boundary the engine actually calls
    import harness.intent.engine as engine_module

    original = engine_module.parse_goal_payload
    engine_module.parse_goal_payload = spying_parse
    try:
        with pytest.raises(IntentError):
            asyncio.run(engine.extract(_request()))
    finally:
        engine_module.parse_goal_payload = original

    assert seen_graphs == []  # planning would have received nothing


def test_exhausted_retries_return_structured_failure_with_evidence():
    client = FakeModelClient(script=[
        ScriptedTurn(structured=MALFORMED),
        ScriptedTurn(structured=MALFORMED),
    ])
    with pytest.raises(IntentError) as excinfo:
        asyncio.run(IntentEngine(client).extract(_request()))

    details = excinfo.value.details
    assert details["reached_planning"] is False
    assert details["validation_problems"]
    evidence = details["evidence"]
    assert len(evidence) == 2  # one reference per rejected attempt
    assert [e["metadata"]["attempt"] for e in evidence] == [1, 2]
    assert evidence[0]["kind"] == "model_response"
    assert "rejected by schema validation" in evidence[0]["description"]
    # evidence is JSON-serializable for the run directory
    import json

    assert json.loads(json.dumps(details["evidence"]))


def test_successful_extraction_carries_no_failure_evidence():
    good = {
        "objective": "obj",
        "goals": [{
            "goal_id": "G1", "title": "t", "kind": "fix",
            "acceptance_criteria": ["a"], "verification_criteria": ["v"],
        }],
    }
    client = FakeModelClient(script=[ScriptedTurn(structured=good)])
    graph = asyncio.run(IntentEngine(client).extract(_request()))
    assert graph.goal("G1").kind.value == "fix"

