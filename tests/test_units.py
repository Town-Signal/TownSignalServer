"""common.units — 분기 매출 → 월 매출(÷ 3, 버림) (판정 31)."""

import pytest

from common.units import quarterly_to_monthly


@pytest.mark.parametrize(
    ("quarterly", "monthly"),
    [(96_000_000, 32_000_000), (130_673_000, 43_557_666), (2, 0), (0, 0), (None, None)],
)
def test_quarterly_to_monthly(quarterly, monthly):
    assert quarterly_to_monthly(quarterly) == monthly


@pytest.mark.parametrize(
    ("amount", "count", "expected"),
    [(100, 3, 33), (5, 2, 3), (7, 2, 4), (1, 0, None), (None, 3, None), (100, None, None)],
)
def test_per_store_amount(amount, count, expected):
    from common.units import per_store_amount

    assert per_store_amount(amount, count) == expected  # 사사오입
