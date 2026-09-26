"""Anthropic Messages API adapter.

Normalisation notes (all handled here so agents never see wire shapes):

- harness ``system`` messages become the top-level ``system`` parameter;
- assistant ``tool_calls`` become ``tool_use`` content blocks;
- harness ``tool`` results become user ``tool_result`` content blocks;
- ``stop_reason`` maps onto the shared finish-reason vocabulary;
- the API has no native JSON-schema response mode, so ``response_schema``
  requests are refused as a structured ``CapabilityNotSupportedError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness.model.errors import InvalidRequestError
from harness.model.providers.base import BaseProviderClient, NormalizedOutput
from harness.model.providers.http import ProviderHttpRequest, ProviderHttpResponse
from harness.model.redaction import truncate_for_message
from harness.model.types import FinishReason, Message, ResponseSchema, ToolCall, ToolSpec, Usage

if TYPE_CHECKING:
    from collections.abc import Mapping

ANTHROPIC_VERSION = "2023-06-01"

_STOP_REASON_MAP: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
    "model_context_window_exceeded": "length",
}


class AnthropicClient(BaseProviderClient):
    """Adapter for the Anthropic ``/v1/messages`` wire format."""

    def _default_base_url(self) -> str:
        return "https://api.anthropic.com"

    def _build_request(
        self,
        messages: tuple[Message, ...],
        tools: tuple[ToolSpec, ...] | None,
        response_schema: ResponseSchema | None,
    ) -> ProviderHttpRequest:
        conversation = [
            self._message_payload(message) for message in messages if message.role != "system"
        ]
        if not conversation:
            # System-only prompts are valid elsewhere but not on this wire format.
            raise self._wrap(
                InvalidRequestError("anthropic requires at least one non-system message")
            )
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._capabilities.max_output_tokens,
            "messages": conversation,
        }
        system_parts = [message.content for message in messages if message.role == "system"]
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if tools:
            payload["tools"] = [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": dict(spec.parameters),
                }
                for spec in tools
            ]
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self._api_key is not None:
            headers["x-api-key"] = self._api_key.reveal()
        return ProviderHttpRequest(
            method="POST",
            url=f"{self._base_url}/v1/messages",
            headers=headers,
            payload=payload,
            timeout_seconds=self._timeout,
        )

    def _parse_output(self, payload: Mapping[str, Any]) -> NormalizedOutput:
        content = payload.get("content") or ()
        text_parts = [
            str(block.get("text"))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        tool_calls = tuple(
            self._tool_call(block)
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        )
        usage_payload = payload.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_payload.get("input_tokens") or 0),
            output_tokens=int(usage_payload.get("output_tokens") or 0),
        )
        return NormalizedOutput(
            text="\n".join(text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=_STOP_REASON_MAP.get(payload.get("stop_reason") or "end_turn", "stop"),
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
            details = {"error_type": str(error["type"])} if error.get("type") else None
            return message, details
        return f"HTTP {response.status}", None

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        if message.role == "tool":
            # Tool results travel as user messages with tool_result blocks.
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content,
                    }
                ],
            }
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        for call in message.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": dict(call.arguments),
                }
            )
        return {"role": message.role, "content": content or [{"type": "text", "text": ""}]}

    def _tool_call(self, block: Mapping[str, Any]) -> ToolCall:
        block_input = block.get("input")
        if not isinstance(block_input, dict):
            raise self._malformed(
                f"tool_use block {block.get('name')!r} returned non-object input: "
                f"{truncate_for_message(str(block_input))}"
            )
        return ToolCall(
            id=str(block.get("id") or ""),
            name=str(block.get("name") or ""),
            arguments=dict(block_input),
        )
