"""/api/recommendations — 행정동 추천 · 재조회 · 예산 여유 (전체 명세 8.3 · 8.6). 핸들러는 모두 동기(def)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_session
from api.schemas.common import ApiResponse, ok
from api.schemas.recommendation import (
    MarginResponse,
    RecommendationExecuteRequest,
    RecommendationResponse,
    RecommendationSnapshot,
)
from api.schemas.types import DongCode
from api.services import recommendation_service
from common.constants import SERVING_MODEL_VERSION

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=ApiResponse[RecommendationResponse])
def execute(body: RecommendationExecuteRequest, session: SessionDep):
    """④ ②의 스냅샷(rec_id)으로 후보 행정동을 종합점수순 top_k개 추천하고 rec_item에 저장한다."""
    data = recommendation_service.execute(session, body)
    return ok(data, meta={"base_quarter": data.base_quarter, "model_version": SERVING_MODEL_VERSION})


@router.get("/{rec_id}", response_model=ApiResponse[RecommendationSnapshot])
def snapshot(rec_id: UUID, session: SessionDep):
    """⑤ 추천 스냅샷 재조회(대시보드 · 3단계 복원)."""
    data = recommendation_service.snapshot(session, rec_id)
    return ok(data, meta={"base_quarter": data.base_quarter, "model_version": SERVING_MODEL_VERSION})


@router.get("/{rec_id}/margin", response_model=ApiResponse[MarginResponse])
def margin(rec_id: UUID, session: SessionDep, dong_code: Annotated[DongCode, Query()]):
    """⑥ 상권 상세의 예산 여유(그 동이 속한 자치구 값)."""
    return ok(recommendation_service.margin(session, rec_id, dong_code))
