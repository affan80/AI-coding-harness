"""M1.18 — environment configuration and secret containment.

Credentials load from environment variables, never appear in ``repr``/logs/
evidence, and configuration problems surface as structured failures.
"""

import json

import pytest

from harness.model import (
    AnthropicClient,
    FakeModelClient,
    ModelClientSettings,
    ModelConfigurationError,
    OpenAICompatibleClient,
    ProviderName,
    Redactor,
    Secret,
    build_model_client,
    settings_from_env,
)


def test_defaults_keep_the_harness_offline_safe() -> None:
    settings = settings_from_env({})
    assert settings.provider is ProviderName.FAKE
    assert settings.model == "fake-model"
    assert settings.api_key is None
    assert isinstance(build_model_client(settings), FakeModelClient)


def test_openai_settings_parse_from_environment() -> None:
    env = {
        "HARNESS_MODEL_PROVIDER": "openai",
        "HARNESS_MODEL": "gpt-4o",
        "HARNESS_OPENAI_API_KEY": "sk-env-key-1234567890",
        "OPENAI_BASE_URL": "https://proxy.internal/v1",
    }
    settings = settings_from_env(env)
    assert settings.provider is ProviderName.OPENAI
    assert settings.model == "gpt-4o"
    assert settings.api_key is not None
    assert settings.api_key.reveal() == "sk-env-key-1234567890"
    assert settings.base_url == "https://proxy.internal/v1"
    assert isinstance(build_model_client(settings), OpenAICompatibleClient)


def test_anthropic_settings_parse_case_insensitively_with_fallback_var() -> None:
    settings = settings_from_env(
        {"HARNESS_MODEL_PROVIDER": "ANTHROPIC", "ANTHROPIC_API_KEY": "ak-live-1234567890"}
    )
    assert settings.provider is ProviderName.ANTHROPIC
    assert settings.api_key is not None
    assert isinstance(build_model_client(settings), AnthropicClient)


def test_missing_credentials_are_structured_configuration_errors() -> None:
    with pytest.raises(ModelConfigurationError) as missing:
        settings_from_env({"HARNESS_MODEL_PROVIDER": "openai", "HARNESS_MODEL": "gpt-4o"})
    assert "OPENAI_API_KEY" in str(missing.value)
    assert missing.value.retryable is False


def test_blank_credentials_are_treated_as_missing() -> None:
    with pytest.raises(ModelConfigurationError):
        settings_from_env({"HARNESS_MODEL_PROVIDER": "openai", "OPENAI_API_KEY": "   "})


def test_unknown_providers_and_bad_numbers_are_configuration_errors() -> None:
    with pytest.raises(ModelConfigurationError) as unknown:
        settings_from_env({"HARNESS_MODEL_PROVIDER": "gemini"})
    assert "openai" in str(unknown.value)
    assert "anthropic" in str(unknown.value)

    with pytest.raises(ModelConfigurationError):
        settings_from_env({"HARNESS_MODEL_TIMEOUT_SECONDS": "soon"})
    with pytest.raises(ModelConfigurationError):
        settings_from_env({"HARNESS_MODEL_CONTEXT_WINDOW_TOKENS": "-5"})
    with pytest.raises(ModelConfigurationError):
        settings_from_env({"HARNESS_MODEL_MAX_OUTPUT_TOKENS": "zero"})


def test_secret_never_reveals_itself_through_str_or_repr() -> None:
    secret = Secret("sk-super-secret-value-9876")
    assert str(secret) == "[REDACTED]"
    assert repr(secret) == "[REDACTED]"
    assert secret.reveal() == "sk-super-secret-value-9876"
    assert secret.is_empty is False
    assert "sk-super-secret-value-9876" not in json.dumps({"log": [str(secret), repr(secret)]})


def test_settings_and_errors_repr_contains_no_credential_material() -> None:
    settings = ModelClientSettings(
        provider=ProviderName.OPENAI,
        model="gpt-4o",
        api_key=Secret("sk-hidden-credential-99"),
    )
    assert "sk-hidden-credential-99" not in repr(settings)
    assert settings.secret_values() == ("sk-hidden-credential-99",)


def test_redactor_scrubs_secrets_from_text_and_evidence_structures() -> None:
    key = "sk-live-a1b2c3d4e5f6"
    redactor = Redactor([key])

    assert redactor.text(f"Authorization: Bearer {key} failed") == (
        "Authorization: Bearer [REDACTED] failed"
    )
    evidence = {"summary": f"call used {key}", "nested": {"log": [f"key={key}"]}, "tokens": 42}
    scrubbed = redactor.details(evidence)
    assert key not in json.dumps(scrubbed)
    assert scrubbed["tokens"] == 42

    # Longest-first replacement: a short secret inside a longer one must not
    # produce a half-redacted mangle like "[REDACTED]-but-longer".
    overlapping = Redactor(["key-short", "key-short-but-longer"])
    assert overlapping.text("value key-short-but-longer end") == "value [REDACTED] end"
    assert overlapping.text("value key-short end") == "value [REDACTED] end"


def test_redactor_ignores_values_too_short_to_redact_safely() -> None:
    redactor = Redactor(["ok", "ab"])
    assert redactor.text("ok ab") == "ok ab"
