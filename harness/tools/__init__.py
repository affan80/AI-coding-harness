"""Tool layer: the only path to the target repository (PRD §10; issues #11, #12).

Every read, write, command, and git inspection goes through
:class:`harness.tools.registry.ToolRegistry`, which enforces the per-actor
capability table and validates target paths (traversal + approved scope)
before any handler runs.
"""

from harness.tools.policy import PERMISSIONS, CommandPolicy
from harness.tools.registry import ToolRegistry
from harness.tools.result import ToolResult

__all__ = ["PERMISSIONS", "CommandPolicy", "ToolRegistry", "ToolResult"]
