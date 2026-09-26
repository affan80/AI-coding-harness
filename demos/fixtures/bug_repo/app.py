"""Score normalization utilities.

Contains one reproducible defect: normalize_score divides by the wrong
scale, so the demo's first verification run fails deterministically.
"""


def normalize_score(score: float) -> float:
    """Normalize a raw score (0-100) onto a 0-100 percentage scale."""
    return score / 200 * 100


def grade(score: float) -> str:
    return "pass" if normalize_score(score) >= 50 else "fail"
