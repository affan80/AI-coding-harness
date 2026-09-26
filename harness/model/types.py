"""Provider-neutral value types shared by every model client (PRD §§9, 12.1).

These dataclasses are the harness-facing contract: agent roles build
``Message`` inputs and consume ``ModelResponse`` outputs without knowing
which provider produced them. Provider-specific shapes are normalised inside
``harness.model.providers`` and never escape it. ``ModelResponse`` carries no
raw provider payload, so nothing a provider returned can smuggle secrets
into logs or evidence artifacts (PRD §7).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]

FinishReason = Literal["stop", "tool_calls", "length", "content_filter"]

_ROLES: tuple[str, ...] = ("system", "user", "assistant", "tool")


def _frozen_mapping(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model-issued tool invocation, normalised across providers."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class Message:
    """One conversation message.

    ``role="tool"`` messages carry the ``tool_call_id`` of the model-issued
    call they answer. Assistant history may replay previously issued
    ``tool_calls`` so multi-turn tool conversations can be re-sent.
    """

    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"invalid message role: {self.role!r}")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Harness-side tool description offered to the model (JSON schema)."""

    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _frozen_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ResponseSchema:
    """JSON schema the model output must satisfy when structured output is wanted."""

    name: str
    schema: Mapping[str, Any] = field(default_factory=dict)
    strict: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", _frozen_mapping(self.schema))


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage for one model call (PRD §12.9)."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Runtime limits a context budget must respect (PRD §12.1).

    The context manager derives its budget from ``context_window_tokens`` and
    ``max_output_tokens`` instead of hard-coding any token number.
    """

    provider: str
    model: str
    context_window_tokens: int
    max_output_tokens: int
    tool_calling: bool = True
    structured_output: bool = True


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalised model output — identical shape for every provider."""

    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    structured: Any = None
    finish_reason: FinishReason = "stop"
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    provider: str = ""
    model: str = ""


@runtime_checkable
class ModelClient(Protocol):
    """Provider-neutral async model runtime shared by all agent roles.

    Agent prompts, tool permissions, and context slices live in the agent
    layer; a provider adapter must never know which role is calling.
    """

    async def generate(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ModelResponse:
        """Produce one normalised model response for the given conversation."""
        ...

    def capabilities(self) -> ModelCapabilities:
        """Report the runtime limits the orchestrator must budget within."""
        ...


def estimate_tokens(text: str) -> int:
    """Deterministic tokenizer-free size estimate (~4 characters per token).

    Used for preflight context guards and offline usage accounting;
    provider-reported usage on ``ModelResponse`` is always authoritative.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(
    messages: Iterable[Message],
    tools: Iterable[ToolSpec] | None = None,
) -> int:
    """Estimate the prompt size of a conversation, including tool schemas."""
    total = 0
    for message in messages:
        total += estimate_tokens(message.role) + estimate_tokens(message.content)
        for call in message.tool_calls:
            total += estimate_tokens(call.name)
            total += estimate_tokens(json.dumps(dict(call.arguments), sort_keys=True, default=str))
    for spec in tools or ():
        total += estimate_tokens(spec.name) + estimate_tokens(spec.description)
        total += estimate_tokens(json.dumps(dict(spec.parameters), sort_keys=True, default=str))
    return total
