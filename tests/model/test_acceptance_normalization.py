"""M1.17 acceptance evidence: normalization and the explicit retryable matrix.

The normalization landed with the model-client workstream (equivalent
provider payloads, usage, latency, provider failures); this module maps
issue #37's acceptance bullets 1:1 and pins the full retryable matrix plus
the structured, log-safe error payload in one place.
"""

import pytest

from harness.model import (
    AuthenticationError,
    CapabilityNotSupportedError,
    ContextWindowExceededError,
    InvalidRequestError,
    MalformedModelError,
    ModelError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)

RETRYABLE_ERRORS = (
    RateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    MalformedModelError,
)
NON_RETRYABLE_ERRORS = (
    AuthenticationError,
    InvalidRequestError,
    ContextWindowExceededError,
    CapabilityNotSupportedError,
)


@pytest.mark.parametrize("error_cls", RETRYABLE_ERRORS)
def test_retryable_failures_are_explicit(error_cls: type[ModelError]) -> None:
    error = error_cls("upstream said no", provider="openai", model="gpt-x")
    assert error.retryable is True
    payload = error.to_dict()
    assert payload["retryable"] is True
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-x"


@pytest.mark.parametrize("error_cls", NON_RETRYABLE_ERRORS)
def test_non_retryable_failures_are_explicit(error_cls: type[ModelError]) -> None:
    error = error_cls("request is wrong", provider="anthropic", model="claude")
    assert error.retryable is False
    assert error.to_dict()["retryable"] is False


def test_rate_limit_surfaces_retry_after_hint() -> None:
    error = RateLimitError(
        "slow down", provider="openai", model="gpt-x", retry_after_seconds=12.5,
    )
    assert error.retry_after_seconds == 12.5
    assert error.to_dict()["retry_after_seconds"] == 12.5
    # callers budget the wait from the structured field, never the message
    assert "12.5" not in error.message


def test_status_codes_travel_in_the_structured_payload() -> None:
    error = AuthenticationError(
        "bad credentials", provider="openai", model="gpt-x", status_code=401,
    )
    assert error.status_code == 401
    payload = error.to_dict()
    assert payload["status_code"] == 401
    # log-safe: the payload is plain JSON types
    import json

    assert json.loads(json.dumps(payload)) == payload


def test_every_model_error_is_a_harness_error() -> None:
    from harness.core.errors import HarnessError

    for error_cls in (
        *RETRYABLE_ERRORS,
        *NON_RETRYABLE_ERRORS,
    ):
        assert issubclass(error_cls, ModelError)
        assert issubclass(error_cls, HarnessError)
