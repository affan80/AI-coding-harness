"""M1.18 — the deterministic fake client reproduces responses offline.

Same normalisation semantics as real adapters (finish reasons, structured
parsing, call recording), zero network, zero wall-clock latency.
"""

import asyncio
import json

import pytest

from harness.model import (
    FakeModelClient,
    FakeScriptExhaustedError,
    MalformedModelError,
    Message,
    ModelCapabilities,
    RateLimitError,
    ResponseSchema,
    ScriptedTurn,
    ToolCall,
    ToolSpec,
)

PLAN_SCHEMA = ResponseSchema(name="plan", schema={"type": "object"})


def test_scripted_turns_are_fully_deterministic() -> None:
    script = [ScriptedTurn(text="planned 3 steps")]

    def run(client: FakeModelClient):
        return asyncio.run(client.generate([Message(role="user", content="plan the fix")]))

    first = run(FakeModelClient(script))
    second = run(FakeModelClient(script))
    assert first == second
    assert first.text == "planned 3 steps"
    assert first.latency_ms == 0
    assert first.usage.total_tokens > 0
    assert first.provider == "fake"


def test_scripted_tool_calls_drive_finish_reason_and_usage() -> None:
    turn = ScriptedTurn(
        tool_calls=(ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"}),)
    )
    client = FakeModelClient([turn])
    response = asyncio.run(
        client.generate(
            [Message(role="user", content="go")],
            tools=[
                ToolSpec(name="read_file", description="Read a file", parameters={"type": "object"})
            ],
        )
    )
    assert response.finish_reason == "tool_calls"
    assert response.text is None
    assert response.tool_calls[0].arguments == {"path": "a.py"}
    # Input accounting includes the tool schema, output accounting the call.
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0


def test_structured_output_has_two_deterministic_sources() -> None:
    client = FakeModelClient(
        [
            ScriptedTurn(structured={"steps": ["S1"]}),
            ScriptedTurn(text=json.dumps({"steps": ["S2"]})),
        ]
    )
    first = asyncio.run(
        client.generate([Message(role="user", content="plan")], response_schema=PLAN_SCHEMA)
    )
    second = asyncio.run(
        client.generate([Message(role="user", content="plan")], response_schema=PLAN_SCHEMA)
    )
    assert first.structured == {"steps": ["S1"]}
    assert second.structured == {"steps": ["S2"]}


def test_fake_mirrors_real_malformed_structured_output_semantics() -> None:
    client = FakeModelClient([ScriptedTurn(text="definitely not json")])
    with pytest.raises(MalformedModelError) as excinfo:
        asyncio.run(
            client.generate([Message(role="user", content="plan")], response_schema=PLAN_SCHEMA)
        )
    assert excinfo.value.retryable is True


def test_scripted_errors_are_raised_and_the_script_continues() -> None:
    client = FakeModelClient(
        [
            ScriptedTurn(
                error=RateLimitError("slow down", provider="fake", retry_after_seconds=3.0)
            ),
            ScriptedTurn(text="recovered"),
        ]
    )
    with pytest.raises(RateLimitError) as excinfo:
        asyncio.run(client.generate([Message(role="user", content="x")]))
    assert excinfo.value.retry_after_seconds == 3.0

    response = asyncio.run(client.generate([Message(role="user", content="x")]))
    assert response.text == "recovered"
    # The failing call is still recorded for assertions.
    assert len(client.calls) == 2


def test_script_exhaustion_fails_loudly() -> None:
    client = FakeModelClient()
    with pytest.raises(FakeScriptExhaustedError):
        asyncio.run(client.generate([Message(role="user", content="x")]))


def test_recorded_calls_capture_every_argument() -> None:
    client = FakeModelClient([ScriptedTurn(text=json.dumps({"ok": True}))])
    messages = [Message(role="system", content="policy"), Message(role="user", content="task")]
    tools = [ToolSpec(name="search_text", description="Search")]
    asyncio.run(client.generate(messages, tools=tools, response_schema=PLAN_SCHEMA))

    (recorded,) = client.calls
    assert recorded.messages == tuple(messages)
    assert recorded.tools == tuple(tools)
    assert recorded.response_schema is PLAN_SCHEMA


def test_fake_accepts_custom_capabilities() -> None:
    capabilities = ModelCapabilities(
        provider="fake",
        model="mini-fake",
        context_window_tokens=1_000,
        max_output_tokens=100,
    )
    client = FakeModelClient(capabilities=capabilities)
    assert client.capabilities() is capabilities
    assert client.capabilities().model == "mini-fake"
