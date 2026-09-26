from common.scoring import min_max, rank


def test_min_max_handles_missing_values():
    assert min_max([10, None, 20]) == [0.0, None, 1.0]


def test_identical_values_become_mid_point():
    assert min_max([5, 5]) == [0.5, 0.5]


def test_rank_orders_by_weighted_score():
    candidates = [
        {"dong": "A", "sales": 100, "survival": 0.4, "room": 0.1, "growth": 0.0},
        {"dong": "B", "sales": 200, "survival": 0.8, "room": 0.5, "growth": 0.1},
    ]
    ranked = rank(candidates)
    assert [c["dong"] for c in ranked] == ["B", "A"]
    assert set(ranked[0]["factors"]) == {"sales", "survival", "room", "growth"}


def test_missing_growth_redistributes_weight():
    candidates = [
        {"dong": "A", "sales": 100, "survival": 0.4, "room": 0.1, "growth": None},
        {"dong": "B", "sales": 200, "survival": 0.8, "room": 0.5, "growth": None},
    ]
    ranked = rank(candidates)
    assert "growth" not in ranked[0]["factors"]
    assert abs(ranked[0]["score"] - 1.0) < 1e-6
