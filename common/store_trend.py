"""점포 수 증감 (전체 명세 7.12 · 판정 42).

생존 예측이 없는 업종에서 생존 칸 대신 보여 주는 값이다. 업종과 관계없이 늘 계산하고,
화면은 생존 확률이 없을 때만 쓴다. 점수 · 정렬에는 쓰지 않는다(7.7).

change_rate = 최근 m분기 평균 점포 수 ÷ 직전 m분기 평균 − 1. 분기 규칙(2024Q1 경계, k ≥ 8이면 4분기 '높음',
4 ≤ k < 8이면 ⌊k/2⌋분기 '낮음', k < 4 · 빠진 분기 · 직전 0이면 계산 안 함)은 성장세(6.4)와 같아
common.growth.period_change를 그대로 쓴다.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from common.growth import period_change

CHANGE_RATE_PLACES = 3  # TODO(가정): growth_rate(NUMERIC(6,3))와 같게 소수 3자리 반올림


@dataclass(frozen=True)
class StoreTrendValue:
    latest_count: int  # 최근 분기 점포 수
    change_rate: float | None  # -0.071 = -7.1%. 분기가 모자라거나 직전이 0이면 None
    confidence: str | None  # '높음' · '낮음'. change_rate가 None이면 None
    latest_quarter: str  # year_quarter 형식 '20252'


def store_trend(counts: Mapping[str, int], latest_quarter: str | None) -> StoreTrendValue | None:
    """counts = 한 행정동 · 업종의 {year_quarter: store_count}.

    latest_quarter = store_quarterly 전체의 최신 분기.

    점포 기록이 전혀 없으면 None(응답에서 store_trend 객체 대신 null).

    TODO(가정): latest_count · latest_quarter는 데이터 전체 최신 분기에 그 조합 행이 있으면 그 값,
    없으면 그 조합의 가장 최근 행이다. 이때 최근 구간에 빠진 분기가 있어 change_rate는 None이 된다.
    """
    if not counts or latest_quarter is None:
        return None
    own_latest = latest_quarter if latest_quarter in counts else max(counts)
    change = period_change(counts, latest_quarter)
    rate = None if change.rate is None else round(change.rate, CHANGE_RATE_PLACES)
    return StoreTrendValue(
        latest_count=counts[own_latest],
        change_rate=rate,
        confidence=change.confidence if rate is not None else None,
        latest_quarter=own_latest,
    )
