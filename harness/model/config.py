"""Environment-driven model configuration and client factory (issue M1.18).

Environment variables (all optional; defaults keep the harness offline-safe):

    HARNESS_MODEL_PROVIDER           openai | anthropic | fake   (default: fake)
    HARNESS_MODEL                    model id for the chosen provider
    HARNESS_MODEL_TIMEOUT_SECONDS    per-call timeout (default: 60)
    HARNESS_MODEL_BASE_URL           provider API base override
    HARNESS_MODEL_CONTEXT_WINDOW_TOKENS   capability override
    HARNESS_MODEL_MAX_OUTPUT_TOKENS       capability override
    HARNESS_OPENAI_API_KEY           (or OPENAI_API_KEY)
    HARNESS_ANTHROPIC_API_KEY        (or ANTHROPIC_API_KEY)

Values are parsed into immutable settings; credentials are wrapped in
:class:`Secret` so they can never surface through ``repr``, logs, or
evidence. Agent prompts and permissions are deliberately absent here —
they belong to the agent layer (PRD §9).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from harness.model.errors import ModelConfigurationError
from harness.model.redaction import Secret
from harness.model.types import ModelCapabilities, ModelClient

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness.model.providers.http import JsonTransport


class ProviderName(StrEnum):
    """Providers the harness can wire a shared client to."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    FAKE = "fake"


@dataclass(frozen=True, slots=True)
class _ModelLimits:
    context_window_tokens: int
    max_output_tokens: int


# Best-effort published limits; the registry is a convenience, not an oracle.
# Unknown models fall back to conservative defaults, overridable via
# HARNESS_MODEL_CONTEXT_WINDOW_TOKENS / HARNESS_MODEL_MAX_OUTPUT_TOKENS.
_KNOWN_MODELS: dict[str, _ModelLimits] = {
    "gpt-4o": _ModelLimits(128_000, 16_384),
    "gpt-4o-mini": _ModelLimits(128_000, 16_384),
    "gpt-4.1": _ModelLimits(1_047_576, 32_768),
    "gpt-4.1-mini": _ModelLimits(1_047_576, 32_768),
    "o3": _ModelLimits(200_000, 100_000),
    "o4-mini": _ModelLimits(200_000, 100_000),
    "claude-opus-4-1": _ModelLimits(200_000, 32_000),
    "claude-sonnet-4-5": _ModelLimits(200_000, 64_000),
    "claude-haiku-4-5": _ModelLimits(200_000, 64_000),
    "claude-3-7-sonnet": _ModelLimits(200_000, 64_000),
    "claude-3-5-sonnet": _ModelLimits(200_000, 8_192),
    "claude-3-5-haiku": _ModelLimits(200_000, 8_192),
}

_UNKNOWN_MODEL_LIMITS = _ModelLimits(32_768, 4_096)

# Providers without a native JSON-schema response mode must refuse
# ``response_schema`` requests as a structured failure instead of guessing.
_PROVIDER_STRUCTURED_OUTPUT: dict[ProviderName, bool] = {
    ProviderName.OPENAI: True,
    ProviderName.ANTHROPIC: False,
    ProviderName.FAKE: True,
}

_CREDENTIAL_VARS: dict[ProviderName, tuple[str, ...]] = {
    ProviderName.OPENAI: ("HARNESS_OPENAI_API_KEY", "OPENAI_API_KEY"),
    ProviderName.ANTHROPIC: ("HARNESS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    ProviderName.FAKE: (),
}

_BASE_URL_FALLBACK_VARS: dict[ProviderName, str] = {
    ProviderName.OPENAI: "OPENAI_BASE_URL",
    ProviderName.ANTHROPIC: "ANTHROPIC_BASE_URL",
}

_DEFAULT_MODELS: dict[ProviderName, str] = {
    ProviderName.OPENAI: "gpt-4o-mini",
    ProviderName.ANTHROPIC: "claude-sonnet-4-5",
    ProviderName.FAKE: "fake-model",
}

_DEFAULT_TIMEOUT_SECONDS = 60.0


def resolve_capabilities(
    provider: ProviderName | str,
    model: str,
    *,
    context_window_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> ModelCapabilities:
    """Best-effort capability discovery: registry, prefix match, safe defaults.

    Dated snapshots (``claude-sonnet-4-5-20250929``) resolve through prefix
    matching; unknown models get conservative limits so budgets fail closed.
    """
    provider = ProviderName(provider.value if isinstance(provider, ProviderName) else str(provider))
    limits = _lookup_limits(model)
    context = (
        context_window_tokens
        if context_window_tokens is not None
        else limits.context_window_tokens
    )
    output = max_output_tokens if max_output_tokens is not None else limits.max_output_tokens
    return ModelCapabilities(
        provider=provider.value,
        model=model,
        context_window_tokens=context,
        max_output_tokens=output,
        tool_calling=True,
        structured_output=_PROVIDER_STRUCTURED_OUTPUT[provider],
    )


def _lookup_limits(model: str) -> _ModelLimits:
    if model in _KNOWN_MODELS:
        return _KNOWN_MODELS[model]
    prefixes = [prefix for prefix in _KNOWN_MODELS if model.startswith(prefix)]
    if prefixes:
        return _KNOWN_MODELS[max(prefixes, key=len)]
    return _UNKNOWN_MODEL_LIMITS


@dataclass(frozen=True, slots=True)
class ModelClientSettings:
    """Immutable wiring for one shared model client."""

    provider: ProviderName
    model: str
    api_key: Secret | None = None
    base_url: str | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None

    def capabilities(self) -> ModelCapabilities:
        return resolve_capabilities(
            self.provider,
            self.model,
            context_window_tokens=self.context_window_tokens,
            max_output_tokens=self.max_output_tokens,
        )

    def secret_values(self) -> tuple[str, ...]:
        if self.api_key is None or self.api_key.is_empty:
            return ()
        return (self.api_key.reveal(),)


def settings_from_env(env: Mapping[str, str] | None = None) -> ModelClientSettings:
    """Parse settings from an environment mapping (defaults to ``os.environ``)."""
    env = os.environ if env is None else env

    raw_provider = (env.get("HARNESS_MODEL_PROVIDER") or ProviderName.FAKE.value).strip().lower()
    try:
        provider = ProviderName(raw_provider)
    except ValueError:
        allowed = ", ".join(p.value for p in ProviderName)
        raise ModelConfigurationError(
            f"unknown HARNESS_MODEL_PROVIDER {raw_provider!r}; expected one of: {allowed}"
        ) from None

    model = (env.get("HARNESS_MODEL") or "").strip() or _DEFAULT_MODELS[provider]

    api_key = _read_credential(env, provider)
    if provider is not ProviderName.FAKE and api_key is None:
        var_names = " or ".join(_CREDENTIAL_VARS[provider])
        raise ModelConfigurationError(
            f"missing credential for provider '{provider.value}': set {var_names} "
            "(the value is read once and never logged)"
        )

    base_url = (env.get("HARNESS_MODEL_BASE_URL") or "").strip()
    if not base_url:
        fallback_var = _BASE_URL_FALLBACK_VARS.get(provider)
        base_url = (env.get(fallback_var) or "").strip() if fallback_var else ""

    timeout = _parse_positive_float(
        env.get("HARNESS_MODEL_TIMEOUT_SECONDS"),
        "HARNESS_MODEL_TIMEOUT_SECONDS",
        _DEFAULT_TIMEOUT_SECONDS,
    )
    context_override = _parse_positive_int(
        env.get("HARNESS_MODEL_CONTEXT_WINDOW_TOKENS"), "HARNESS_MODEL_CONTEXT_WINDOW_TOKENS"
    )
    output_override = _parse_positive_int(
        env.get("HARNESS_MODEL_MAX_OUTPUT_TOKENS"), "HARNESS_MODEL_MAX_OUTPUT_TOKENS"
    )

    return ModelClientSettings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url or None,
        timeout_seconds=timeout,
        context_window_tokens=context_override,
        max_output_tokens=output_override,
    )


def _read_credential(env: Mapping[str, str], provider: ProviderName) -> Secret | None:
    for var in _CREDENTIAL_VARS[provider]:
        value = env.get(var)
        if value and value.strip():
            return Secret(value.strip())
    return None


def _parse_positive_int(raw: str | None, var: str) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        raise ModelConfigurationError(f"{var} must be an integer, got {raw.strip()!r}") from None
    if value <= 0:
        raise ModelConfigurationError(f"{var} must be positive, got {value}")
    return value


def _parse_positive_float(raw: str | None, var: str, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ModelConfigurationError(f"{var} must be a number, got {raw.strip()!r}") from None
    if value <= 0:
        raise ModelConfigurationError(f"{var} must be positive, got {value}")
    return value


def build_model_client(
    settings: ModelClientSettings | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: JsonTransport | None = None,
) -> ModelClient:
    """Create the one shared model client; ``fake`` keeps local runs offline-safe.

    ``transport`` injects an alternative HTTP layer (used by tests); real
    deployments use the standard-library transport.
    """
    if settings is None:
        settings = settings_from_env(env)
    if settings.provider is ProviderName.FAKE:
        from harness.model.fake import FakeModelClient

        client: ModelClient = FakeModelClient(capabilities=settings.capabilities())
        return client

    from harness.model.providers.anthropic import AnthropicClient
    from harness.model.providers.openai_compatible import OpenAICompatibleClient

    if settings.provider is ProviderName.OPENAI:
        return OpenAICompatibleClient(settings, transport=transport)
    return AnthropicClient(settings, transport=transport)
