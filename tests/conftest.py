"""Shared test fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


class FakeClock:
    """Deterministic clock: each call advances by ``step``."""

    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)):
        self._current = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._step = step

    def __call__(self) -> datetime:
        current = self._current
        self._current += self._step
        return current


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
