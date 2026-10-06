"""추천 스냅샷 저장 · 조회 (② · ④ · ⑤ · ⑥)."""

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Row, and_, delete, func, select
from sqlalchemy.orm import Session

from api.models import District, Dong, Prediction, RecItem, Recommendation, StoreQuarterly, SummaryCache

RECENT_QUARTERS = 4  # 표본 판정 평균 구간(7.6, 가정)


def create_recommendation(
    session: Session, input_condition: dict[str, Any], calculated_budgets: dict[str, Any]
) -> uuid.UUID:
    """recommendation 행을 만들고 DB가 만든 rec_id(UUIDv4, gen_random_uuid())를 돌려준다.

    commit은 하지 않는다(서비스가 한다).
    """
    rec = Recommendation(input_condition=input_condition, calculated_budgets=calculated_budgets)
    session.add(rec)
    session.flush()
    return rec.rec_id


def get_recommendation(session: Session, rec_id: uuid.UUID) -> Recommendation | None:
    return session.get(Recommendation, rec_id)


def get_dong(session: Session, dong_code: str) -> Dong | None:
    return session.get(Dong, dong_code)


def list_candidates(
    session: Session, industry_code: str, model_version: str, district_codes: Iterable[str], limit: int
) -> Sequence[Row]:
    """후보 행정동(7.6): 주어진 자치구의 동 중 예측 행이 있는 것만(inner join).

    점수 · 백분위는 prediction 저장값 그대로. total_score↓ → sales_p50↓ → dong_code↑, NULL은 뒤.
    """
    stmt = (
        select(
            Prediction,
            Dong.name.label("dong_name"),
            Dong.district_code,
            Dong.geo_code,
            District.name.label("district_name"),
        )
        .join(Dong, Dong.dong_code == Prediction.dong_code)
        .join(District, District.district_code == Dong.district_code)
        .where(
            Prediction.industry_code == industry_code,
            Prediction.model_version == model_version,
            Dong.district_code.in_(list(district_codes)),
        )
        .order_by(
            Prediction.total_score.desc().nulls_last(),
            Prediction.sales_p50.desc().nulls_last(),
            Prediction.dong_code,
        )
        .limit(limit)
    )
    return session.execute(stmt).all()


def recent_store_averages(
    session: Session, industry_code: str, dong_codes: Iterable[str]
) -> dict[str, float]:
    """최근 4개 분기 평균 점포 수(동 → 평균). 점포 기록이 없는 동은 결과에 없다.

    TODO(가정): '최근 4개 분기'는 store_quarterly 전체에서 가장 최근 year_quarter 4개다.
    """
    dong_codes = list(dong_codes)
    if not dong_codes:
        return {}
    recent = (
        select(StoreQuarterly.year_quarter)
        .distinct()
        .order_by(StoreQuarterly.year_quarter.desc())
        .limit(RECENT_QUARTERS)
        .scalar_subquery()
    )
    stmt = (
        select(StoreQuarterly.dong_code, func.avg(StoreQuarterly.store_count))
        .where(
            StoreQuarterly.industry_code == industry_code,
            StoreQuarterly.dong_code.in_(dong_codes),
            StoreQuarterly.year_quarter.in_(recent),
        )
        .group_by(StoreQuarterly.dong_code)
    )
    return {dong: float(avg) for dong, avg in session.execute(stmt).all()}


def get_summaries(session: Session, industry_code: str, dong_codes: Iterable[str]) -> dict[str, str]:
    """summary_cache(동 → 요약문). 요청 경로에서 LLM을 부르지 않는다(판정 9)."""
    dong_codes = list(dong_codes)
    if not dong_codes:
        return {}
    stmt = select(SummaryCache.dong_code, SummaryCache.summary_text).where(
        SummaryCache.industry_code == industry_code, SummaryCache.dong_code.in_(dong_codes)
    )
    return dict(session.execute(stmt).all())


def replace_rec_items(session: Session, rec_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
    """같은 rec_id의 rec_item을 지우고 다시 넣는다(7.6-6). commit은 서비스가 한다."""
    session.execute(delete(RecItem).where(RecItem.rec_id == rec_id))
    session.add_all(RecItem(rec_id=rec_id, **item) for item in items)
    session.flush()


def list_rec_items_with_prediction(
    session: Session, rec_id: uuid.UUID, industry_code: str, model_version: str
) -> Sequence[Row]:
    """rec_item(순위 · 예산 여유) + 응답 시점 최신 prediction(LEFT JOIN, 없으면 None) — 판정 41."""
    stmt = (
        select(
            RecItem.rank_no,
            RecItem.dong_code,
            RecItem.budget_margin,
            Dong.name.label("dong_name"),
            Dong.district_code,
            District.name.label("district_name"),
            Prediction,
        )
        .join(Dong, Dong.dong_code == RecItem.dong_code)
        .join(District, District.district_code == Dong.district_code)
        .outerjoin(
            Prediction,
            and_(
                Prediction.dong_code == RecItem.dong_code,
                Prediction.industry_code == industry_code,
                Prediction.model_version == model_version,
            ),
        )
        .where(RecItem.rec_id == rec_id)
        .order_by(RecItem.rank_no)
    )
    return session.execute(stmt).all()
