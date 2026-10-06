"""common.scoring — PERCENT_RANK · 40/40/20 · 결측 비례 재배분 (명세 7.7)."""

import pytest

from common.scoring import percent_rank, percent_ranks, total_score

# ── 7.7 회귀 ─────────────────────────────────────────────────


def test_total_score_with_growth_is_78_2():
    result = total_score({"sales": 0.88, "survival": 0.75, "growth": 0.65})
    assert result.total_score == 78.2
    assert result.applied_weights == {"sales": 0.4, "survival": 0.4, "growth": 0.2}


def test_total_score_without_growth_is_57_5_with_redistribution():
    result = total_score({"sales": 0.55, "survival": 0.60, "growth": None})
    assert result.total_score == 57.5
    assert result.applied_weights == {"sales": 0.5, "survival": 0.5, "growth": 0.0}
    assert result.contributions["growth"] is None


def test_contributions_sum_to_total_score():
    result = total_score({"sales": 0.88, "survival": 0.75, "growth": 0.65})
    assert result.contributions == {"sales": 35.2, "survival": 30.0, "growth": 13.0}
    assert sum(result.contributions.values()) == pytest.approx(result.total_score, abs=0.05)


def test_all_missing_gives_no_score():
    result = total_score({"sales": None, "survival": None, "growth": None})
    assert result.total_score is None
    assert result.applied_weights == {"sales": 0.0, "survival": 0.0, "growth": 0.0}


def test_only_one_factor_takes_full_weight():
    result = total_score({"sales": None, "survival": 0.3, "growth": None})
    assert result.total_score == 30.0
    assert result.applied_weights["survival"] == 1.0


# ── PERCENT_RANK ─────────────────────────────────────────────


def test_percent_rank_matches_sql_definition():
    population = [10, 20, 30, 40, 50]
    assert percent_rank(population, 10) == 0.0
    assert percent_rank(population, 30) == 0.5
    assert percent_rank(population, 50) == 1.0


def test_percent_rank_ignores_null_and_ties_share_value():
    population = [10, None, 20, 20, 40, None]
    assert percent_rank(population, 20) == pytest.approx(1 / 3)
    assert percent_rank(population, 40) == 1.0
    assert percent_rank(population, None) is None


def test_percent_rank_single_value_is_one():
    assert percent_rank([42], 42) == 1.0
    assert percent_rank([None, 42], 42) == 1.0


def test_percent_ranks_matches_single_version_and_keeps_order():
    values = [30, None, 10, 20, 20, 50]
    ranks = percent_ranks(values)
    assert ranks == [percent_rank(values, v) for v in values]
    assert ranks[1] is None
    assert percent_ranks([7]) == [1.0]
    assert percent_ranks([None]) == [None]


def test_population_is_not_candidate_set():
    """같은 동이라도 모집단(서울 전체)이 같으면 백분위가 같다 — 후보군에 따라 바뀌지 않는다."""
    seoul = [100, 200, 300, 400, 500]
    assert percent_rank(seoul, 400) == 0.75


# ── 근거 분해용 가중치 · 표본 판정 ─────────────────────────────


def test_applied_weights_only_redistributes_weights():
    from common.scoring import applied_weights

    assert applied_weights({"sales": 0.9, "survival": 0.1, "growth": 0.5}) == {
        "sales": 0.4, "survival": 0.4, "growth": 0.2,
    }
    assert applied_weights({"sales": 0.55, "survival": 0.60, "growth": None}) == {
        "sales": 0.5, "survival": 0.5, "growth": 0.0,
    }
    assert applied_weights({"sales": None, "survival": None, "growth": None}) == {
        "sales": 0.0, "survival": 0.0, "growth": 0.0,
    }


@pytest.mark.parametrize(
    ("has_prediction", "average", "expected"),
    [
        (False, 30.0, "예측 불가"),
        (True, 4.75, "표본 부족"),
        (True, 5.0, "정상"),
        (True, None, "표본 부족"),  # 예측은 있는데 점포 기록이 없음(가정)
    ],
)
def test_data_status(has_prediction, average, expected):
    from common.scoring import data_status

    assert data_status(has_prediction, average) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, "적음"), (2, "적음"), (3, "보통"), (4, "보통"), (5, "많음"), (6, "많음"), (None, None)],
)
def test_store_level(value, expected):
    from common.scoring import store_level

    assert store_level([1, 2, 3, 4, 5, 6], value) == expected  # PERCENT_RANK 0 · .2 · .4 · .6 · .8 · 1
