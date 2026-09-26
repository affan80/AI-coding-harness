"""Structured provider failures for the model runtime (issue M1.17).

Extends the core error contract (``harness.core.errors.HarnessError``) with
the fields the orchestrator needs to budget retries: an explicit
``retryable`` flag, provider identity, HTTP status, and a ``Retry-After``
hint. Every failure crossing the model boundary is one of these, so callers
never parse provider-specific exception strings (PRD §17). Messages are
short; callers redact them before they reach logs or evidence (see
``harness.model.redaction``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from harness.core.errors import HarnessError


class ModelError(HarnessError):
    """Base class for every structured model-runtime failure."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    @property
    def error_type(self) -> str:
        return self.__class__.__name__

    def to_dict(self) -> dict[str, Any]:
        """Structured, log-safe view for events and evidence artifacts."""
        payload: dict[str, Any] = {
            "error": self.error_type,
            "message": self.message,
            "retryable": self.retryable,
            "provider": self.provider,
            "model": self.model,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        if self.details:
            payload["details"] = dict(self.details)
        return payload


class RateLimitError(ModelError):
    """Provider rejected the call for rate limiting; safe to retry."""

    retryable = True


class ProviderTimeoutError(ModelError):
    """The provider did not answer within the configured timeout; safe to retry."""

    retryable = True


class ProviderUnavailableError(ModelError):
    """Provider-side outage or unusable response; safe to retry."""

    retryable = True


class AuthenticationError(ModelError):
    """Credentials were rejected; retrying cannot help."""

    retryable = False


class InvalidRequestError(ModelError):
    """The request itself was malformed; retrying cannot help."""

    retryable = False


class ContextWindowExceededError(InvalidRequestError):
    """Estimated input plus the output reserve exceeds the context window.

    Not retryable as-is: the context manager must shrink the working set.
    """


class CapabilityNotSupportedError(InvalidRequestError):
    """The provider/model does not support a requested capability."""


class MalformedModelError(ModelError):
    """Provider returned output violating the expected shape; retrying may help."""

    retryable = True


class ModelConfigurationError(ModelError):
    """Environment or configuration problem detected before any provider call."""


def http_status_to_error(
    status: int,
    message: str,
    *,
    provider: str,
    model: str = "",
    retry_after_seconds: float | None = None,
    details: Mapping[str, Any] | None = None,
) -> ModelError:
    """Map an HTTP status from any provider onto the structured hierarchy."""
    common: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "status_code": status,
        "details": details,
    }
    if status == 429:
        return RateLimitError(message, retry_after_seconds=retry_after_seconds, **common)
    if status in (401, 403):
        return AuthenticationError(message, **common)
    if status == 408:
        return ProviderTimeoutError(message, **common)
    if status >= 500:
        return ProviderUnavailableError(message, **common)
    return InvalidRequestError(message, **common)
