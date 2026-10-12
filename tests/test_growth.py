"""성장세 · 점포 수 증감 공통 규칙(common.growth)과 배치 선계산(compute_growth). 전체 명세 6.4 · 7.12."""

import math

import pytest

from batch.jobs.train_and_predict import GROWTH_RATE_LIMIT, compute_growth
from common.growth import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    PeriodChange,
    period_change,
    quarter_index,
    quarter_label,
    window_size,
)


def _quarters(first: str, last: str) -> list[str]:
    return [quarter_label(i) for i in range(quarter_index(first), quarter_index(last) + 1)]


def test_quarter_roundtrip():
    assert quarter_label(quarter_index("20244") + 1) == "20251"


@pytest.mark.parametrize(
    ("latest", "expected"),
    [
        ("20254", (4, CONFIDENCE_HIGH)),  # 경계 이후 8개 분기
        ("20262", (4, CONFIDENCE_HIGH)),
        ("20252", (3, CONFIDENCE_LOW)),  # 6개 → 3분기씩
        ("20244", (2, CONFIDENCE_LOW)),  # 4개 → 2분기씩
        ("20243", None),  # 3개 — 계산하지 않음
    ],
)
def test_window_size(latest, expected):
    assert window_size(latest) == expected


def test_full_year_comparison():
    values = dict.fromkeys(_quarters("20241", "20244"), 100.0) | dict.fromkeys(
        _quarters("20251", "20254"), 110.0
    )
    change = period_change(values, "20254")
    assert change.rate == pytest.approx(0.1)
    assert change.confidence == CONFIDENCE_HIGH
    assert change.window == 4


def test_ignores_quarters_before_space_break():
    values = dict.fromkeys(_quarters("20241", "20254"), 100.0) | {"20234": 1_000_000.0, "20233": 0.0}
    assert period_change(values, "20254").rate == pytest.approx(0.0)


def test_low_confidence_uses_half_of_available_quarters():
    # 20241 ~ 20252(6개) → 직전 20241 ~ 20243, 최근 20244 ~ 20252
    values = {"20241": 10.0, "20242": 10.0, "20243": 10.0, "20244": 20.0, "20251": 20.0, "20252": 20.0}
    change = period_change(values, "20252")
    assert change == PeriodChange(1.0, CONFIDENCE_LOW, 3)


@pytest.mark.parametrize("hole", [None, math.nan])
def test_missing_quarter_gives_none(hole):
    values = dict.fromkeys(_quarters("20241", "20254"), 100.0)
    values["20243"] = hole
    assert period_change(values, "20254") == PeriodChange(None, None, 4)
    del values["20243"]
    assert period_change(values, "20254").rate is None


def test_zero_base_gives_none():
    values = dict.fromkeys(_quarters("20241", "20244"), 0.0) | dict.fromkeys(_quarters("20251", "20254"), 5.0)
    assert period_change(values, "20254").rate is None


def test_too_few_quarters_gives_none():
    assert period_change({"20241": 1.0, "20242": 2.0, "20243": 3.0}, "20243") == PeriodChange(
        None, None, None
    )


# ---------- 배치 선계산 ----------
def _sales_frame(rows):
    pd = pytest.importorskip("pandas")
    return pd.DataFrame(rows, columns=["dong_code", "industry_code", "year_quarter", "amount"])


def test_compute_growth_by_combo_with_global_latest_quarter():
    rows = []
    for q in _quarters("20241", "20254"):
        rows.append(("11110515", "CS100001", q, 100 if q < "20251" else 125))
        if q != "20254":  # 이 조합은 최신 분기가 없다 → 데이터 전체의 최신 분기 기준으로 빠진 분기
            rows.append(("11110515", "CS100002", q, 100))
    out = compute_growth(_sales_frame(rows))
    assert out[("11110515", "CS100001")] == (0.25, CONFIDENCE_HIGH)
    assert out[("11110515", "CS100002")] == (None, None)


def test_compute_growth_rounds_and_clamps():
    rows = [("11110515", "CS100001", q, 3) for q in _quarters("20241", "20244")]
    rows += [("11110515", "CS100001", q, 4) for q in _quarters("20251", "20254")]
    rows += [("11110516", "CS100001", q, 0.001) for q in _quarters("20241", "20244")]
    rows += [("11110516", "CS100001", q, 1_000_000) for q in _quarters("20251", "20254")]
    out = compute_growth(_sales_frame(rows))
    assert out[("11110515", "CS100001")][0] == 0.333
    assert out[("11110516", "CS100001")][0] == GROWTH_RATE_LIMIT


def test_compute_growth_empty():
    assert compute_growth(_sales_frame([])) == {}
