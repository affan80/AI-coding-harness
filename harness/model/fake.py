"""Deterministic fake client for offline tests (issue M1.18).

``FakeModelClient`` replays a scripted list of turns, records every call,
and derives usage deterministically from the input — no network, no
randomness, no wall-clock latency (``latency_ms`` is always 0). Tests assert
against it exactly like a real provider, and it implements the same
normalisation semantics (structured-output parsing, finish reasons) so the
whole agent layer can be exercised end-to-end without credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.model.errors import MalformedModelError
from harness.model.types import (
    Message,
    ModelCapabilities,
    ModelResponse,
    ResponseSchema,
    ToolCall,
    ToolSpec,
    Usage,
    estimate_message_tokens,
    estimate_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_UNSET = object()


class FakeScriptExhaustedError(RuntimeError):
    """The test script ran out of turns; this is a test bug, not a ModelError."""


@dataclass(frozen=True, slots=True)
class ScriptedTurn:
    """One planned fake response, or an error to raise instead.

    ``structured`` defaults to unset: when a response schema is requested and
    no explicit value was scripted, the fake parses ``text`` as JSON exactly
    like a real adapter would.
    """

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    structured: Any = _UNSET
    error: Exception | None = None
    finish_reason: str | None = None
    usage: Usage | None = None
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """Everything one ``generate`` call received, for assertions."""

    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    response_schema: ResponseSchema | None


class FakeModelClient:
    """Scripted, deterministic ``ModelClient`` implementation."""

    def __init__(
        self,
        script: Sequence[ScriptedTurn] = (),
        *,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self._script = list(script)
        self._capabilities = capabilities or ModelCapabilities(
            provider="fake",
            model="fake-model",
            context_window_tokens=8_192,
            max_output_tokens=1_024,
            tool_calling=True,
            structured_output=True,
        )
        self._calls: list[RecordedCall] = []

    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    @property
    def calls(self) -> tuple[RecordedCall, ...]:
        return tuple(self._calls)

    async def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ModelResponse:
        messages = tuple(messages)
        tool_specs = tuple(tools) if tools is not None else ()
        self._calls.append(
            RecordedCall(messages=messages, tools=tool_specs, response_schema=response_schema)
        )
        if not self._script:
            raise FakeScriptExhaustedError(
                f"FakeModelClient script exhausted after {len(self._calls)} call(s)"
            )
        turn = self._script.pop(0)
        if turn.error is not None:
            raise turn.error

        structured = self._structured_for(turn) if response_schema is not None else None
        usage = turn.usage or self._estimated_usage(messages, tool_specs, turn)
        finish_reason = turn.finish_reason or ("tool_calls" if turn.tool_calls else "stop")
        return ModelResponse(
            text=turn.text,
            tool_calls=turn.tool_calls,
            structured=structured,
            finish_reason=finish_reason,
            usage=usage,
            latency_ms=turn.latency_ms,
            provider=self._capabilities.provider,
            model=self._capabilities.model,
        )

    @staticmethod
    def _structured_for(turn: ScriptedTurn) -> Any:
        if turn.structured is not _UNSET:
            return turn.structured
        if turn.text and turn.text.strip():
            try:
                return json.loads(turn.text)
            except ValueError:
                raise MalformedModelError(
                    "fake scripted output is not valid JSON for the requested response schema"
                ) from None
        return None

    @staticmethod
    def _estimated_usage(
        messages: tuple[Message, ...], tools: tuple[ToolSpec, ...], turn: ScriptedTurn
    ) -> Usage:
        input_tokens = estimate_message_tokens(messages, tools)
        output_tokens = estimate_tokens(turn.text or "")
        for call in turn.tool_calls:
            output_tokens += estimate_tokens(call.name)
            call_json = json.dumps(dict(call.arguments), sort_keys=True, default=str)
            output_tokens += estimate_tokens(call_json)
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens)
