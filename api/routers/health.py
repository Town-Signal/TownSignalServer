"""헬스체크. 배포 스크립트가 컨테이너 기동을 확인하는 데 쓴다.

DB에 의존하지 않는다. DB가 잠깐 끊겨도 컨테이너 자체는 살아 있다고 보고해야
배포 판단과 장애 판단을 구분할 수 있다. 컨테이너 상태 확인은 HTTP 200 여부만 본다.
"""

from fastapi import APIRouter

from api.config import APP_ENV
from api.schemas.common import ApiResponse, ok
from api.schemas.health import HealthData

router = APIRouter(tags=["health"])


@router.get("/health", response_model=ApiResponse[HealthData])
def health() -> ApiResponse[HealthData]:
    return ok(HealthData(service_status="ok", env=APP_ENV))
