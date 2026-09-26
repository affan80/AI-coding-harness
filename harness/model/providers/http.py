"""Minimal async HTTP transport over the standard library — no third-party deps.

Provider adapters receive a :data:`JsonTransport` callable; production uses
:func:`urllib_json_transport` (blocking urllib executed in a worker thread)
while tests inject canned responses, keeping the whole model layer
offline-testable. Connection failures are normalised into structured
``ModelError`` subclasses before they escape the transport.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness.model.errors import ProviderTimeoutError, ProviderUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class ProviderHttpRequest:
    """One outbound provider request.

    Headers carry credentials by design and are never logged by the transport.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    payload: Mapping[str, Any]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    """Raw provider HTTP reply, keyed by lower-case header names."""

    status: int
    headers: Mapping[str, str]
    body: str

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json(self) -> Any:
        return json.loads(self.body)


JsonTransport = Callable[[ProviderHttpRequest], Awaitable[ProviderHttpResponse]]


def _normalize_headers(raw: Any) -> dict[str, str]:
    return {key.lower(): value for key, value in raw.items()}


async def urllib_json_transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
    """Send one JSON request via blocking urllib inside a worker thread."""

    def _blocking() -> ProviderHttpResponse:
        body = json.dumps(dict(request.payload)).encode("utf-8")
        req = urllib.request.Request(
            request.url,
            data=body,
            method=request.method,
            headers=dict(request.headers),
        )
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
                return ProviderHttpResponse(
                    status=resp.status,
                    headers=_normalize_headers(resp.headers),
                    body=resp.read().decode("utf-8", errors="replace"),
                )
        except urllib.error.HTTPError as exc:
            # 4xx/5xx replies still carry a body the adapter must normalise.
            return ProviderHttpResponse(
                status=exc.code,
                headers=_normalize_headers(exc.headers),
                body=exc.read().decode("utf-8", errors="replace"),
            )

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _blocking)
    except TimeoutError as exc:
        raise ProviderTimeoutError(
            f"provider request timed out after {request.timeout_seconds:g}s"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderUnavailableError(
            f"provider connection failed: {reason.__class__.__name__}"
        ) from exc
    except http.client.HTTPException as exc:
        raise ProviderUnavailableError(
            f"provider sent an unusable HTTP reply: {exc.__class__.__name__}"
        ) from exc
    except OSError as exc:
        raise ProviderUnavailableError(
            f"provider transport failed: {exc.__class__.__name__}"
        ) from exc
