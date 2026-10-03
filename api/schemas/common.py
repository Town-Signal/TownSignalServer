"""공통 응답 봉투 (전체 명세 8.1.1 · 9장).

모든 응답은 성공 · 실패와 관계없이 ApiResponse 모양이다. 라우터는 data만 만들고
봉투는 ok()가 씌운다. 실패 봉투는 api/errors의 예외 핸들러가 만든다.
"""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from api.middleware import current_request_id

T = TypeVar("T")

KST = ZoneInfo("Asia/Seoul")

ErrorReason = Literal[
    "REQUIRED", "INVALID_TYPE", "INVALID_FORMAT", "OUT_OF_RANGE",
    "TOO_SHORT", "TOO_LONG", "TOO_FEW", "TOO_MANY",
    "DUPLICATE", "UNKNOWN_CODE", "INVALID_VALUE",
]


class FieldError(BaseModel):
    field: str  # 점 표기 경로: age, dong_codes.2
    reason: ErrorReason
    message: str  # 입력 칸 옆에 띄울 문구
    rejected_value: object | None = None


class Notice(BaseModel):
    code: str  # SUPPORT_PROGRAM_UNAVAILABLE 등
    message: str


class Meta(BaseModel):
    request_id: str
    timestamp: datetime
    base_quarter: str | None = None
    model_version: str | None = None


class ApiResponse(BaseModel, Generic[T]):
    status: Literal["SUCCESS", "FAILURE"]
    code: str  # 성공 "OK", 실패는 8.1.1 오류 코드표
    message: str | None = None
    data: T | None = None
    errors: list[FieldError] = Field(default_factory=list)
    warnings: list[Notice] = Field(default_factory=list)
    meta: Meta


class ListData(BaseModel, Generic[T]):
    items: list[T]
    total: int


def build_meta(request_id: str | None = None, extra: dict[str, Any] | None = None) -> Meta:
    """meta를 만든다. timestamp는 KST(+09:00), 초 단위."""
    return Meta(
        request_id=request_id or current_request_id(),
        timestamp=datetime.now(KST).replace(microsecond=0),
        **(extra or {}),
    )


def ok(
    data: Any = None,
    warnings: list[Notice] | None = None,
    meta: dict[str, Any] | None = None,
) -> ApiResponse:
    """성공 봉투. meta에는 base_quarter · model_version만 넘긴다(예측 · 임대료를 쓸 때)."""
    return ApiResponse(
        status="SUCCESS", code="OK", data=data, warnings=warnings or [], meta=build_meta(extra=meta)
    )
