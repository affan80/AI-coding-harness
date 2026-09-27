"""Issue #38 acceptance: secure configuration and the deterministic fake client.

The secure-config and fake-client layers landed with the model-runtime
workstream; this module maps the issue's acceptance bullets 1:1 —
credentials come only from the environment and never leak, and tests can
reproduce responses, tool calls, and failures fully offline.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.model import (
    FakeModelClient,
    Message,
    ResponseSchema,
    ScriptedTurn,
    ToolCall,
    build_model_client,
    settings_from_env,
)


def test_offline_default_needs_no_credentials_and_makes_no_network():
    settings = settings_from_env({})
    assert settings.provider == "fake"
    # the default-built client is the offline fake
    client = build_model_client(env={})
    assert isinstance(client, FakeModelClient)


def test_provider_credentials_come_from_the_environment():
    settings = settings_from_env({
        "HARNESS_MODEL_PROVIDER": "openai",
        "HARNESS_OPENAI_API_KEY": "sk-official-key-123456",
    })
    assert settings.provider == "openai"
    # settings never reveal the key through str/repr
    assert "sk-official-key-123456" not in str(settings)
    assert "sk-official-key-123456" not in repr(settings)


def test_missing_credentials_are_structured_not_accidental_network():
    with pytest.raises(Exception) as excinfo:
        settings_from_env({"HARNESS_MODEL_PROVIDER": "openai"})
    assert "credential" in str(excinfo.value).lower()


def test_fake_reproduces_responses_and_tool_calls_offline():
    client = FakeModelClient(script=[
        ScriptedTurn(text="plan follows"),
        ScriptedTurn(tool_calls=(
            ToolCall(name="read_file", arguments={"path": "app.py"}, id="call-1"),
        )),
        ScriptedTurn(structured={"objective": "obj", "goals": [{
            "goal_id": "G1", "title": "t", "kind": "fix",
            "acceptance_criteria": ["a"], "verification_criteria": ["v"],
        }]}),
    ])
    first = asyncio.run(client.generate([Message(role="user", content="go")]))
    second = asyncio.run(client.generate([Message(role="user", content="tools?")]))
    third = asyncio.run(client.generate(
        [Message(role="user", content="json?")],
        response_schema=ResponseSchema(
            name="goal_graph", schema={"type": "object"},
        ),
    ))

    assert first.text == "plan follows"
    assert second.tool_calls[0].name == "read_file"
    assert third.structured["goals"][0]["kind"] == "fix"
    # every call was recorded with its arguments for offline reproduction
    assert len(client.calls) == 3
    assert client.calls[1].tools == ()


def test_fake_reproduces_failures_deterministically():
    from harness.model.errors import ModelError

    class Boom(ModelError):
        pass

    client = FakeModelClient(script=[
        ScriptedTurn(error=Boom("rate limited", provider="fake")),
        ScriptedTurn(text="recovered"),
    ])
    with pytest.raises(ModelError):
        asyncio.run(client.generate([Message(role="user", content="a")]))
    recovered = asyncio.run(
        client.generate([Message(role="user", content="a")])
    )
    assert recovered.text == "recovered"


def test_script_exhaustion_is_loud_not_hanging():
    client = FakeModelClient(script=[ScriptedTurn(text="only one")])
    asyncio.run(client.generate([Message(role="user", content="a")]))
    with pytest.raises(Exception) as excinfo:
        asyncio.run(client.generate([Message(role="user", content="b")]))
    assert "exhausted" in str(excinfo.value)
