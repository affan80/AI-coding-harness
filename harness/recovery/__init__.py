"""Recovery: failure classification and bounded recovery planning (M4, Person C)."""

from harness.recovery.classify import (
    ClassifiedFailure,
    FailureKind,
    classify_failure,
)

__all__ = ["ClassifiedFailure", "FailureKind", "classify_failure"]
