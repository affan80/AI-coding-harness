"""Failure classification, recovery, and loop detection (PRD §§17, 20; FR-10)."""

from harness.recovery.classifier import (
    FailureInput,
    classify_failure,
    collect_failure_evidence,
    stage_result_to_input,
)
from harness.recovery.engine import (
    RecoveryRequest,
    evaluate_recovery,
    persist_recovery_outcome,
    should_rollback,
)
from harness.recovery.loops import LoopDetector, action_fingerprint, result_digest
from harness.recovery.models import (
    AttemptRecord,
    FailureClass,
    FailureEvidence,
    RecoveryDecision,
    RecoveryOutcome,
    RecoveryPlan,
    RepairAction,
)
from harness.recovery.planner import (
    RecoveryModelClient,
    RecoveryPlanError,
    build_recovery_prompt,
    parse_recovery_plan,
    propose_recovery_plan,
)
from harness.recovery.reverify import reverification_sequence

__all__ = [
    "AttemptRecord",
    "FailureClass",
    "FailureEvidence",
    "FailureInput",
    "LoopDetector",
    "RecoveryDecision",
    "RecoveryModelClient",
    "RecoveryOutcome",
    "RecoveryPlan",
    "RecoveryPlanError",
    "RecoveryRequest",
    "RepairAction",
    "action_fingerprint",
    "build_recovery_prompt",
    "classify_failure",
    "collect_failure_evidence",
    "evaluate_recovery",
    "parse_recovery_plan",
    "persist_recovery_outcome",
    "propose_recovery_plan",
    "reverification_sequence",
    "result_digest",
    "should_rollback",
    "stage_result_to_input",
]
