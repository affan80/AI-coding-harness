"""OpenAI-compatible chat-completions adapter.

Speaks the ``/chat/completions`` wire format understood by api.openai.com and
compatible runtimes (vLLM, Ollama's OpenAI layer, gateways). All responses
are normalised into the contract vocabulary by the shared pipeline in
:mod:`harness.model.providers.base`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from harness.model.providers.base import BaseProviderClient, NormalizedOutput
from harness.model.providers.http import ProviderHttpRequest, ProviderHttpResponse
from harness.model.redaction import truncate_for_message
from harness.model.types import FinishReason, Message, ResponseSchema, ToolCall, ToolSpec, Usage

if TYPE_CHECKING:
    from collections.abc import Mapping

_FINISH_REASON_MAP: dict[str, FinishReason] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "length",
    "max_tokens": "length",
    "content_filter": "content_filter",
}


class OpenAICompatibleClient(BaseProviderClient):
    """Adapter for OpenAI chat-completions and compatible endpoints."""

    def _default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    def _build_request(
        self,
        messages: tuple[Message, ...],
        tools: tuple[ToolSpec, ...] | None,
        response_schema: ResponseSchema | None,
    ) -> ProviderHttpRequest:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
        }
        # api.openai.com moved to max_completion_tokens; compatible runtimes
        # (vLLM, Ollama) still speak max_tokens.
        if "api.openai.com" in self._base_url:
            payload["max_completion_tokens"] = self._capabilities.max_output_tokens
        else:
            payload["max_tokens"] = self._capabilities.max_output_tokens
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": dict(spec.parameters),
                    },
                }
                for spec in tools
            ]
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.name,
                    "schema": dict(response_schema.schema),
                    "strict": response_schema.strict,
                },
            }
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.reveal()}"
        return ProviderHttpRequest(
            method="POST",
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            payload=payload,
            timeout_seconds=self._timeout,
        )

    def _parse_output(self, payload: Mapping[str, Any]) -> NormalizedOutput:
        choices = payload.get("choices") or []
        if not choices:
            raise self._malformed("openai-compatible response has no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        tool_calls = tuple(
            self._tool_call(raw)
            for raw in (message.get("tool_calls") or ())
            if isinstance(raw, dict)
        )
        usage_payload = payload.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_payload.get("prompt_tokens") or 0),
            output_tokens=int(usage_payload.get("completion_tokens") or 0),
        )
        text = message.get("content")
        return NormalizedOutput(
            text=text or None,
            tool_calls=tool_calls,
            finish_reason=_FINISH_REASON_MAP.get(choice.get("finish_reason") or "stop", "stop"),
            usage=usage,
        )

    def _extract_error(self, response: ProviderHttpResponse) -> tuple[str, dict[str, Any] | None]:
        try:
            payload = response.json()
        except ValueError:
            return truncate_for_message(response.body) or f"HTTP {response.status}", None
        if not isinstance(payload, dict):
            return f"HTTP {response.status}", None
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or f"HTTP {response.status}")
            details = {key: value for key, value in error.items() if key != "message"}
            return message, details or None
        return f"HTTP {response.status}", None

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(dict(call.arguments), sort_keys=True),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    def _tool_call(self, raw: Mapping[str, Any]) -> ToolCall:
        function = raw.get("function") or {}
        arguments = self._parse_arguments(function.get("arguments"), function.get("name"))
        return ToolCall(
            id=str(raw.get("id") or ""),
            name=str(function.get("name") or ""),
            arguments=arguments,
        )

    def _parse_arguments(self, raw_arguments: Any, tool_name: Any) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return dict(raw_arguments)
        try:
            arguments = json.loads(raw_arguments or "{}")
        except (TypeError, ValueError):
            arguments = None
        if not isinstance(arguments, dict):
            snippet = truncate_for_message(str(raw_arguments))
            raise self._malformed(
                f"tool call {tool_name!r} returned non-JSON or non-object arguments: {snippet}"
            )
        return arguments
