"""/api/regions — 상권 탐색 · 마스터 조회 (전체 명세 8.4 · 8.6). 핸들러는 모두 동기(def)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_session
from api.schemas.common import ApiResponse, ListData, ok
from api.schemas.region import DistrictItem, DongItem, IndustryItem, RegionSearchItem
from api.schemas.types import DistrictCode, IndustryCategory
from api.services import region_service

router = APIRouter(prefix="/api/regions", tags=["regions"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/search", response_model=ApiResponse[ListData[RegionSearchItem]])
def search(session: SessionDep, q: Annotated[str | None, Query()] = None):
    """⑦ 행정동 자동완성. 공백을 뺀 1자 이상, 최대 8건. 0자면 빈 목록(오류 아님)."""
    return ok(region_service.search(session, q))


@router.get("/districts", response_model=ApiResponse[ListData[DistrictItem]])
def districts(session: SessionDep):
    """⑧ 자치구 25개, district_code 오름차순."""
    return ok(region_service.districts(session))


@router.get("/dongs", response_model=ApiResponse[ListData[DongItem]])
def dongs(session: SessionDep, district_code: Annotated[DistrictCode, Query()]):
    """⑨ 자치구 산하 행정동, 이름 가나다순. district_code 필수."""
    return ok(region_service.dongs(session, district_code))


@router.get("/industries", response_model=ApiResponse[ListData[IndustryItem]])
def industries(session: SessionDep, category: Annotated[IndustryCategory | None, Query()] = None):
    """⑩ 업종 마스터. category 생략 시 전체, 대분류 → 이름 순."""
    return ok(region_service.industries(session, category))
