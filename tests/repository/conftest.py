"""Shared fixtures for repository profiling tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def write_tree(tmp_path: Path):
    """Return ``write(spec)`` that materializes a nested dict under tmp_path.

    Keys ending with ``/`` become directories; bytes values are written raw;
    str values are written as UTF-8 text; dict values recurse.
    """

    def write(spec: dict) -> Path:
        for name, value in spec.items():
            path = tmp_path / name
            if name.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, dict):
                path.mkdir(parents=True, exist_ok=True)
                continue
            if isinstance(value, bytes):
                path.write_bytes(value)
            else:
                path.write_text(value, encoding="utf-8")
        return tmp_path

    return write


@pytest.fixture
def package_json():
    def make(payload: dict) -> str:
        return json.dumps(payload)

    return make
