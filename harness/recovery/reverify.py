"""Targeted re-verification ordering (#64).

After a repair, the failed verification stage reruns FIRST, then the ladder
widens in PRD §16 order. Stages that precede the failed one already passed
and are not re-run, keeping re-verification cheap and focused.
"""

from __future__ import annotations

from harness.verification.models import LADDER_ORDER, StageName


def reverification_sequence(first_failure: StageName) -> tuple[StageName, ...]:
    """Failed stage first, then the not-yet-run wider stages."""
    index = LADDER_ORDER.index(first_failure)
    return (first_failure, *LADDER_ORDER[index + 1 :])
