"""/api/regions — 상권 탐색 · 마스터 조회 (전체 명세 8.4 · 8.6). 핸들러는 모두 동기(def)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from api.deps import get_session
from api.schemas.common import ApiResponse, ListData, ok
from api.schemas.region import (
    AnalyticsResponse,
    DistrictItem,
    DongItem,
    IndustryItem,
    PredictionResponse,
    RankingResponse,
    RegionCompareRequest,
    RegionCompareResponse,
    RegionSearchItem,
    SummaryResponse,
)
from api.schemas.types import DistrictCode, DongCode, IndustryCategory, IndustryCode
from api.services import insight_service, region_service
from common.constants import AREA_MAX_SQM, DEFAULT_AREA_SQM, SERVING_MODEL_VERSION

RANKING_MAX = 426  # ⑱ limit 1~426, 기본 전체(8.6)

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


def _market_meta(session: Session, with_model: bool) -> dict:
    meta = {"base_quarter": insight_service.market_base_quarter(session)}
    if with_model:
        meta["model_version"] = SERVING_MODEL_VERSION
    return meta


@router.get("/analytics/{dong_code}", response_model=ApiResponse[AnalyticsResponse])
def analytics(
    session: SessionDep,
    dong_code: Annotated[DongCode, Path()],
    industry_code: Annotated[IndustryCode | None, Query()] = None,
    area_sqm: Annotated[float, Query(gt=0, le=AREA_MAX_SQM)] = DEFAULT_AREA_SQM,
):
    """⑪ 상권 현황: 업종 분포 · 최근 8분기 매출 추이 · 임대료 · 유동 인구 · 성장세."""
    data = insight_service.analytics(session, dong_code, industry_code, area_sqm)
    return ok(data, meta=_market_meta(session, with_model=industry_code is not None))


@router.get("/predictions/{dong_code}/{industry_code}", response_model=ApiResponse[PredictionResponse])
def prediction(
    session: SessionDep,
    dong_code: Annotated[DongCode, Path()],
    industry_code: Annotated[IndustryCode, Path()],
):
    """⑫ 예측구간 단건. 예측 행이 없으면 200 + 값 null + '예측 불가'."""
    data = insight_service.prediction(session, dong_code, industry_code)
    return ok(data, meta=_market_meta(session, with_model=True))


@router.get("/summary/{dong_code}", response_model=ApiResponse[SummaryResponse])
def summary(
    session: SessionDep,
    dong_code: Annotated[DongCode, Path()],
    industry_code: Annotated[IndustryCode, Query()],
):
    """⑬ 행정동 요약(summary_cache 조회만, 없으면 null)."""
    return ok(insight_service.summary(session, dong_code, industry_code))


@router.get("/rankings", response_model=ApiResponse[RankingResponse])
def rankings(
    session: SessionDep,
    industry_code: Annotated[IndustryCode, Query()],
    limit: Annotated[int, Query(ge=1, le=RANKING_MAX)] = RANKING_MAX,
):
    """⑱ 업종별 행정동 랭킹. rank는 배치가 저장한 score_rank 그대로."""
    data = insight_service.rankings(session, industry_code, limit)
    return ok(data, meta=_market_meta(session, with_model=True))


@router.post("/compare", response_model=ApiResponse[RegionCompareResponse])
def compare(body: RegionCompareRequest, session: SessionDep):
    """⑭ 1~4개 상권 비교(요청 순서). rec_id가 있으면 예산 여유 · passed를 스냅샷에서."""
    data = insight_service.compare(session, body)
    return ok(data, meta=_market_meta(session, with_model=True))
