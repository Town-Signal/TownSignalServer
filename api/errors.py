"""앱 예외와 예외 핸들러 (전체 명세 8.1.1).

FastAPI 기본 오류 형식 {"detail": ...}을 내보내지 않는다. 모든 실패는 ApiResponse 봉투로 바꾼다.
- RequestValidationError → 422 VALIDATION_ERROR (본문이 JSON이 아니면 400 BAD_REQUEST)
- AppError → 해당 code
- Starlette HTTPException → 404 NOT_FOUND · 405 METHOD_NOT_ALLOWED
- OperationalError(DB 연결 실패 · 시간 초과) → 503 DB_UNAVAILABLE
- 그 밖의 Exception → 500 INTERNAL_ERROR (스택은 로그에만)
"""

import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.middleware import REQUEST_ID_HEADER, new_request_id
from api.schemas.common import ApiResponse, ErrorReason, FieldError, build_meta

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, http_status: int, message: str, errors: list[FieldError] | None = None):
        super().__init__(code)
        self.code, self.http_status, self.message = code, http_status, message
        self.errors = errors or []


# 예: raise AppError("REC_NOT_FOUND", 404, "추천 결과를 찾을 수 없어요. 다시 추천받아 주세요.")

MESSAGES = {
    "BAD_REQUEST": "요청 형식이 올바르지 않아요.",
    "VALIDATION_ERROR": "입력한 값을 다시 확인해 주세요.",
    "NOT_FOUND": "요청한 주소를 찾을 수 없어요.",
    "METHOD_NOT_ALLOWED": "허용하지 않는 요청 방식이에요.",
    "DB_UNAVAILABLE": "잠시 후 다시 시도해 주세요.",
    "INTERNAL_ERROR": "일시적인 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
}

# Pydantic 오류 type → errors.reason (8.1.1 사유 코드표)
# duplicate · unknown_code는 DTO validator가 PydanticCustomError로 내는 type이다(같은 value_error라 구분용)
_REASON_BY_TYPE: dict[str, ErrorReason] = {
    "missing": "REQUIRED",
    "uuid_parsing": "INVALID_TYPE",
    "string_pattern_mismatch": "INVALID_FORMAT",
    "uuid_type": "INVALID_FORMAT",
    "json_invalid": "INVALID_FORMAT",
    "greater_than": "OUT_OF_RANGE",
    "greater_than_equal": "OUT_OF_RANGE",
    "less_than": "OUT_OF_RANGE",
    "less_than_equal": "OUT_OF_RANGE",
    "string_too_short": "TOO_SHORT",
    "string_too_long": "TOO_LONG",
    "too_short": "TOO_FEW",
    "too_long": "TOO_MANY",
    "duplicate": "DUPLICATE",
    "unknown_code": "UNKNOWN_CODE",
}

_LOC_SOURCES = {"body", "query", "path", "header", "cookie"}


def _reason(error_type: str) -> ErrorReason:
    if error_type in _REASON_BY_TYPE:
        return _REASON_BY_TYPE[error_type]
    if error_type.endswith(("_parsing", "_type")):
        return "INVALID_TYPE"
    return "INVALID_VALUE"  # value_error · enum · literal_error 등


_NUMBER_REASONS: tuple[ErrorReason, ...] = ("REQUIRED", "INVALID_TYPE", "OUT_OF_RANGE")

# 칸별 맞춤 문구 (field, reason) → message. 없으면 아래 사유별 기본 문구를 쓴다.
# TODO(가정): 문구는 화면명세서 확정 전 임시. 엔드포인트를 붙일 때마다 해당 칸을 더한다.
FIELD_MESSAGES: dict[tuple[str, ErrorReason], str] = {
    ("district_code", "REQUIRED"): "자치구를 선택해 주세요.",
    ("district_code", "INVALID_FORMAT"): "자치구 코드는 숫자 5자리예요.",
    ("district_code", "UNKNOWN_CODE"): "없는 자치구예요. 자치구를 다시 선택해 주세요.",
    ("category", "INVALID_VALUE"): "업종 대분류는 외식업 · 서비스업 · 소매업 중 하나예요.",
    # ② calculate-budgets (age · industry_code REQUIRED 문구는 8.1.1 실패 예시 그대로)
    **{("age", r): "나이는 15~99세 사이로 입력해 주세요." for r in _NUMBER_REASONS},
    ("industry_code", "REQUIRED"): "희망 업종을 선택해 주세요.",
    ("industry_code", "INVALID_FORMAT"): "희망 업종을 다시 선택해 주세요.",
    ("industry_code", "UNKNOWN_CODE"): "없는 업종이에요. 희망 업종을 다시 선택해 주세요.",
    **{("capital", r): "자본금은 0원 이상으로 입력해 주세요." for r in _NUMBER_REASONS},
    **{("career_years", r): "경력은 0년 이상으로 입력해 주세요." for r in ("INVALID_TYPE", "OUT_OF_RANGE")},
    **{
        ("target_area_sqm", r): "희망 면적은 0㎡보다 크고 1,000㎡ 이하로 입력해 주세요."
        for r in ("INVALID_TYPE", "OUT_OF_RANGE")
    },
    ("certificates", "TOO_MANY"): "자격증은 20개까지 넣을 수 있어요.",
    ("certificates.*", "TOO_SHORT"): "자격증 이름을 입력해 주세요.",
    ("certificates.*", "TOO_LONG"): "자격증 이름은 50자 이하로 입력해 주세요.",
    # ① upcoming · ③ program 상세
    **{("limit", r): "limit은 1~50 사이 정수로 요청해 주세요." for r in ("INVALID_TYPE", "OUT_OF_RANGE")},
    ("program_id", "INVALID_TYPE"): "공고 번호가 올바르지 않아요.",
}

_INDEX = re.compile(r"\.\d+(?=\.|$)")


def _field_message_for(field: str, reason: ErrorReason, ctx: dict[str, Any]) -> str:
    """칸별 문구 → 목록 칸(certificates.0 → certificates.*) 문구 → 사유별 기본 문구 순으로 찾는다."""
    return (
        FIELD_MESSAGES.get((field, reason))
        or FIELD_MESSAGES.get((_INDEX.sub(".*", field), reason))
        or _field_message(reason, ctx)
    )


def field_error(field: str, reason: ErrorReason, rejected_value: object | None = None) -> FieldError:
    """서비스에서 직접 만드는 칸 오류(예: 마스터에 없는 코드 → UNKNOWN_CODE)."""
    message = _field_message_for(field, reason, {})
    return FieldError(field=field, reason=reason, message=message, rejected_value=rejected_value)


def _field_message(reason: ErrorReason, ctx: dict[str, Any]) -> str:
    """사유별 기본 문구. 칸별 문구는 FIELD_MESSAGES가 먼저다."""
    if reason == "OUT_OF_RANGE":
        for key, tail in (("ge", "이상으로"), ("gt", "보다 크게"), ("le", "이하로"), ("lt", "보다 작게")):
            if key in ctx:
                sep = " " if key in ("ge", "le") else ""
                return f"{ctx[key]}{sep}{tail} 입력해 주세요."
        return "허용 범위를 벗어났어요."
    if reason == "TOO_SHORT":
        return f"{ctx.get('min_length', 1)}자 이상 입력해 주세요."
    if reason == "TOO_LONG":
        return f"{ctx.get('max_length')}자 이하로 입력해 주세요."
    if reason == "TOO_FEW":
        return f"{ctx.get('min_length', 1)}개 이상 골라 주세요."
    if reason == "TOO_MANY":
        return f"{ctx.get('max_length')}개까지만 고를 수 있어요."
    return {
        "REQUIRED": "필수 입력 항목이에요.",
        "INVALID_TYPE": "입력 형식이 올바르지 않아요.",
        "INVALID_FORMAT": "형식이 올바르지 않아요.",
        "DUPLICATE": "같은 값을 두 번 넣을 수 없어요.",
        "UNKNOWN_CODE": "존재하지 않는 코드예요.",
        "INVALID_VALUE": "허용하지 않는 값이에요.",
    }[reason]


def _rejected(error: dict[str, Any]) -> object | None:
    if error["type"] == "missing":
        return None  # missing의 input은 상위 객체 전체라 내리지 않는다
    try:
        return jsonable_encoder(error.get("input"))
    except Exception:
        return str(error.get("input"))


def to_field_errors(errors: list[dict[str, Any]]) -> list[FieldError]:
    """Pydantic 오류 목록을 8.1.1 errors 항목으로 바꾼다."""
    result = []
    for error in errors:
        loc = list(error.get("loc", ()))
        if loc and loc[0] in _LOC_SOURCES:
            loc = loc[1:]
        reason = _reason(error["type"])
        field = ".".join(str(p) for p in loc) or "body"
        result.append(
            FieldError(
                field=field,
                reason=reason,
                message=_field_message_for(field, reason, error.get("ctx") or {}),
                rejected_value=_rejected(error),
            )
        )
    return result


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


def failure(
    request: Request, http_status: int, code: str, message: str, errors: list[FieldError] | None = None
) -> JSONResponse:
    """실패 봉투. 500 핸들러는 미들웨어 바깥에서 돌므로 X-Request-ID 헤더를 여기서 직접 붙인다."""
    request_id = _request_id(request)
    body = ApiResponse(
        status="FAILURE",
        code=code,
        message=message,
        data=None,
        errors=errors or [],
        meta=build_meta(request_id=request_id),
    )
    return JSONResponse(
        status_code=http_status,
        content=body.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id},
    )


def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = list(exc.errors())
    # TODO(가정): 본문이 JSON이 아니면 사유표의 INVALID_FORMAT(422)이 아니라 코드표대로 400 BAD_REQUEST
    if any(e.get("type") == "json_invalid" for e in errors):
        return failure(request, 400, "BAD_REQUEST", MESSAGES["BAD_REQUEST"])
    return failure(request, 422, "VALIDATION_ERROR", MESSAGES["VALIDATION_ERROR"], to_field_errors(errors))


def _on_app_error(request: Request, exc: AppError) -> JSONResponse:
    return failure(request, exc.http_status, exc.code, exc.message, exc.errors)


def _on_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code == 405:
        code = "METHOD_NOT_ALLOWED"
    elif exc.status_code < 500:
        code = "BAD_REQUEST"
    else:
        code = "INTERNAL_ERROR"
    response = failure(request, exc.status_code, code, MESSAGES[code])
    if exc.headers:  # 405의 Allow 등은 유지한다
        response.headers.update(exc.headers)
    return response


def _on_db_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    logger.error("DB_UNAVAILABLE request_id=%s", _request_id(request), exc_info=exc)
    return failure(request, 503, "DB_UNAVAILABLE", MESSAGES["DB_UNAVAILABLE"])


def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.error("INTERNAL_ERROR request_id=%s", _request_id(request), exc_info=exc)
    return failure(request, 500, "INTERNAL_ERROR", MESSAGES["INTERNAL_ERROR"])


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(AppError, _on_app_error)
    app.add_exception_handler(StarletteHTTPException, _on_http_exception)
    app.add_exception_handler(OperationalError, _on_db_unavailable)
    app.add_exception_handler(Exception, _on_unhandled)