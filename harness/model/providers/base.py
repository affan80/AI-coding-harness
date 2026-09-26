"""Shared provider-adapter pipeline (issue M1.17).

One implementation of the ``generate`` flow — capability guards, preflight
context check, request dispatch, HTTP-status normalisation, secret
redaction, and usage/latency capture. Concrete providers only supply
wire-format hooks, so provider differences stay localised and equivalent
payloads from any provider produce identical harness result types.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.model.errors import (
    CapabilityNotSupportedError,
    ContextWindowExceededError,
    InvalidRequestError,
    MalformedModelError,
    ModelError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    http_status_to_error,
)
from harness.model.providers.http import (
    JsonTransport,
    ProviderHttpRequest,
    ProviderHttpResponse,
    urllib_json_transport,
)
from harness.model.redaction import Redactor, truncate_for_message
from harness.model.types import (
    FinishReason,
    Message,
    ModelCapabilities,
    ModelResponse,
    ResponseSchema,
    ToolCall,
    ToolSpec,
    Usage,
    estimate_message_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness.model.config import ModelClientSettings


@dataclass(frozen=True, slots=True)
class NormalizedOutput:
    """Provider payload reduced to the contract vocabulary."""

    text: str | None
    tool_calls: tuple[ToolCall, ...]
    finish_reason: FinishReason
    usage: Usage


class BaseProviderClient(ABC):
    """Template for provider adapters; subclasses map wire formats only."""

    def __init__(
        self,
        settings: ModelClientSettings,
        *,
        capabilities: ModelCapabilities | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        self._provider = settings.provider.value
        self._model = settings.model
        self._api_key = settings.api_key
        self._base_url = (settings.base_url or self._default_base_url()).rstrip("/")
        self._timeout = settings.timeout_seconds
        self._capabilities = capabilities or settings.capabilities()
        self._transport = transport or urllib_json_transport
        self._redactor = Redactor(settings.secret_values())

    @abstractmethod
    def _default_base_url(self) -> str:
        """Provider API root used when the settings carry no base URL."""

    @abstractmethod
    def _build_request(
        self,
        messages: tuple[Message, ...],
        tools: tuple[ToolSpec, ...] | None,
        response_schema: ResponseSchema | None,
    ) -> ProviderHttpRequest:
        """Translate the conversation into one provider HTTP request."""

    @abstractmethod
    def _parse_output(self, payload: Mapping[str, Any]) -> NormalizedOutput:
        """Translate a successful provider payload into :class:`NormalizedOutput`."""

    @abstractmethod
    def _extract_error(self, response: ProviderHttpResponse) -> tuple[str, dict[str, Any] | None]:
        """Pull a short message and safe details out of an error body."""

    async def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ModelResponse:
        started = time.perf_counter()
        conversation = tuple(messages)
        tool_specs = tuple(tools) if tools is not None else ()
        self._validate_conversation(conversation)

        if tool_specs and not self._capabilities.tool_calling:
            raise self._wrap(
                CapabilityNotSupportedError(
                    f"provider '{self._provider}' model '{self._model}' "
                    "does not support tool calling"
                )
            )
        if response_schema is not None and not self._capabilities.structured_output:
            raise self._wrap(
                CapabilityNotSupportedError(
                    f"provider '{self._provider}' model '{self._model}' does not support "
                    "structured output; use prompt-based JSON instead"
                )
            )
        self._preflight_context(conversation, tool_specs)

        request = self._build_request(conversation, tool_specs or None, response_schema)
        try:
            response = await self._transport(request)
        except TimeoutError as exc:
            raise self._wrap(
                ProviderTimeoutError(f"provider request timed out after {self._timeout:g}s")
            ) from exc
        except ModelError as exc:
            raise self._wrap(exc) from exc
        except Exception as exc:
            raise self._wrap(
                ProviderUnavailableError(f"provider transport failed: {exc.__class__.__name__}")
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status != 200:
            raise self._http_error(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise self._wrap(
                ProviderUnavailableError("provider returned a non-JSON response body")
            ) from exc
        if not isinstance(payload, dict):
            raise self._wrap(
                ProviderUnavailableError("provider returned an unexpected JSON shape")
            )

        output = self._parse_output(payload)
        structured = self._parse_structured(output.text) if response_schema is not None else None
        return ModelResponse(
            text=output.text,
            tool_calls=output.tool_calls,
            structured=structured,
            finish_reason=output.finish_reason,
            usage=output.usage,
            latency_ms=latency_ms,
            provider=self._provider,
            model=self._model,
        )

    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    # -- internals ---------------------------------------------------------

    def _wrap(self, error: ModelError) -> ModelError:
        """Re-key an error onto this provider and scrub secrets from it."""
        return error.__class__(
            self._redactor.text(str(error)),
            provider=error.provider or self._provider,
            model=error.model or self._model,
            status_code=error.status_code,
            retry_after_seconds=error.retry_after_seconds,
            details=self._redactor.details(error.details) if error.details else None,
        )

    def _validate_conversation(self, messages: tuple[Message, ...]) -> None:
        if not messages:
            raise self._wrap(InvalidRequestError("messages must contain at least one message"))
        for message in messages:
            if message.role == "tool" and not message.tool_call_id:
                raise self._wrap(
                    InvalidRequestError("tool messages must carry the tool_call_id they answer")
                )

    def _preflight_context(
        self, messages: tuple[Message, ...], tools: tuple[ToolSpec, ...]
    ) -> None:
        estimated = estimate_message_tokens(messages, tools)
        allowance = self._capabilities.context_window_tokens - self._capabilities.max_output_tokens
        if estimated > allowance:
            raise self._wrap(
                ContextWindowExceededError(
                    f"estimated input of {estimated} tokens exceeds the {allowance}-token input "
                    f"budget for {self._model} (context "
                    f"{self._capabilities.context_window_tokens}, output reserve "
                    f"{self._capabilities.max_output_tokens})",
                    details={
                        "estimated_input_tokens": estimated,
                        "context_window_tokens": self._capabilities.context_window_tokens,
                        "max_output_tokens": self._capabilities.max_output_tokens,
                    },
                )
            )

    def _http_error(self, response: ProviderHttpResponse) -> ModelError:
        message, details = self._extract_error(response)
        error = http_status_to_error(
            response.status,
            message,
            provider=self._provider,
            model=self._model,
            retry_after_seconds=_parse_retry_after(response.header("retry-after")),
            details=details,
        )
        return self._wrap(error)

    def _parse_structured(self, text: str | None) -> Any:
        if text is None or not text.strip():
            raise self._wrap(
                MalformedModelError("model returned no content for the requested response schema")
            )
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            snippet = self._redactor.text(truncate_for_message(text))
            raise self._wrap(
                MalformedModelError(
                    f"model output is not valid JSON for the response schema: {snippet}"
                )
            ) from exc
        return parsed

    def _malformed(self, message: str) -> MalformedModelError:
        return self._wrap(MalformedModelError(message))


def _parse_retry_after(value: str | None) -> float | None:
    """Numeric ``Retry-After`` seconds only; HTTP-date form is left to the caller."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
