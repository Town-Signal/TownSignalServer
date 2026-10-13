"""점포 수 증감 조립 (전체 명세 7.12 · 판정 42) — ④ · ⑤ · ⑫ · ⑭가 같이 쓴다.

store_quarterly를 읽어 common.store_trend로 계산하고 DTO로 옮긴다.
요청 경로의 단순 집계이며 모델 추론이 아니다.
"""

from collections.abc import Iterable
from dataclasses import asdict

from sqlalchemy.orm import Session

from api.models import StoreQuarterly
from api.repositories import insight_repository as insight_repo
from api.repositories import recommendation_repository as rec_repo
from api.schemas.region import StoreTrend
from common.store_trend import store_trend


def store_trends(
    session: Session, industry_code: str, dong_codes: Iterable[str]
) -> dict[str, StoreTrend | None]:
    """dong_code → StoreTrend. 점포 기록이 없는 동은 None.

    최신 분기는 store_quarterly 전체 기준이다(6.4 성장세와 같게, 조합마다 다르게 잡지 않는다).
    """
    dong_codes = list(dong_codes)
    quarters = insight_repo.latest_quarters(session, StoreQuarterly, 1)
    latest = quarters[-1] if quarters else None
    series = rec_repo.store_count_series(session, industry_code, dong_codes)
    result: dict[str, StoreTrend | None] = {}
    for dong in dong_codes:
        value = store_trend(series.get(dong, {}), latest)
        result[dong] = None if value is None else StoreTrend(**asdict(value))
    return result
