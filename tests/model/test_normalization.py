"""M1.17 — responses, usage, latency, and provider failures normalise to one shape.

Equivalent payloads from different providers must produce the same harness
result types, and every failure boundary must raise an explicit, correctly
classified ``ModelError`` — never a provider-specific exception.
"""

import asyncio
import json

import pytest

from harness.model import (
    AuthenticationError,
    CapabilityNotSupportedError,
    ContextWindowExceededError,
    InvalidRequestError,
    MalformedModelError,
    Message,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    ResponseSchema,
    ToolCall,
    ToolSpec,
    Usage,
)

READ_FILE = ToolSpec(name="read_file", description="Read a file", parameters={"type": "object"})
SEARCH_TEXT = ToolSpec(name="search_text", description="Search text")

OPENAI_TOOL_CALL_RESPONSE = {
    "id": "chatcmpl-1",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "Inspecting the auth module.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "app/auth.py", "limit": 40}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "search_text",
                            "arguments": '{"query": "def authenticate"}',
                        },
                    },
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
}

ANTHROPIC_TOOL_USE_RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5",
    "content": [
        {"type": "text", "text": "Inspecting the auth module."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "read_file",
            "input": {"path": "app/auth.py", "limit": 40},
        },
        {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "search_text",
            "input": {"query": "def authenticate"},
        },
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 120, "output_tokens": 30},
}


def test_equivalent_provider_payloads_normalise_to_one_shape(
    stub_transport, make_openai_client, make_anthropic_client
) -> None:
    openai = make_openai_client(stub_transport(payload=OPENAI_TOOL_CALL_RESPONSE))
    anthropic = make_anthropic_client(stub_transport(payload=ANTHROPIC_TOOL_USE_RESPONSE))
    messages = [Message(role="user", content="Inspect the auth module.")]

    from_openai = asyncio.run(openai.generate(messages, tools=[READ_FILE, SEARCH_TEXT]))
    from_anthropic = asyncio.run(anthropic.generate(messages, tools=[READ_FILE, SEARCH_TEXT]))

    assert from_openai.text == from_anthropic.text == "Inspecting the auth module."
    assert from_openai.finish_reason == from_anthropic.finish_reason == "tool_calls"
    assert from_openai.usage == from_anthropic.usage == Usage(input_tokens=120, output_tokens=30)
    assert [call.name for call in from_openai.tool_calls] == ["read_file", "search_text"]
    assert [call.name for call in from_anthropic.tool_calls] == ["read_file", "search_text"]
    assert from_openai.tool_calls[0].arguments == {"path": "app/auth.py", "limit": 40}
    assert from_anthropic.tool_calls[0].arguments == {"path": "app/auth.py", "limit": 40}
    assert from_openai.provider == "openai"
    assert from_anthropic.provider == "anthropic"
    assert from_openai.latency_ms >= 0 and from_anthropic.latency_ms >= 0


def test_plain_text_responses_normalise_to_one_shape(
    stub_transport, make_openai_client, make_anthropic_client
) -> None:
    openai = make_openai_client(
        stub_transport(
            payload={
                "choices": [{"finish_reason": "stop", "message": {"content": "Planned."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        )
    )
    anthropic = make_anthropic_client(
        stub_transport(
            payload={
                "content": [{"type": "text", "text": "Planned."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        )
    )
    messages = [Message(role="user", content="Plan the fix.")]

    from_openai = asyncio.run(openai.generate(messages))
    from_anthropic = asyncio.run(anthropic.generate(messages))

    assert from_openai.text == from_anthropic.text == "Planned."
    assert from_openai.finish_reason == from_anthropic.finish_reason == "stop"
    assert from_openai.usage == from_anthropic.usage == Usage(input_tokens=10, output_tokens=2)


def test_conversation_history_maps_to_each_provider_wire_format(
    stub_transport, make_openai_client, make_anthropic_client
) -> None:
    messages = [
        Message(role="system", content="You are the planner."),
        Message(role="user", content="Plan the fix."),
        Message(
            role="assistant",
            content="Checking the file.",
            tool_calls=(ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"}),),
        ),
        Message(role="tool", content="def fix(): ...", tool_call_id="call_1"),
    ]

    openai_transport = stub_transport(
        payload={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
    )
    asyncio.run(make_openai_client(openai_transport).generate(messages))
    openai_payload = openai_transport.requests[0].payload
    assert openai_transport.requests[0].url.endswith("/chat/completions")
    assert openai_payload["model"] == "gpt-4o"
    assert openai_payload["messages"] == [
        {"role": "system", "content": "You are the planner."},
        {"role": "user", "content": "Plan the fix."},
        {
            "role": "assistant",
            "content": "Checking the file.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "def fix(): ..."},
    ]

    anthropic_transport = stub_transport(payload={"content": [{"type": "text", "text": "ok"}]})
    asyncio.run(make_anthropic_client(anthropic_transport).generate(messages))
    anthropic_payload = anthropic_transport.requests[0].payload
    assert anthropic_transport.requests[0].url.endswith("/v1/messages")
    assert anthropic_payload["system"] == "You are the planner."
    assert anthropic_payload["max_tokens"] > 0
    assert anthropic_payload["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Plan the fix."}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking the file."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "def fix(): ..."}
            ],
        },
    ]


def test_provider_authentication_headers_are_wired(
    stub_transport, make_openai_client, make_anthropic_client, api_key
) -> None:
    openai_transport = stub_transport(
        payload={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
    )
    anthropic_transport = stub_transport(payload={"content": [{"type": "text", "text": "ok"}]})
    asyncio.run(make_openai_client(openai_transport).generate([Message(role="user", content="x")]))
    asyncio.run(
        make_anthropic_client(anthropic_transport).generate([Message(role="user", content="x")])
    )
    assert openai_transport.requests[0].headers["Authorization"] == f"Bearer {api_key}"
    assert anthropic_transport.requests[0].headers["x-api-key"] == api_key


def test_rate_limits_become_retryable_errors_with_retry_after(
    stub_transport, make_openai_client, make_anthropic_client
) -> None:
    openai_transport = stub_transport(
        status=429,
        headers={"retry-after": "7"},
        payload={"error": {"message": "Rate limit reached", "type": "requests"}},
    )
    with pytest.raises(RateLimitError) as openai_error:
        asyncio.run(
            make_openai_client(openai_transport).generate([Message(role="user", content="x")])
        )
    assert openai_error.value.retryable is True
    assert openai_error.value.retry_after_seconds == 7.0
    assert openai_error.value.status_code == 429
    assert openai_error.value.provider == "openai"
    assert openai_error.value.to_dict()["retryable"] is True

    anthropic_transport = stub_transport(
        status=429,
        headers={"retry-after": "12"},
        payload={"type": "error", "error": {"type": "rate_limit_error", "message": "Slow down"}},
    )
    with pytest.raises(RateLimitError) as anthropic_error:
        asyncio.run(
            make_anthropic_client(anthropic_transport).generate([Message(role="user", content="x")])
        )
    assert anthropic_error.value.retryable is True
    assert anthropic_error.value.retry_after_seconds == 12.0


def test_authentication_failures_are_not_retryable_and_never_leak_the_key(
    stub_transport, make_openai_client, api_key
) -> None:
    transport = stub_transport(
        status=401,
        payload={"error": {"message": f"Invalid API key provided: {api_key}"}},
    )
    with pytest.raises(AuthenticationError) as excinfo:
        asyncio.run(make_openai_client(transport).generate([Message(role="user", content="x")]))
    assert excinfo.value.retryable is False
    assert api_key not in str(excinfo.value)
    assert api_key not in json.dumps(excinfo.value.to_dict())


def test_server_errors_and_timeouts_are_retryable_structured_failures(
    stub_transport, make_openai_client
) -> None:
    server_down = stub_transport(status=503, payload={"error": {"message": "upstream overloaded"}})
    with pytest.raises(ProviderUnavailableError) as unavailable:
        asyncio.run(make_openai_client(server_down).generate([Message(role="user", content="x")]))
    assert unavailable.value.retryable is True
    assert unavailable.value.status_code == 503

    timeout = stub_transport(error=TimeoutError("boom"))
    with pytest.raises(ProviderTimeoutError) as timed_out:
        asyncio.run(make_openai_client(timeout).generate([Message(role="user", content="x")]))
    assert timed_out.value.retryable is True

    broken = stub_transport(error=RuntimeError("no route to host"))
    with pytest.raises(ProviderUnavailableError) as transport_failure:
        asyncio.run(make_openai_client(broken).generate([Message(role="user", content="x")]))
    assert transport_failure.value.retryable is True


def test_malformed_tool_arguments_become_retryable_malformed_errors(
    stub_transport, make_openai_client
) -> None:
    transport = stub_transport(
        payload={
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{not json"},
                            }
                        ],
                    },
                }
            ]
        }
    )
    with pytest.raises(MalformedModelError) as excinfo:
        asyncio.run(make_openai_client(transport).generate([Message(role="user", content="x")]))
    assert excinfo.value.retryable is True
    assert "{not json" in str(excinfo.value)


def test_structured_output_is_parsed_only_when_a_schema_is_requested(
    stub_transport, make_openai_client
) -> None:
    plan = json.dumps({"steps": ["S1", "S2"]})
    transport = stub_transport(
        payload={"choices": [{"finish_reason": "stop", "message": {"content": plan}}]}
    )
    response = asyncio.run(
        make_openai_client(transport).generate(
            [Message(role="user", content="plan")],
            response_schema=ResponseSchema(name="plan", schema={"type": "object"}),
        )
    )
    assert response.structured == {"steps": ["S1", "S2"]}
    assert response.text == plan
    requested = transport.requests[0].payload["response_format"]
    assert requested["type"] == "json_schema"
    assert requested["json_schema"]["name"] == "plan"

    unstructured = stub_transport(
        payload={"choices": [{"finish_reason": "stop", "message": {"content": plan}}]}
    )
    plain = asyncio.run(
        make_openai_client(unstructured).generate([Message(role="user", content="plan")])
    )
    assert plain.structured is None


def test_unparseable_structured_output_is_a_retryable_malformed_error(
    stub_transport, make_openai_client
) -> None:
    transport = stub_transport(
        payload={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}
    )
    with pytest.raises(MalformedModelError) as excinfo:
        asyncio.run(
            make_openai_client(transport).generate(
                [Message(role="user", content="plan")],
                response_schema=ResponseSchema(name="plan", schema={"type": "object"}),
            )
        )
    assert excinfo.value.retryable is True


def test_anthropic_refuses_structured_output_as_a_structured_failure(
    stub_transport, make_anthropic_client
) -> None:
    transport = stub_transport(payload={"content": []})
    with pytest.raises(CapabilityNotSupportedError) as excinfo:
        asyncio.run(
            make_anthropic_client(transport).generate(
                [Message(role="user", content="plan")],
                response_schema=ResponseSchema(name="plan", schema={"type": "object"}),
            )
        )
    assert excinfo.value.retryable is False
    assert transport.requests == []


def test_preflight_context_guard_blocks_oversized_requests_before_dispatch(
    stub_transport, make_openai_client
) -> None:
    transport = stub_transport(payload={"choices": []})
    client = make_openai_client(transport, context_window_tokens=100, max_output_tokens=50)
    with pytest.raises(ContextWindowExceededError) as excinfo:
        asyncio.run(
            client.generate([Message(role="user", content="word " * 400)], tools=[READ_FILE])
        )
    assert excinfo.value.retryable is False
    details = excinfo.value.details
    assert details["estimated_input_tokens"] > 50
    assert details["context_window_tokens"] == 100
    assert transport.requests == []


def test_structurally_invalid_requests_fail_before_dispatch(
    stub_transport, make_openai_client, make_anthropic_client
) -> None:
    transport = stub_transport(payload={"choices": []})
    with pytest.raises(InvalidRequestError) as empty:
        asyncio.run(make_openai_client(transport).generate([]))
    assert empty.value.retryable is False

    with pytest.raises(InvalidRequestError) as unkeyed:
        asyncio.run(
            make_openai_client(transport).generate([Message(role="tool", content="result")])
        )
    assert "tool_call_id" in str(unkeyed.value)

    with pytest.raises(InvalidRequestError) as system_only:
        asyncio.run(
            make_anthropic_client(transport).generate([Message(role="system", content="policy")])
        )
    assert system_only.value.retryable is False

    assert transport.requests == []
