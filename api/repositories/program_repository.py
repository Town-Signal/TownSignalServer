"""지원사업 · 임대료 · 업종 조회 (① · ② · ③)."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import Row, case, func, select
from sqlalchemy.orm import Session

from api.models import District, DistrictRent, Industry, SupportProgram


def list_matchable_programs(session: Session) -> Sequence[SupportProgram]:
    """매칭 대상: 검수를 마친 공고 전부(verified_by IS NOT NULL). 마감 공고도 포함한다(7.3)."""
    stmt = (
        select(SupportProgram)
        .where(SupportProgram.verified_by.is_not(None))
        .order_by(SupportProgram.program_id)
    )
    return session.scalars(stmt).all()


def get_verified_program(session: Session, program_id: int) -> Row | None:
    """검수 공고 하나 + 자치구명. 검수 전 공고는 없는 것으로 본다(8.2 ③)."""
    stmt = (
        select(SupportProgram, District.name.label("district_name"))
        .outerjoin(District, District.district_code == SupportProgram.district_code)
        .where(SupportProgram.program_id == program_id, SupportProgram.verified_by.is_not(None))
    )
    return session.execute(stmt).first()


def list_upcoming(session: Session, today: date, limit: int) -> tuple[Sequence[Row], int]:
    """검수 공고 중 현금 지원(amount_max > 0)이고 마감일이 있는 것. 마감된 공고도 넣는다.

    순서: 모집 중(apply_end ≥ 오늘) 마감 가까운 순 → 마감 공고 최근 마감 순 → program_id.
    (rows, limit으로 자르기 전 전체 개수)
    """
    programs = (
        select(SupportProgram, District.name.label("district_name"))
        .outerjoin(District, District.district_code == SupportProgram.district_code)
        .where(
            SupportProgram.verified_by.is_not(None),
            SupportProgram.amount_max > 0,
            SupportProgram.apply_end.is_not(None),
        )
    )
    is_open = SupportProgram.apply_end >= today
    total = session.scalar(select(func.count()).select_from(programs.subquery())) or 0
    rows = session.execute(
        programs.order_by(
            case((is_open, 0), else_=1),
            case((is_open, SupportProgram.apply_end)).asc().nulls_last(),  # 모집 중: 마감 가까운 순
            SupportProgram.apply_end.desc(),  # 마감: 최근 마감 순
            SupportProgram.program_id,
        ).limit(limit)
    ).all()
    return rows, total


def get_industry(session: Session, industry_code: str) -> Industry | None:
    return session.get(Industry, industry_code)


def list_districts_with_rent(session: Session) -> Sequence[Row]:
    """자치구 25개 + district_rent(없으면 칼럼이 None). district_code 오름차순."""
    stmt = (
        select(
            District.district_code,
            District.name,
            District.geo_code,
            DistrictRent.rent_per_sqm,
            DistrictRent.deposit_multiplier,
            DistrictRent.confidence,
            DistrictRent.base_quarter,
        )
        .outerjoin(DistrictRent, DistrictRent.district_code == District.district_code)
        .order_by(District.district_code)
    )
    return session.execute(stmt).all()
