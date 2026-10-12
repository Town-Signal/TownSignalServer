"""분기 증감률 — 최근 m분기 합 ÷ 직전 m분기 합 − 1 (전체 명세 6.4 성장세 · 7.12 점포 수 증감).

성장세(배치, 매출 합계)와 점포 수 증감(API, 점포 수)이 같은 규칙을 쓰므로 한 함수로 둔다.
두 구간의 분기 수가 같으므로 '합의 비'와 '평균의 비'는 같다.

규칙
- 2024Q1 공간 기준 변경 이후 분기만 쓴다(경계를 넘는 비교 금지).
  데이터의 최신 분기까지 경계 이후 분기 수를 k라 하면
  k ≥ 8 → 4분기씩 비교, 신뢰도 '높음' / 4 ≤ k < 8 → ⌊k/2⌋분기씩, '낮음' / k < 4 → 계산하지 않음
- 두 구간의 분기 값이 하나라도 없으면(행 없음 · NaN) 계산하지 않는다.
  빠진 분기를 0으로 보면 증감이 부풀거나 줄어든다
- 직전 구간 합이 0 이하이면 계산하지 않는다(0으로 나눔)
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

SPACE_BREAK_QUARTER = "20241"  # 2024년 1분기부터 표준단위구역(공간 기준 변경)
FULL_WINDOW = 4  # 계절성을 상쇄하려면 1년(4분기)씩 묶는다
MIN_QUARTERS = 4  # 경계 이후 분기가 이보다 적으면 계산하지 않는다
CONFIDENCE_HIGH = "높음"
CONFIDENCE_LOW = "낮음"


def quarter_index(year_quarter: str) -> int:
    """'20251' → 연속 정수. 분기끼리 빼면 분기 차이가 된다."""
    return int(year_quarter[:4]) * 4 + int(year_quarter[4]) - 1


def quarter_label(index: int) -> str:
    return f"{index // 4}{index % 4 + 1}"


@dataclass(frozen=True)
class PeriodChange:
    rate: float | None  # 0.045 = 4.5%. 계산하지 않으면 None
    confidence: str | None  # '높음' · '낮음'. 분기가 모자라면 None
    window: int | None  # 비교한 구간 길이(분기). 분기가 모자라면 None


def window_size(latest_quarter: str, break_quarter: str = SPACE_BREAK_QUARTER) -> tuple[int, str] | None:
    """최신 분기 기준 비교 구간 길이와 신뢰도. 경계 이후 분기가 4개 미만이면 None."""
    k = quarter_index(latest_quarter) - quarter_index(break_quarter) + 1
    if k >= 2 * FULL_WINDOW:
        return FULL_WINDOW, CONFIDENCE_HIGH
    if k >= MIN_QUARTERS:
        return k // 2, CONFIDENCE_LOW
    return None


def _missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def period_change(
    values: Mapping[str, float | None],
    latest_quarter: str,
    break_quarter: str = SPACE_BREAK_QUARTER,
) -> PeriodChange:
    """values = {year_quarter: 값}. latest_quarter는 데이터 전체의 최신 분기(조합마다 다르게 잡지 않는다)."""
    size = window_size(latest_quarter, break_quarter)
    if size is None:
        return PeriodChange(None, None, None)
    m, confidence = size
    last = quarter_index(latest_quarter)
    recent = [values.get(quarter_label(i)) for i in range(last - m + 1, last + 1)]
    prior = [values.get(quarter_label(i)) for i in range(last - 2 * m + 1, last - m + 1)]
    if any(_missing(v) for v in (*recent, *prior)):
        return PeriodChange(None, None, m)
    base = sum(prior)
    if base <= 0:
        return PeriodChange(None, None, m)
    return PeriodChange(sum(recent) / base - 1, confidence, m)
