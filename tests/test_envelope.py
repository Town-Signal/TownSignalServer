"""모든 응답이 8.1.1 공통 봉투인지 검사한다 (성공 · 400 · 401 · 404 · 405 · 422 · 500 · 503).

실제 앱(create_app)에 테스트 전용 라우터를 붙여 오류를 일부러 일으킨다.
"""

from typing import Annotated, Literal
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, StringConstraints, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.exc import OperationalError

from api import config
from api.deps import require_admin
from api.errors import AppError
from api.main import create_app
from api.schemas.common import Notice, ok

ENVELOPE_KEYS = {"status", "code", "message", "data", "errors", "warnings", "meta"}
KNOWN_DISTRICTS = {"11620"}  # UNKNOWN_CODE 검사용 가짜 마스터


class Probe(BaseModel):
    age: int = Field(ge=15, le=99)
    industry_code: str = Field(pattern=r"^CS\d{6}$")
    nickname: str = Field(default="ab", min_length=2, max_length=5)
    certificates: list[Annotated[str, StringConstraints(min_length=1, max_length=50)]] = Field(
        default_factory=list, max_length=20
    )
    dong_codes: list[Annotated[str, StringConstraints(pattern=r"^\d{8}$")]] = Field(
        default_factory=lambda: ["11620655"], min_length=1, max_length=4
    )
    district_code: str = "11620"
    kind: Literal["a", "b"] = "a"

    @field_validator("dong_codes")
    @classmethod
    def no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise PydanticCustomError("duplicate", "중복된 값")
        return v

    @field_validator("district_code")
    @classmethod
    def known_district(cls, v: str) -> str:
        if v not in KNOWN_DISTRICTS:
            raise PydanticCustomError("unknown_code", "없는 코드")
        return v


probe = APIRouter(prefix="/probe")


@probe.post("/body")
def probe_body(body: Probe):
    return ok({"age": body.age})


@probe.get("/list")
def probe_list(limit: Annotated[int, Query(ge=1, le=426)] = 426):
    return ok({"limit": limit})


@probe.get("/rec/{rec_id}")
def probe_rec(rec_id: UUID):
    raise AppError("REC_NOT_FOUND", 404, "추천 결과를 찾을 수 없어요. 다시 추천받아 주세요.")


@probe.get("/meta")
def probe_meta():
    return ok(
        {"x": 1},
        warnings=[Notice(code="SUPPORT_PROGRAM_UNAVAILABLE", message="지원사업 정보를 불러오지 못했어요.")],
        meta={"base_quarter": "2026Q2", "model_version": "xgb_v1"},
    )


@probe.get("/boom")
def probe_boom():
    raise RuntimeError("비밀 내부 정보 SELECT * FROM secret")


@probe.get("/db")
def probe_db():
    raise OperationalError("SELECT 1", {}, Exception("connection refused password=secret"))


@probe.get("/admin", dependencies=[Depends(require_admin)])
def probe_admin():
    return ok({"admin": True})


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.include_router(probe)
    return TestClient(app, raise_server_exceptions=False)


def assert_envelope(response, http_status: int, status: str, code: str) -> dict:
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert "detail" not in body
    assert response.status_code == http_status
    assert (body["status"], body["code"]) == (status, code)
    assert (status == "SUCCESS") == (200 <= http_status < 300)  # 실패를 200으로 보내지 않는다
    if status == "FAILURE":
        assert body["data"] is None
        assert isinstance(body["message"], str) and body["message"]
    else:
        assert body["message"] is None and body["errors"] == []
    meta = body["meta"]
    assert meta["request_id"] == response.headers["X-Request-ID"]
    assert meta["timestamp"].endswith("+09:00")
    return body


VALID = {"age": 27, "industry_code": "CS100001"}


# ── 성공 ─────────────────────────────────────────────────────


def test_success_envelope(client):
    body = assert_envelope(client.get("/health"), 200, "SUCCESS", "OK")
    assert body["data"]["service_status"] == "ok"
    assert body["meta"]["base_quarter"] is None


def test_success_with_warnings_and_meta(client):
    body = assert_envelope(client.get("/probe/meta"), 200, "SUCCESS", "OK")
    assert body["warnings"] == [
        {"code": "SUPPORT_PROGRAM_UNAVAILABLE", "message": "지원사업 정보를 불러오지 못했어요."}
    ]
    assert (body["meta"]["base_quarter"], body["meta"]["model_version"]) == ("2026Q2", "xgb_v1")


def test_success_on_valid_body(client):
    body = assert_envelope(client.post("/probe/body", json=VALID), 200, "SUCCESS", "OK")
    assert body["data"] == {"age": 27}


# ── 실패 코드별 ──────────────────────────────────────────────


def test_bad_request_when_body_is_not_json(client):
    response = client.post("/probe/body", content=b"{age: 27", headers={"Content-Type": "application/json"})
    body = assert_envelope(response, 400, "FAILURE", "BAD_REQUEST")
    assert body["errors"] == []


def test_not_found_for_unknown_path(client):
    assert_envelope(client.get("/nope"), 404, "FAILURE", "NOT_FOUND")


def test_method_not_allowed(client):
    response = client.post("/health")
    assert_envelope(response, 405, "FAILURE", "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET"


def test_app_error_passes_code_through(client):
    response = client.get("/probe/rec/c7b9a2e1-4f8a-4d2b-9e12-3a8b7c9d0e1f")
    body = assert_envelope(response, 404, "FAILURE", "REC_NOT_FOUND")
    assert body["message"] == "추천 결과를 찾을 수 없어요. 다시 추천받아 주세요."


def test_internal_error_hides_exception(client):
    response = client.get("/probe/boom")
    body = assert_envelope(response, 500, "FAILURE", "INTERNAL_ERROR")
    assert "secret" not in response.text and "SELECT" not in response.text
    assert body["errors"] == []


def test_db_unavailable(client):
    response = client.get("/probe/db")
    assert_envelope(response, 503, "FAILURE", "DB_UNAVAILABLE")
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("server_token", "sent"),
    [("t0ken", None), ("t0ken", "wrong"), ("", "anything")],
    ids=["missing", "mismatch", "server-token-unset"],
)
def test_unauthorized(client, monkeypatch, server_token, sent):
    monkeypatch.setattr(config, "ADMIN_TOKEN", server_token)
    headers = {"X-Admin-Token": sent} if sent is not None else {}
    assert_envelope(client.get("/probe/admin", headers=headers), 401, "FAILURE", "UNAUTHORIZED")


def test_admin_token_accepted(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "t0ken")
    response = client.get("/probe/admin", headers={"X-Admin-Token": "t0ken"})
    assert assert_envelope(response, 200, "SUCCESS", "OK")["data"] == {"admin": True}


# ── 422 사유 코드 매핑 (8.1.1 사유 코드표 11종) ──────────────────


@pytest.mark.parametrize(
    ("patch", "field", "reason", "rejected"),
    [
        ({"industry_code": None}, "industry_code", "REQUIRED", None),
        ({"age": "스물"}, "age", "INVALID_TYPE", "스물"),
        ({"industry_code": "XX1"}, "industry_code", "INVALID_FORMAT", "XX1"),
        ({"age": 120}, "age", "OUT_OF_RANGE", 120),
        ({"nickname": "a"}, "nickname", "TOO_SHORT", "a"),
        ({"certificates": [""]}, "certificates.0", "TOO_SHORT", ""),
        ({"nickname": "abcdef"}, "nickname", "TOO_LONG", "abcdef"),
        ({"dong_codes": []}, "dong_codes", "TOO_FEW", []),
        ({"dong_codes": ["11110515"] * 5}, "dong_codes", "TOO_MANY", ["11110515"] * 5),
        ({"dong_codes": ["11110515", "11110530", "1111"]}, "dong_codes.2", "INVALID_FORMAT", "1111"),
        ({"dong_codes": ["11110515", "11110515"]}, "dong_codes", "DUPLICATE", ["11110515", "11110515"]),
        ({"district_code": "99999"}, "district_code", "UNKNOWN_CODE", "99999"),
        ({"kind": "z"}, "kind", "INVALID_VALUE", "z"),
    ],
)
def test_validation_reasons(client, patch, field, reason, rejected):
    payload = {**VALID, **patch}
    payload = {k: v for k, v in payload.items() if v is not None}
    body = assert_envelope(client.post("/probe/body", json=payload), 422, "FAILURE", "VALIDATION_ERROR")
    assert body["message"] == "입력한 값을 다시 확인해 주세요."
    assert len(body["errors"]) == 1
    error = body["errors"][0]
    assert (error["field"], error["reason"], error["rejected_value"]) == (field, reason, rejected)
    assert error["message"]


def test_validation_collects_multiple_errors_with_range_message(client):
    response = client.post("/probe/body", json={"age": 120})
    body = assert_envelope(response, 422, "FAILURE", "VALIDATION_ERROR")
    errors = {e["field"]: e for e in body["errors"]}
    assert errors["age"]["reason"] == "OUT_OF_RANGE"
    # 칸별 문구(FIELD_MESSAGES)가 사유별 기본 문구보다 먼저다
    assert errors["age"]["message"] == "나이는 15~99세 사이로 입력해 주세요."
    assert errors["industry_code"]["reason"] == "REQUIRED"
    assert errors["industry_code"]["message"] == "희망 업종을 선택해 주세요."


def test_default_range_message_when_no_field_message():
    from api.errors import _field_message_for

    assert _field_message_for("unknown_field", "OUT_OF_RANGE", {"le": 99}) == "99 이하로 입력해 주세요."
    assert _field_message_for("unknown_field", "OUT_OF_RANGE", {"ge": 1}) == "1 이상으로 입력해 주세요."
    message = _field_message_for("certificates.3", "TOO_LONG", {"max_length": 50})
    assert message == "자격증 이름은 50자 이하로 입력해 주세요."  # 목록 칸 certificates.* 문구


def test_validation_on_query_and_path(client):
    body = assert_envelope(client.get("/probe/list?limit=0"), 422, "FAILURE", "VALIDATION_ERROR")
    assert body["errors"][0] | {"message": None} == {
        "field": "limit", "reason": "OUT_OF_RANGE", "message": None, "rejected_value": "0"
    }
    body = assert_envelope(client.get("/probe/rec/not-a-uuid"), 422, "FAILURE", "VALIDATION_ERROR")
    assert (body["errors"][0]["field"], body["errors"][0]["reason"]) == ("rec_id", "INVALID_TYPE")


# ── request_id ───────────────────────────────────────────────


def test_request_id_generated(client):
    response = client.get("/health")
    assert response.headers["X-Request-ID"].startswith("req_")
    assert len(response.headers["X-Request-ID"]) == len("req_") + 10


def test_request_id_reuses_client_value(client):
    response = client.get("/health", headers={"X-Request-ID": "fe-abc_123.4"})
    assert response.headers["X-Request-ID"] == "fe-abc_123.4"
    assert response.json()["meta"]["request_id"] == "fe-abc_123.4"


def test_request_id_rejects_strange_client_value(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id <script>"})
    assert response.headers["X-Request-ID"].startswith("req_")


@pytest.mark.parametrize("path", ["/probe/boom", "/probe/db", "/nope"])
def test_request_id_on_failures_uses_client_value(client, path):
    response = client.get(path, headers={"X-Request-ID": "trace-1"})
    assert response.headers["X-Request-ID"] == "trace-1"
    assert response.json()["meta"]["request_id"] == "trace-1"


def test_request_ids_differ_between_requests(client):
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]
    assert first != second
