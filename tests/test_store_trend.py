"""common.store_trend — 점포 수 증감(전체 명세 7.12). DB 없이 돈다."""

import pytest

from common.store_trend import StoreTrendValue, store_trend

EIGHT = ["20241", "20242", "20243", "20244", "20251", "20252", "20253", "20254"]


def _counts(quarters, values):
    return dict(zip(quarters, values, strict=True))


def test_eight_quarters_compares_four_and_four_high():
    counts = _counts(EIGHT, [10, 10, 10, 10, 12, 12, 11, 11])  # 직전 40 → 최근 46
    assert store_trend(counts, "20254") == StoreTrendValue(
        latest_count=11, change_rate=0.15, confidence="높음", latest_quarter="20254"
    )


def test_quarters_before_break_are_ignored():
    counts = {"20233": 1, "20234": 1, **_counts(EIGHT, [10] * 4 + [9] * 4)}
    assert store_trend(counts, "20254").change_rate == -0.1


def test_six_quarters_compares_three_and_three_low():
    counts = _counts(EIGHT[:6], [100, 100, 100, 90, 90, 90])  # ⌊6/2⌋ = 3분기씩
    trend = store_trend(counts, "20252")
    assert (trend.change_rate, trend.confidence, trend.latest_count) == (-0.1, "낮음", 90)


def test_fewer_than_four_quarters_has_no_rate():
    trend = store_trend(_counts(EIGHT[:3], [5, 6, 7]), "20243")
    assert (trend.change_rate, trend.confidence, trend.latest_count, trend.latest_quarter) == (
        None,
        None,
        7,
        "20243",
    )


def test_missing_quarter_has_no_rate():
    counts = _counts(EIGHT, [10] * 8)
    del counts["20242"]  # 직전 구간에 빠진 분기 — 0으로 보지 않는다
    trend = store_trend(counts, "20254")
    assert trend.change_rate is None and trend.confidence is None and trend.latest_count == 10


def test_prior_zero_has_no_rate():
    trend = store_trend(_counts(EIGHT, [0, 0, 0, 0, 1, 2, 3, 4]), "20254")
    assert trend.change_rate is None and trend.latest_count == 4


def test_no_records_is_none():
    assert store_trend({}, "20254") is None
    assert store_trend({"20254": 3}, None) is None


def test_latest_count_falls_back_to_own_latest_row():
    """데이터 전체 최신 분기에 그 조합 행이 없으면 그 조합의 가장 최근 행을 보여 주고 증감은 없다."""
    counts = _counts(EIGHT[:7], [10] * 7)
    trend = store_trend(counts, "20254")
    assert (trend.latest_count, trend.latest_quarter, trend.change_rate) == (10, "20253", None)


@pytest.mark.parametrize(
    ("prior", "recent", "expected"),
    [(3, 2, -0.333), (3, 4, 0.333), (7, 6, -0.143)],
)
def test_change_rate_rounded_to_three_places(prior, recent, expected):
    counts = _counts(EIGHT, [prior] * 4 + [recent] * 4)
    assert store_trend(counts, "20254").change_rate == expected
