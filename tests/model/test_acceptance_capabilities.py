"""M1.16 acceptance evidence: one async client contract, runtime limits (issue #36).

The contract and its tests landed with the model-client workstream; this
module maps the issue's acceptance bullets 1:1 so closing evidence is
explicit, and pins the usage-capability edges that had no direct test.
"""

import asyncio

from harness.model import (
    FakeModelClient,
    Message,
    ModelCapabilities,
    ModelClient,
    ModelResponse,
    ScriptedTurn,
    Usage,
    estimate_tokens,
)

ROLES = (
    "intent", "repository", "planner", "executor",
    "verification", "recovery", "audit",
)


def test_every_agent_role_calls_one_shared_client_and_queries_limits():
    """Acceptance: all logical roles reuse the same contract and can query
    limits at runtime — one instance, interleaved capability queries."""
    client = FakeModelClient(
        script=[ScriptedTurn(text=f"ok: {role}") for role in ROLES]
    )
    assert isinstance(client, ModelClient)  # the protocol is runtime-checkable

    for role in ROLES:
        capabilities = client.capabilities()  # runtime limit discovery
        assert capabilities.context_window_tokens > 0
        assert capabilities.max_output_tokens > 0
        response = asyncio.run(
            client.generate([Message(role="user", content=f"task for {role}")])
        )
        assert isinstance(response, ModelResponse)
        assert response.text == f"ok: {role}"


def test_usage_capability_contract_defaults_and_totals():
    """Usage is part of the reported contract (PRD §12.9): defaults are zero
    and totals derive from inputs + outputs."""
    usage = Usage()
    assert usage.input_tokens == 0 and usage.output_tokens == 0
    assert usage.total_tokens == 0
    assert Usage(input_tokens=120, output_tokens=30).total_tokens == 150


def test_tool_calling_and_structured_output_flags_are_queryable():
    capabilities = ModelCapabilities(
        provider="fake", model="m", context_window_tokens=1_000,
        max_output_tokens=100,
    )
    assert capabilities.tool_calling is True
    assert capabilities.structured_output is True
    restricted = ModelCapabilities(
        provider="fake", model="m2", context_window_tokens=1_000,
        max_output_tokens=100, tool_calling=False, structured_output=False,
    )
    assert restricted.tool_calling is False
    assert restricted.structured_output is False


def test_token_estimation_is_deterministic_for_budgeting():
    """Context budgeting relies on the deterministic estimator (PRD §12.1)."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == estimate_tokens("a" * 400)
