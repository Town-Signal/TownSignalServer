"""/api/admin — 관리자 API (전체 명세 8.5 ⑮). 핸들러는 동기(def).

X-Admin-Token 헤더가 ADMIN_TOKEN과 같아야 한다. ADMIN_TOKEN이 비어 있으면 항상 401이다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_session, require_admin
from api.schemas.admin import AccuracyItem
from api.schemas.common import ApiResponse, ListData, ok
from api.services import admin_service

# 토큰 검사(require_admin)가 라우터 단위 의존성이라 DB 조회보다 먼저 돈다
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/verification/accuracy", response_model=ApiResponse[ListData[AccuracyItem]])
def extraction_accuracy(session: Annotated[Session, Depends(get_session)]):
    """⑮ 필드별 공고 추출 정확도(v_extraction_accuracy, accuracy_pct 오름차순)."""
    return ok(admin_service.extraction_accuracy(session))
