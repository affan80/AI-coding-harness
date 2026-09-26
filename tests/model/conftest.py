"""Shared offline test doubles for the model runtime (issue M1.18).

No network is ever touched: transports are stubs, providers get canned
payloads, and the fake client is fully scripted.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness.model import (
    AnthropicClient,
    ModelCapabilities,
    ModelClientSettings,
    OpenAICompatibleClient,
    ProviderName,
    Secret,
)
from harness.model.providers.http import ProviderHttpRequest, ProviderHttpResponse

API_KEY = "sk-unit-test-key-1234567890"


class StubTransport:
    """Records outbound requests and replays one canned HTTP response."""

    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        body: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list[ProviderHttpRequest] = []
        self._status = status
        self._headers = headers or {}
        self._payload = payload
        self._body = body
        self._error = error

    async def __call__(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        body = self._body if self._body is not None else json.dumps(self._payload)
        return ProviderHttpResponse(status=self._status, headers=self._headers, body=body)


@pytest.fixture
def api_key() -> str:
    return API_KEY


@pytest.fixture
def stub_transport() -> type[StubTransport]:
    return StubTransport


def _settings(provider: ProviderName, model: str, **overrides: Any) -> ModelClientSettings:
    common: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "api_key": Secret(API_KEY),
        "timeout_seconds": 5.0,
    }
    common.update(overrides)
    return ModelClientSettings(**common)


@pytest.fixture
def make_openai_client():
    def _make(
        transport: Any,
        model: str = "gpt-4o",
        *,
        capabilities: ModelCapabilities | None = None,
        **overrides: Any,
    ) -> OpenAICompatibleClient:
        settings = _settings(
            ProviderName.OPENAI, model, base_url="https://unit.test/v1", **overrides
        )
        return OpenAICompatibleClient(settings, transport=transport, capabilities=capabilities)

    return _make


@pytest.fixture
def make_anthropic_client():
    def _make(
        transport: Any,
        model: str = "claude-sonnet-4-5",
        *,
        capabilities: ModelCapabilities | None = None,
        **overrides: Any,
    ) -> AnthropicClient:
        settings = _settings(
            ProviderName.ANTHROPIC, model, base_url="https://unit.test", **overrides
        )
        return AnthropicClient(settings, transport=transport, capabilities=capabilities)

    return _make
