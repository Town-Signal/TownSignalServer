"""상권 현황 · 예측 · 요약 · 랭킹 · 비교용 조회 (⑪ ⑫ ⑬ ⑭ ⑱). 순수 동기 조회, 계산은 하지 않는다."""

from collections.abc import Iterable, Sequence

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session

from api.models import (
    District,
    DistrictRent,
    Dong,
    Industry,
    PopulationQuarterly,
    Prediction,
    SalesQuarterly,
    StoreQuarterly,
    SummaryCache,
)


def latest_quarters(session: Session, model, n: int) -> list[str]:
    """그 표 전체에서 가장 최근 year_quarter n개(오름차순).

    TODO(가정): '최근 N개 분기'는 행정동별이 아니라 표 전체 기준이다. 그 동에 없는 분기는 빈 값으로 둔다.
    """
    stmt = select(model.year_quarter).distinct().order_by(model.year_quarter.desc()).limit(n)
    return sorted(session.scalars(stmt).all())


def get_dong(session: Session, dong_code: str) -> Row | None:
    stmt = (
        select(
            Dong.dong_code, Dong.name, Dong.district_code, Dong.geo_code, District.name.label("district_name")
        )
        .join(District, District.district_code == Dong.district_code)
        .where(Dong.dong_code == dong_code)
    )
    return session.execute(stmt).first()


def list_dongs(session: Session, dong_codes: Iterable[str] | None = None) -> Sequence[Row]:
    stmt = select(
        Dong.dong_code, Dong.name, Dong.district_code, Dong.geo_code, District.name.label("district_name")
    ).join(District, District.district_code == Dong.district_code)
    if dong_codes is not None:
        stmt = stmt.where(Dong.dong_code.in_(list(dong_codes)))
    return session.execute(stmt.order_by(Dong.dong_code)).all()


def get_district_rent(session: Session, district_code: str) -> DistrictRent | None:
    return session.get(DistrictRent, district_code)


def latest_sales_quarter_of_dong(session: Session, dong_code: str) -> str | None:
    """그 동의 sales_quarterly 최근 분기. 행이 하나도 없으면 None(→ '조회 불가')."""
    return session.scalar(
        select(func.max(SalesQuarterly.year_quarter)).where(SalesQuarterly.dong_code == dong_code)
    )


def store_counts(session: Session, dong_code: str, year_quarter: str) -> Sequence[Row]:
    """그 분기 그 동의 업종별 점포 수 + 업종명. 점포 수 내림차순 → 업종 코드."""
    stmt = (
        select(StoreQuarterly.industry_code, Industry.name, StoreQuarterly.store_count)
        .join(Industry, Industry.industry_code == StoreQuarterly.industry_code)
        .where(StoreQuarterly.dong_code == dong_code, StoreQuarterly.year_quarter == year_quarter)
        .order_by(StoreQuarterly.store_count.desc(), StoreQuarterly.industry_code)
    )
    return session.execute(stmt).all()


def sales_series(
    session: Session, dong_code: str, industry_code: str | None, quarters: list[str]
) -> dict[str, tuple[int, int | None]]:
    """분기 → (매출 합, 점포 수 합). industry_code가 없으면 전 업종 합계.

    점포 수는 같은 분기 · 같은 업종의 store_quarterly를 붙인다(매출이 있는 업종만 더해 점포당 매출이 맞게).
    """
    stmt = (
        select(
            SalesQuarterly.year_quarter,
            func.sum(SalesQuarterly.amount),
            func.sum(StoreQuarterly.store_count),
        )
        .outerjoin(
            StoreQuarterly,
            (StoreQuarterly.dong_code == SalesQuarterly.dong_code)
            & (StoreQuarterly.industry_code == SalesQuarterly.industry_code)
            & (StoreQuarterly.year_quarter == SalesQuarterly.year_quarter),
        )
        .where(SalesQuarterly.dong_code == dong_code, SalesQuarterly.year_quarter.in_(quarters))
        .group_by(SalesQuarterly.year_quarter)
    )
    if industry_code is not None:
        stmt = stmt.where(SalesQuarterly.industry_code == industry_code)
    return {
        q: (int(amount), None if count is None else int(count)) for q, amount, count in session.execute(stmt)
    }


def population_rows(session: Session, dong_code: str, quarters: list[str]) -> Sequence[PopulationQuarterly]:
    stmt = (
        select(PopulationQuarterly)
        .where(PopulationQuarterly.dong_code == dong_code, PopulationQuarterly.year_quarter.in_(quarters))
        .order_by(PopulationQuarterly.year_quarter)
    )
    return session.scalars(stmt).all()


def latest_population_totals(
    session: Session, dong_codes: Iterable[str], year_quarter: str | None
) -> dict[str, int]:
    if year_quarter is None:
        return {}
    stmt = select(PopulationQuarterly.dong_code, PopulationQuarterly.total).where(
        PopulationQuarterly.dong_code.in_(list(dong_codes)), PopulationQuarterly.year_quarter == year_quarter
    )
    return dict(session.execute(stmt).all())


def get_prediction(
    session: Session, dong_code: str, industry_code: str, model_version: str
) -> Prediction | None:
    return session.get(Prediction, (dong_code, industry_code, model_version))


def predictions_by_dong(session: Session, industry_code: str, model_version: str) -> dict[str, Prediction]:
    stmt = select(Prediction).where(
        Prediction.industry_code == industry_code, Prediction.model_version == model_version
    )
    return {p.dong_code: p for p in session.scalars(stmt)}


def industry_store_counts(session: Session, industry_code: str, year_quarter: str | None) -> dict[str, int]:
    """그 분기 그 업종의 행정동별 점포 수(서울 전체). store_level 3등분 모집단 · store_count_latest."""
    if year_quarter is None:
        return {}
    stmt = select(StoreQuarterly.dong_code, StoreQuarterly.store_count).where(
        StoreQuarterly.industry_code == industry_code, StoreQuarterly.year_quarter == year_quarter
    )
    return dict(session.execute(stmt).all())


def get_summary(session: Session, dong_code: str, industry_code: str) -> SummaryCache | None:
    return session.get(SummaryCache, (dong_code, industry_code))
