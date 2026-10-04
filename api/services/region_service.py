"""지역 · 업종 마스터 조회 서비스 (⑦ · ⑧ · ⑨ · ⑩)."""

import re

from sqlalchemy.orm import Session

from api.errors import MESSAGES, AppError, field_error
from api.repositories import region_repository as repo
from api.schemas.common import ListData
from api.schemas.region import DistrictItem, DongItem, IndustryItem, RegionSearchItem

SEARCH_LIMIT = 8  # ⑦ 최대 8건(8.6)
_SPACES = re.compile(r"\s+")


def _items(model, rows) -> list:
    """조회 Row → DTO. ORM 객체를 그대로 내보내지 않는다."""
    return [model.model_validate(row, from_attributes=True) for row in rows]


def search(session: Session, q: str | None) -> ListData[RegionSearchItem]:
    """공백을 모두 뺀 검색어가 0자면 오류가 아니라 빈 목록이다(8.6).

    TODO(가정): total은 8건으로 자르기 전 전체 일치 수다(8.1.1 '전체 개수').
    """
    keyword = _SPACES.sub("", q or "")
    if not keyword:
        return ListData(items=[], total=0)
    rows, total = repo.search_dongs(session, keyword, SEARCH_LIMIT)
    return ListData(items=_items(RegionSearchItem, rows), total=total)


def districts(session: Session) -> ListData[DistrictItem]:
    items = [
        DistrictItem(
            district_code=row.district_code,
            name=row.name,
            # TODO(가정): district_rent 행이 없는 구는 '없음'
            rent_confidence=row.confidence or "없음",
            geo_code=row.geo_code,
        )
        for row in repo.list_districts(session)
    ]
    return ListData(items=items, total=len(items))


def dongs(session: Session, district_code: str) -> ListData[DongItem]:
    # TODO(가정): 형식은 맞지만 마스터에 없는 자치구는 422 UNKNOWN_CODE
    # (8.1.1 '본문 · 쿼리 안의 코드가 마스터에 없으면 UNKNOWN_CODE')
    if not repo.district_exists(session, district_code):
        raise AppError(
            "VALIDATION_ERROR",
            422,
            MESSAGES["VALIDATION_ERROR"],
            [field_error("district_code", "UNKNOWN_CODE", district_code)],
        )
    items = _items(DongItem, repo.list_dongs(session, district_code))
    return ListData(items=items, total=len(items))


def industries(session: Session, category: str | None) -> ListData[IndustryItem]:
    items = _items(IndustryItem, repo.list_industries(session, category))
    return ListData(items=items, total=len(items))
