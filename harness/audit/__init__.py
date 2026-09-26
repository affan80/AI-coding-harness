"""Evidence-gated automatic audit (PRD §18; FR-11)."""

from harness.audit.models import (
    AuditReport,
    AuditRoundReport,
    Finding,
    FindingStatus,
    InvalidTransitionError,
    authorizes_repair,
    transition,
)
from harness.audit.reproduce import ReproductionResult, apply_reproduction, attempt_reproduction

__all__ = [
    "AuditReport",
    "AuditRoundReport",
    "Finding",
    "FindingStatus",
    "InvalidTransitionError",
    "ReproductionResult",
    "apply_reproduction",
    "attempt_reproduction",
    "authorizes_repair",
    "transition",
]
