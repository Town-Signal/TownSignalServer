"""지역 · 업종 마스터 조회 (⑦ · ⑧ · ⑨ · ⑩)."""

from collections.abc import Sequence

from sqlalchemy import Row, case, func, select
from sqlalchemy.orm import Session

from api.models import District, DistrictRent, Dong, Industry
from common.constants import INDUSTRY_CATEGORIES

# TODO(가정): 이름 정렬은 COLLATE "C"(한글 음절 코드 순 = 가나다순). DB 로캘과 관계없이 같은 순서가 나온다
_KOREAN_ORDER = "C"


def list_districts(session: Session) -> Sequence[Row]:
    """자치구 25개 + 임대료 신뢰도. district_rent 행이 없으면 confidence는 None."""
    stmt = (
        select(District.district_code, District.name, District.geo_code, DistrictRent.confidence)
        .outerjoin(DistrictRent, DistrictRent.district_code == District.district_code)
        .order_by(District.district_code)
    )
    return session.execute(stmt).all()


def district_exists(session: Session, district_code: str) -> bool:
    return session.get(District, district_code) is not None


def list_dongs(session: Session, district_code: str) -> Sequence[Row]:
    stmt = (
        select(Dong.dong_code, Dong.name, Dong.district_code, Dong.geo_code)
        .where(Dong.district_code == district_code)
        .order_by(Dong.name.collate(_KOREAN_ORDER), Dong.dong_code)
    )
    return session.execute(stmt).all()


def search_dongs(session: Session, keyword: str, limit: int) -> tuple[Sequence[Row], int]:
    """행정동명 부분 일치. 앞부분 일치 먼저 → 가나다순 → dong_code. (rows, 잘리기 전 전체 개수)

    strpos로 찾아서 keyword의 % · _가 LIKE 와일드카드로 해석되지 않는다.
    """
    position = func.strpos(Dong.name, keyword)
    matched = (
        select(
            Dong.dong_code,
            Dong.name.label("dong_name"),
            Dong.district_code,
            District.name.label("district_name"),
            Dong.geo_code,
        )
        .join(District, District.district_code == Dong.district_code)
        .where(position > 0)
    )
    total = session.scalar(select(func.count()).select_from(matched.subquery()))
    rows = session.execute(
        matched.order_by(
            case((position == 1, 0), else_=1),
            Dong.name.collate(_KOREAN_ORDER),
            Dong.dong_code,
        ).limit(limit)
    ).all()
    return rows, total or 0


def list_industries(session: Session, category: str | None) -> Sequence[Row]:
    """category 순서(외식업 → 서비스업 → 소매업) → 이름순. category가 있으면 그 대분류만."""
    category_order = case(
        {name: i for i, name in enumerate(INDUSTRY_CATEGORIES)},
        value=Industry.category,
        else_=len(INDUSTRY_CATEGORIES),
    )
    stmt = select(Industry.industry_code, Industry.name, Industry.category).order_by(
        category_order, Industry.name.collate(_KOREAN_ORDER), Industry.industry_code
    )
    if category is not None:
        stmt = stmt.where(Industry.category == category)
    return session.execute(stmt).all()
