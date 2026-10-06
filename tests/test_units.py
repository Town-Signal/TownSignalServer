"""common.units — 분기 매출 → 월 매출(÷ 3, 버림) (판정 31)."""

import pytest

from common.units import quarterly_to_monthly


@pytest.mark.parametrize(
    ("quarterly", "monthly"),
    [(96_000_000, 32_000_000), (130_673_000, 43_557_666), (2, 0), (0, 0), (None, None)],
)
def test_quarterly_to_monthly(quarterly, monthly):
    assert quarterly_to_monthly(quarterly) == monthly
