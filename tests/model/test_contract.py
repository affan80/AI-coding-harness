"""M1.16 — async ``ModelClient`` contract and runtime capability discovery."""

import asyncio

import pytest

from harness.model import (
    CapabilityNotSupportedError,
    FakeModelClient,
    Message,
    ModelCapabilities,
    ModelClient,
    ModelResponse,
    ProviderName,
    ResponseSchema,
    ScriptedTurn,
    ToolSpec,
    build_model_client,
    resolve_capabilities,
    settings_from_env,
)

# One client, seven logical roles (PRD §9): roles differ by prompt and
# permissions, never by runtime.
ROLE_PROMPTS = {
    "intent": "Extract goals, constraints, and acceptance criteria.",
    "repository": "Profile the repository and rank candidate areas.",
    "planner": "Turn goals and evidence into dependency-aware steps.",
    "executor": "Apply the current approved plan step through tools.",
    "verification": "Select and interpret deterministic checks.",
    "recovery": "Diagnose the failing check; propose a bounded repair.",
    "audit": "Search the affected scope for reproducible defects.",
}


def test_all_agent_roles_reuse_one_client() -> None:
    client = FakeModelClient(script=[ScriptedTurn(text=f"ok: {role}") for role in ROLE_PROMPTS])
    for role, prompt in ROLE_PROMPTS.items():
        response = asyncio.run(
            client.generate(
                [Message(role="system", content=prompt), Message(role="user", content="objective")]
            )
        )
        assert isinstance(response, ModelResponse)
        assert response.text == f"ok: {role}"
        assert response.provider == "fake"
    assert len(client.calls) == len(ROLE_PROMPTS)


def test_client_implementations_satisfy_the_protocol(stub_transport, make_openai_client) -> None:
    assert isinstance(FakeModelClient(), ModelClient)
    built = build_model_client(env={})
    assert isinstance(built, ModelClient)
    # Default configuration stays offline-safe: no credentials, no network.
    assert isinstance(built, FakeModelClient)
    assert isinstance(make_openai_client(stub_transport()), ModelClient)


def test_capabilities_are_queryable_at_runtime() -> None:
    capabilities = FakeModelClient().capabilities()
    assert capabilities.provider == "fake"
    assert capabilities.context_window_tokens > 0
    assert capabilities.max_output_tokens > 0
    assert capabilities.tool_calling is True
    assert capabilities.structured_output is True


def test_capability_registry_resolves_known_prefix_and_unknown_models() -> None:
    gpt4o = resolve_capabilities(ProviderName.OPENAI, "gpt-4o")
    assert (gpt4o.context_window_tokens, gpt4o.max_output_tokens) == (128_000, 16_384)

    dated = resolve_capabilities(ProviderName.ANTHROPIC, "claude-sonnet-4-5-20250929")
    assert (dated.context_window_tokens, dated.max_output_tokens) == (200_000, 64_000)
    # Honest capability surface: no native JSON-schema mode on this provider.
    assert dated.structured_output is False
    assert dated.tool_calling is True

    unknown = resolve_capabilities(ProviderName.OPENAI, "brand-new-model-9")
    assert (unknown.context_window_tokens, unknown.max_output_tokens) == (32_768, 4_096)


def test_explicit_overrides_win_over_the_registry() -> None:
    capabilities = resolve_capabilities(
        ProviderName.OPENAI,
        "brand-new-model-9",
        context_window_tokens=400_000,
        max_output_tokens=32_000,
    )
    assert (capabilities.context_window_tokens, capabilities.max_output_tokens) == (400_000, 32_000)

    settings = settings_from_env(
        {
            "HARNESS_MODEL_PROVIDER": "fake",
            "HARNESS_MODEL_CONTEXT_WINDOW_TOKENS": "256000",
            "HARNESS_MODEL_MAX_OUTPUT_TOKENS": "8192",
        }
    )
    assert build_model_client(settings).capabilities().context_window_tokens == 256_000


def test_unsupported_capabilities_fail_structured_before_any_network_call(
    stub_transport, make_openai_client
) -> None:
    transport = stub_transport()
    limited = ModelCapabilities(
        provider="openai",
        model="text-only",
        context_window_tokens=8_000,
        max_output_tokens=1_000,
        tool_calling=False,
        structured_output=False,
    )
    client = make_openai_client(transport, model="text-only", capabilities=limited)

    with pytest.raises(CapabilityNotSupportedError) as tool_error:
        asyncio.run(
            client.generate(
                [Message(role="user", content="hi")],
                tools=[ToolSpec(name="read_file", description="Read a file")],
            )
        )
    assert tool_error.value.retryable is False
    assert tool_error.value.provider == "openai"

    with pytest.raises(CapabilityNotSupportedError) as schema_error:
        asyncio.run(
            client.generate(
                [Message(role="user", content="hi")],
                response_schema=ResponseSchema(name="Plan", schema={"type": "object"}),
            )
        )
    assert schema_error.value.retryable is False

    # Guards fire before dispatch: the transport must never have been called.
    assert transport.requests == []
