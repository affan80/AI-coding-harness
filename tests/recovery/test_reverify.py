"""Targeted re-verification ordering: failed stage first, then widen."""

from __future__ import annotations

import pytest

from harness.recovery import reverification_sequence
from harness.verification.models import StageName


def test_early_failure_widens_through_the_whole_ladder() -> None:
    sequence = reverification_sequence(StageName.SYNTAX)

    assert sequence == (
        StageName.SYNTAX,
        StageName.BUILD,
        StageName.REPRODUCER,
        StageName.TARGETED_TESTS,
        StageName.RELATED_TESTS,
        StageName.FULL_SUITE,
        StageName.DIFF_SCOPE,
    )


def test_mid_ladder_failure_skips_already_passed_stages() -> None:
    sequence = reverification_sequence(StageName.TARGETED_TESTS)

    # Syntax/build/reproducer already passed before the failure; the failed
    # stage reruns first, then the wider checks.
    assert sequence == (
        StageName.TARGETED_TESTS,
        StageName.RELATED_TESTS,
        StageName.FULL_SUITE,
        StageName.DIFF_SCOPE,
    )


def test_late_failure_only_reruns_itself_and_scope_check() -> None:
    assert reverification_sequence(StageName.FULL_SUITE) == (
        StageName.FULL_SUITE,
        StageName.DIFF_SCOPE,
    )
    assert reverification_sequence(StageName.DIFF_SCOPE) == (StageName.DIFF_SCOPE,)


def test_unknown_stage_is_rejected() -> None:
    with pytest.raises(ValueError):
        reverification_sequence("not-a-stage")
