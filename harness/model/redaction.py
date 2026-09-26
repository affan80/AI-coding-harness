"""Secret containment for the model runtime (issue M1.18, PRD §7).

Credentials are wrapped in :class:`Secret`, whose ``str``/``repr`` never
reveal the value, and every string that may reach logs or evidence passes
through :class:`Redactor` so known secret values are scrubbed first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SECRET_PLACEHOLDER = "[REDACTED]"

# Shorter values are skipped when scrubbing: replacing them would corrupt
# ordinary words, while real provider keys are far longer than this.
MIN_REDACT_LEN = 8

MESSAGE_SNIPPET_LIMIT = 200


class Secret:
    """Immutable credential holder that refuses to print its value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    @property
    def is_empty(self) -> bool:
        return not self._value.strip()

    def __repr__(self) -> str:
        return SECRET_PLACEHOLDER

    def __str__(self) -> str:
        return SECRET_PLACEHOLDER


class Redactor:
    """Scrub known secret values from strings and nested structures."""

    def __init__(self, secrets: Iterable[str]) -> None:
        values = {s for s in secrets if s and len(s) >= MIN_REDACT_LEN}
        # Longest first so overlapping secrets are replaced atomically.
        self._secrets = tuple(sorted(values, key=len, reverse=True))

    def text(self, value: str) -> str:
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, SECRET_PLACEHOLDER)
        return redacted

    def details(self, value: Any) -> Any:
        """Deep-redact every string inside a JSON-like structure."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {key: self.details(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.details(item) for item in value]
        return value


def truncate_for_message(text: str, limit: int = MESSAGE_SNIPPET_LIMIT) -> str:
    """Bound a provider-supplied snippet so error messages stay small."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"
