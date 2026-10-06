"""② POST /api/programs/calculate-budgets (명세 7.1 ~ 7.5 · 8.2 ② · 8.6). 데이터는 data/dev 더미.

8.6 '엔드포인트별 오류 코드' 표 대응:
- VALIDATION_ERROR(age · capital · career_years · industry_code · certificates · target_area_sqm)
- 경고 SUPPORT_PROGRAM_UNAVAILABLE · CERTIFICATE_NOT_RECOGNIZED · PARTIAL_DISTRICT_ERROR
- 공통 BAD_REQUEST · DB_UNAVAILABLE · METHOD_NOT_ALLOWED
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from api import config
from api.repositories import program_repository
from api.services import budget_service, certificate_llm
from tests.api_helpers import failure, single_error, success, warning_codes

URL = "/api/programs/calculate-budgets"
JONGNO, GWANAK, MAPO, GANGNAM, GEUMCHEON, SEONGDONG, DOBONG, SEOCHO = (
    "11110", "11620", "11440", "11680", "11545", "11200", "11320", "11650",
)
# 기준 입력: 나이 27 · 경력 0 · 자격증 '한식조리사'(→ 조리기능사) · 한식음식점(외식업) · 33㎡
BASE = {
    "age": 27,
    "capital": 30_000_000,
    "industry_code": "CS100001",
    "career_years": 0,
    "certificates": ["한식조리사"],
    "target_area_sqm": 33.0,
}


def calc(client, **overrides) -> dict:
    return success(client.post(URL, json={**BASE, **overrides}))


def by_district(body: dict) -> dict[str, dict]:
    return {d["district_code"]: d for d in body["data"]["district_budgets"]}


def program_ids(items) -> list[int]:
    return [p["program_id"] for p in items]


# ── 성공 · 계산값 고정 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("district", "support"),
    [
        (JONGNO, 24_000_000),  # A 2,000 + 역량 300 + 바우처 100
        (GWANAK, 32_000_000),  # + 관악 800
        (MAPO, 29_000_000),  # 마포 배타 2,500 + 300 + 100 (A · B 제외)
        (GANGNAM, 24_000_000),  # 강남 배타는 A와 동률 → A
        (GEUMCHEON, 33_000_000),  # + 금천 외식업 900
        (SEONGDONG, 30_000_000),  # + 성동 600
    ],
)
def test_budget_reference_table(api_client, district, support):
    """S5 참고 계산표를 API 응답으로 고정한다."""
    row = by_district(calc(api_client))[district]
    assert row["support_fund_max"] == support
    assert row["available_budget"] == BASE["capital"] + support


def test_budget_gwanak_spec_regression(api_client):
    """명세 7.5 예시: 관악구 33㎡ → 추정 임대비용 16,929,000원.

    가용예산이 55,000,000원이면 예산 여유 38,071,000원, '통과'.
    """
    gwanak = by_district(calc(api_client, capital=23_000_000))[GWANAK]
    assert gwanak["available_budget"] == 55_000_000
    assert gwanak["rent_per_sqm"] == 28.5
    assert gwanak["monthly_rent"] == 940_500
    assert gwanak["estimated_rent_cost"] == 16_929_000
    assert gwanak["budget_margin"] == 38_071_000
    assert gwanak["passed"] == "통과"
    assert gwanak["unavailable_reason"] is None


def test_budget_age_60_gets_600man(api_client):
    body = calc(api_client, age=60, industry_code="CS200001", certificates=[])
    jongno = by_district(body)[JONGNO]
    assert jongno["support_fund_max"] == 6_000_000
    assert program_ids(jongno["matched_programs"]) == [4, 11]


def test_budget_25_districts_each_matched(api_client):
    rows = by_district(calc(api_client))
    assert len(rows) == 25
    district_only = {5: GWANAK, 6: MAPO, 7: GEUMCHEON, 8: SEONGDONG, 9: GANGNAM}
    for code, row in rows.items():
        seen = set(program_ids(row["matched_programs"])) | set(program_ids(row["excluded_programs"]))
        for program_id, owner in district_only.items():
            assert (program_id in seen) == (code == owner), (code, program_id)


def test_budget_only_verified_and_closed_included(api_client):
    for row in by_district(calc(api_client)).values():
        matched = program_ids(row["matched_programs"])
        assert 10 not in matched and 10 not in program_ids(row["excluded_programs"])  # 미검수
        assert 4 in matched  # 2025-12-31에 마감된 공고도 매칭(7.3)
    jongno = by_district(calc(api_client))[JONGNO]
    closed = next(p for p in jongno["matched_programs"] if p["program_id"] == 4)
    assert closed["apply_end"] == "2025-12-31"


def test_budget_exclusion_reasons(api_client):
    rows = by_district(calc(api_client))
    assert rows[JONGNO]["excluded_programs"] == [
        {
            "program_id": 2,
            "name": "[더미] 청년창업 초기자금 B",
            "amount": 7_000_000,
            "exclude_reason": "더 큰 지원금인 [더미] 청년창업 정착지원금 A(2,000만 원)와 중복 수혜 불가",
        }
    ]
    assert program_ids(rows[MAPO]["excluded_programs"]) == [1, 2]
    mapo_reasons = [e["exclude_reason"] for e in rows[MAPO]["excluded_programs"]]
    assert all("마포구 청년 가게 지원(2,500만 원)" in reason for reason in mapo_reasons)
    assert 1 in program_ids(rows[GANGNAM]["matched_programs"])
    assert program_ids(rows[GANGNAM]["excluded_programs"]) == [2, 9]


def test_budget_dobong_unknown_not_excluded(api_client):
    body = calc(api_client)
    dobong = by_district(body)[DOBONG]
    assert dobong["passed"] == "확인불가"
    for key in ("estimated_rent_cost", "budget_margin", "monthly_rent", "rent_per_sqm"):
        assert dobong[key] is None, key  # 0이 아니라 null
    assert (dobong["unavailable_reason"], dobong["rent_confidence"]) == ("RENT_DATA_MISSING", "없음")
    assert dobong["available_budget"] == BASE["capital"] + dobong["support_fund_max"]
    summary = body["data"]["summary"]
    # 도봉구가 '감당 가능한 구'(통과 + 확인불가)에 들어간다
    assert summary["eligible_district_count"] == summary["passed_district_count"] + 1


def test_budget_no_match_budget_is_capital(api_client, dummy_db):
    dummy_db.execute(text("DELETE FROM support_program"))  # 테스트 SAVEPOINT 안 — 끝나면 되돌린다
    body = calc(api_client)
    assert body["data"]["degraded"] is False and body["warnings"] == []
    for row in body["data"]["district_budgets"]:
        assert (row["support_fund_max"], row["available_budget"]) == (0, BASE["capital"])
        assert row["matched_programs"] == [] and row["excluded_programs"] == []


def test_budget_summary_counts_and_order(api_client):
    body = calc(api_client, capital=0)
    rows = body["data"]["district_budgets"]
    summary = body["data"]["summary"]
    passed = [r["passed"] for r in rows]
    assert summary == {
        "passed_district_count": passed.count("통과"),
        "eligible_district_count": passed.count("통과") + passed.count("확인불가"),
        "excluded_district_count": passed.count("제외"),
    }
    counted = summary["passed_district_count"] + summary["excluded_district_count"] + passed.count("확인불가")
    assert counted == 25
    assert summary["excluded_district_count"] > 0  # 자본금 0이면 비싼 구는 제외된다
    assert [(r["available_budget"], r["district_code"]) for r in rows] == sorted(
        ((r["available_budget"], r["district_code"]) for r in rows), key=lambda x: (-x[0], x[1])
    )


def test_budget_saves_recommendation(api_client, dummy_db):
    body = calc(api_client)
    data = body["data"]
    rec_id = uuid.UUID(data["rec_id"])
    assert rec_id.version == 4
    row = dummy_db.execute(
        text("SELECT input_condition, calculated_budgets FROM recommendation WHERE rec_id = :id"),
        {"id": rec_id},
    ).one()
    assert row.input_condition["normalized_certificates"] == ["조리기능사"]
    assert row.input_condition["certificates"] == ["한식조리사"]
    assert row.input_condition["industry_code"] == "CS100001"
    assert row.calculated_budgets == {k: v for k, v in data.items() if k != "rec_id"}


def test_budget_normalizes_certificates(api_client):
    data = calc(api_client)["data"]
    assert data["normalized_certificates"] == ["조리기능사"]
    assert 3 in program_ids(by_district(calc(api_client))[JONGNO]["matched_programs"])  # 조리기능사 조건 사업
    without = by_district(calc(api_client, certificates=[]))[JONGNO]
    assert 3 not in program_ids(without["matched_programs"])


def test_budget_meta_base_quarter(api_client):
    body = calc(api_client)
    assert body["meta"]["base_quarter"] == body["data"]["base_quarter"] == "2026Q2"
    assert body["meta"]["model_version"] is None
    assert body["data"]["own_capital"] == BASE["capital"]


# ── 자격증 LLM 폴백 플래그 ───────────────────────────────────


def test_budget_llm_fallback_disabled_by_default(monkeypatch):
    assert config.CERT_LLM_FALLBACK_ENABLED is False  # 테스트 · 기본 설정은 꺼져 있다
    assert certificate_llm.build_mapper() is None
    monkeypatch.setattr(config, "CERT_LLM_FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    assert certificate_llm.build_mapper() is None  # 켜도 키가 없으면 쓰지 않는다


def test_budget_llm_mapper_injected(api_client, monkeypatch):
    def fake_mapper(text, standards):
        return "제빵기능사" if text == "빵 자격증" else None

    monkeypatch.setattr(certificate_llm, "build_mapper", lambda: fake_mapper)
    body = calc(api_client, certificates=["빵 자격증"])
    assert body["data"]["normalized_certificates"] == ["제빵기능사"]
    assert body["warnings"] == []


def test_budget_llm_failure_does_not_block_submit(api_client, monkeypatch):
    """플래그를 켜도 매퍼가 실패하면(현재는 미구현 → 예외) 원문을 쓰고 계산은 그대로 된다."""
    monkeypatch.setattr(config, "CERT_LLM_FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    body = calc(api_client, certificates=["빵 자격증"])
    assert body["data"]["normalized_certificates"] == ["빵 자격증"]
    assert warning_codes(body) == ["CERTIFICATE_NOT_RECOGNIZED"]


# ── 경고 코드 ─────────────────────────────────────────────────


def test_budget_degraded_when_program_query_fails(api_client, monkeypatch):
    def broken(session):
        raise ProgrammingError("SELECT … FROM support_program", {}, Exception("relation is broken"))

    monkeypatch.setattr(program_repository, "list_matchable_programs", broken)
    body = calc(api_client)
    data = body["data"]
    assert data["degraded"] is True
    assert body["warnings"] == [
        {
            "code": "SUPPORT_PROGRAM_UNAVAILABLE",
            "message": "지원사업 정보를 불러오지 못해 지원금 없이 계산했어요.",
        }
    ]
    for row in data["district_budgets"]:
        assert (row["support_fund_max"], row["available_budget"]) == (0, BASE["capital"])
    assert uuid.UUID(data["rec_id"])  # 지원금 없이도 rec_id는 발급된다
    assert by_district(body)[GWANAK]["estimated_rent_cost"] == 16_929_000  # 임대료 계산은 그대로


def test_budget_unrecognized_certificate_warning(api_client):
    body = calc(api_client, certificates=["한식조리사", "드론 조종사"])
    assert body["data"]["normalized_certificates"] == ["조리기능사", "드론 조종사"]
    assert warning_codes(body) == ["CERTIFICATE_NOT_RECOGNIZED"]
    assert "드론 조종사" in body["warnings"][0]["message"]


def test_budget_partial_district_error(api_client, monkeypatch):
    original = budget_service.match_district

    def fail_for_seocho(programs, applicant, district_code):
        if district_code == SEOCHO:
            raise RuntimeError("서초구만 계산 실패")
        return original(programs, applicant, district_code)

    monkeypatch.setattr(budget_service, "match_district", fail_for_seocho)
    body = calc(api_client)
    rows = by_district(body)
    assert warning_codes(body) == ["PARTIAL_DISTRICT_ERROR"]
    seocho = rows[SEOCHO]
    assert (seocho["unavailable_reason"], seocho["support_fund_max"]) == ("SUPPORT_DATA_ERROR", 0)
    assert seocho["estimated_rent_cost"] is not None  # 임대료 판정은 그대로
    assert rows[GWANAK]["support_fund_max"] == 32_000_000 and rows[GWANAK]["unavailable_reason"] is None
    assert body["data"]["degraded"] is False


# ── VALIDATION_ERROR ─────────────────────────────────────────


def post(client, **overrides):
    return client.post(URL, json={**BASE, **overrides})


@pytest.mark.parametrize("age", [14, 100])
def test_budget_age_out_of_range(api_client, age):
    error = single_error(post(api_client, age=age))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("age", "OUT_OF_RANGE", age)
    assert error["message"] == "나이는 15~99세 사이로 입력해 주세요."


def test_budget_age_invalid_type(api_client):
    error = single_error(post(api_client, age="스물"))
    assert (error["field"], error["reason"]) == ("age", "INVALID_TYPE")


def test_budget_capital_negative(api_client):
    error = single_error(post(api_client, capital=-1))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("capital", "OUT_OF_RANGE", -1)


def test_budget_capital_required(api_client):
    payload = {k: v for k, v in BASE.items() if k != "capital"}
    error = single_error(api_client.post(URL, json=payload))
    assert (error["field"], error["reason"]) == ("capital", "REQUIRED")


def test_budget_career_negative(api_client):
    error = single_error(post(api_client, career_years=-1))
    assert (error["field"], error["reason"]) == ("career_years", "OUT_OF_RANGE")


def test_budget_industry_required(api_client):
    payload = {k: v for k, v in BASE.items() if k != "industry_code"}
    error = single_error(api_client.post(URL, json=payload))
    assert (error["field"], error["reason"], error["message"]) == (
        "industry_code", "REQUIRED", "희망 업종을 선택해 주세요.",
    )


def test_budget_industry_format(api_client):
    error = single_error(post(api_client, industry_code="CS1"))
    assert (error["field"], error["reason"]) == ("industry_code", "INVALID_FORMAT")


def test_budget_industry_unknown(api_client, dummy_db):
    before = dummy_db.scalar(text("SELECT count(*) FROM recommendation"))
    error = single_error(post(api_client, industry_code="CS999999"))
    assert (error["field"], error["reason"]) == ("industry_code", "UNKNOWN_CODE")
    assert error["rejected_value"] == "CS999999"
    assert dummy_db.scalar(text("SELECT count(*) FROM recommendation")) == before  # 저장하지 않는다


def test_budget_certificates_too_many(api_client):
    error = single_error(post(api_client, certificates=[f"자격증{i}" for i in range(21)]))
    assert (error["field"], error["reason"], error["message"]) == (
        "certificates", "TOO_MANY", "자격증은 20개까지 넣을 수 있어요.",
    )


def test_budget_certificate_blank(api_client):
    error = single_error(post(api_client, certificates=["   "]))
    assert (error["field"], error["reason"]) == ("certificates.0", "TOO_SHORT")


def test_budget_certificate_too_long(api_client):
    error = single_error(post(api_client, certificates=["조리기능사", "가" * 51]))
    assert (error["field"], error["reason"], error["message"]) == (
        "certificates.1", "TOO_LONG", "자격증 이름은 50자 이하로 입력해 주세요.",
    )


@pytest.mark.parametrize("area", [0, -5, 1000.1])
def test_budget_area_out_of_range(api_client, area):
    error = single_error(post(api_client, target_area_sqm=area))
    assert (error["field"], error["reason"]) == ("target_area_sqm", "OUT_OF_RANGE")


def test_budget_multiple_errors_like_spec_example(api_client):
    """8.1.1 실패 예시: 나이 범위 초과 + 업종 누락."""
    payload = {k: v for k, v in BASE.items() if k != "industry_code"} | {"age": 120}
    body = failure(api_client.post(URL, json=payload), 422, "VALIDATION_ERROR")
    assert body["message"] == "입력한 값을 다시 확인해 주세요."
    assert {(e["field"], e["reason"], e["message"], e["rejected_value"]) for e in body["errors"]} == {
        ("age", "OUT_OF_RANGE", "나이는 15~99세 사이로 입력해 주세요.", 120),
        ("industry_code", "REQUIRED", "희망 업종을 선택해 주세요.", None),
    }


# ── 공통 코드 ─────────────────────────────────────────────────


def test_budget_body_not_json(api_client):
    response = api_client.post(URL, content=b"{age: 27", headers={"Content-Type": "application/json"})
    failure(response, 400, "BAD_REQUEST")


def test_budget_db_unavailable(dead_db_client):
    failure(dead_db_client.post(URL, json=BASE), 503, "DB_UNAVAILABLE")


def test_budget_method_not_allowed(api_client):
    # GET은 /api/programs/{program_id}와 경로가 겹쳐 422(program_id 형식)가 된다.
    # 그래서 다른 경로와 겹치지 않는 PUT · DELETE로 405를 확인한다
    failure(api_client.put(URL, json=BASE), 405, "METHOD_NOT_ALLOWED")
    failure(api_client.delete(URL), 405, "METHOD_NOT_ALLOWED")
