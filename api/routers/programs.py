"""/api/programs — 지원사업 · 가용예산 (전체 명세 8.2 · 8.6). 핸들러는 모두 동기(def)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_session, get_today
from api.schemas.common import ApiResponse, ListData, ok
from api.schemas.program import (
    BudgetCalculationRequest,
    BudgetCalculationResponse,
    ProgramDetail,
    UpcomingProgram,
)
from api.services import budget_service, certificate_llm, program_service

router = APIRouter(prefix="/api/programs", tags=["programs"])

SessionDep = Annotated[Session, Depends(get_session)]


# /{program_id}보다 먼저 둔다(경로 충돌 방지)
@router.get("/upcoming", response_model=ApiResponse[ListData[UpcomingProgram]])
def upcoming(
    session: SessionDep,
    today: Annotated[date, Depends(get_today)],
    limit: Annotated[int | None, Query(ge=1, le=program_service.UPCOMING_MAX)] = None,
):
    """① 마감임박 공고. 마감 전 · 상시 공고, 마감일 오름차순(상시는 맨 뒤)."""
    return ok(program_service.upcoming(session, today, limit))


@router.post("/calculate-budgets", response_model=ApiResponse[BudgetCalculationResponse])
def calculate_budgets(body: BudgetCalculationRequest, session: SessionDep):
    """② 25개 자치구별 가용예산 · 추정 임대비용 · passed 산출, rec_id 발급."""
    data, warnings = budget_service.calculate(session, body, certificate_llm.build_mapper())
    return ok(data, warnings, meta={"base_quarter": data.base_quarter})


@router.get("/{program_id}", response_model=ApiResponse[ProgramDetail])
def program_detail(program_id: int, session: SessionDep):
    """③ 지원사업 공고 상세(검수 공고만)."""
    return ok(program_service.detail(session, program_id))
