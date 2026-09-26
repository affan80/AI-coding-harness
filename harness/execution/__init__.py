"""Execution: one validated plan step at a time through the tool boundary.

The executor is the only component allowed to modify target repository source
(CONTRIBUTING rule 3). It translates a single :class:`PlanStep` into typed
tool requests, runs them through the :class:`ToolRegistry`, captures
mutation evidence, and returns control to the orchestrator with a structured
:class:`StepOutcome` (issue #11).
"""

from harness.execution.executor import ExecutorLoop

__all__ = ["ExecutorLoop"]
