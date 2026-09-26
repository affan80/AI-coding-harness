"""Deterministic adapter selection (issue #57 acceptance criterion 1)."""

from __future__ import annotations

from pathlib import Path

from .base import DetectionResult, ProjectAdapter
from .node_adapter import NodeAdapter
from .python_adapter import PythonAdapter

# Checked in order; the first adapter whose required manifest exists wins,
# so a repository with both manifests is handled deterministically.
_CANDIDATES: list[ProjectAdapter] = [PythonAdapter(), NodeAdapter()]


def detect_adapter(root: Path) -> tuple[ProjectAdapter | None, DetectionResult]:
    """Pick the adapter for a repository root, or a structured unsupported."""
    for adapter in _CANDIDATES:
        result = adapter.detect(root)
        if result.supported:
            return adapter, result
    return None, DetectionResult.unsupported(
        "no Python (pyproject.toml/requirements/setup) or Node (package.json) "
        "manifest found"
    )
