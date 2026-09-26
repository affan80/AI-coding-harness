from app import grade, normalize_score


def test_normalize_maps_full_score_to_100():
    assert normalize_score(100) == 100


def test_normalize_maps_half_score_to_50():
    assert normalize_score(50) == 50


def test_grade_passes_at_fifty():
    assert grade(50) == "pass"
