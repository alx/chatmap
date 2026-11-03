"""Scoring rules — pure functions only, no network."""

from chatmap.liveability import (
    CRITERIA,
    _sub_score,
    describe,
    haversine_m,
    Score,
)


def test_weights_sum_to_100():
    assert sum(c["weight"] for c in CRITERIA.values()) == 100


def test_all_requested_criteria_present():
    """Every criterion the user asked for must be scoreable."""
    required = {
        "hospital", "transit", "konbini", "fresh", "seaside",
        "restaurants", "coffee", "third_space", "coworking",
    }
    assert required <= set(CRITERIA)


def test_sub_score_is_bounded_and_monotonic():
    assert _sub_score(0, 800, 8000) == 100
    assert _sub_score(800, 800, 8000) == 100
    assert _sub_score(8000, 800, 8000) == 0
    assert _sub_score(99_000, 800, 8000) == 0
    assert _sub_score(None, 800, 8000) == 0
    # monotonic decreasing
    scores = [_sub_score(d, 500, 5000) for d in range(0, 6000, 500)]
    assert scores == sorted(scores, reverse=True)


def test_seaside_ideal_is_walking_distance():
    """15 min on foot is roughly 1.2 km; the criterion must score 0 past that."""
    seaside = CRITERIA["seaside"]
    assert seaside["dead_m"] <= 1200
    assert _sub_score(1200, seaside["ideal_m"], seaside["dead_m"]) == 0
    assert _sub_score(500, seaside["ideal_m"], seaside["dead_m"]) >= 60


def test_haversine_known_distance():
    # Tokyo Station -> Shinjuku Station, ~6.4 km straight line
    d = haversine_m(35.6812, 139.7671, 35.6896, 139.7006)
    assert 5_500 < d < 7_500


def test_describe_includes_totals_and_criteria():
    score = Score(lat=0, lon=0, total=42)
    for key, spec in CRITERIA.items():
        score.criteria[key] = {
            "label": spec["label"], "weight": spec["weight"],
            "distance_m": None, "nearest": None, "score": 0, "walk_min": None,
        }
    text = describe(score)
    assert "42/100" in text
    assert "Hospital" in text and "Seaside" in text


def test_criteria_allow_types_exclude_homonyms():
    """'シーサイドホテル' is a hotel, not a beach; a post office is not the coast."""
    seaside = CRITERIA["seaside"]
    assert "hotel" not in seaside["allow_types"]
    assert "post_office" not in seaside["allow_types"]
    assert "beach" in seaside["allow_types"]
