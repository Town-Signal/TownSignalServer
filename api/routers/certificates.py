"""/api/certificates — 표준 자격증 목록 (전체 명세 8.6 ⑰). DB를 쓰지 않는다."""

from fastapi import APIRouter

from api.schemas.certificate import CertificateItem
from api.schemas.common import ApiResponse, ListData, ok
from api.services import certificate_service

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


@router.get("", response_model=ApiResponse[ListData[CertificateItem]])
def certificates():
    """⑰ 자격증 추천 목록용 표준명 · 동의어 · 분류. 프론트가 앱 시작 시 한 번 받아 검색어로 거른다."""
    return ok(certificate_service.certificates())
