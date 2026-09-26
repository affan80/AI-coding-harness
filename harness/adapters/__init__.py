"""Project adapters: discover, never invent, project commands (PRD §19; issue #13)."""

from harness.adapters.base import (
    AdapterCommand,
    CommandKind,
    DetectionResult,
    EnvironmentStatus,
    ProjectAdapter,
)
from harness.adapters.detect import detect_adapter
from harness.adapters.node_adapter import NodeAdapter
from harness.adapters.python_adapter import PythonAdapter

__all__ = [
    "AdapterCommand",
    "CommandKind",
    "DetectionResult",
    "EnvironmentStatus",
    "NodeAdapter",
    "ProjectAdapter",
    "PythonAdapter",
    "detect_adapter",
]
