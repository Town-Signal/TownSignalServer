"""③ GET /api/programs/{program_id} · ① GET /api/programs/upcoming (명세 8.2 · 8.6). 데이터는 data/dev 더미.

8.6 '엔드포인트별 오류 코드' 표 대응:
- ③ PROGRAM_NOT_FOUND, VALIDATION_ERROR(program_id 형식), 공통 DB_UNAVAILABLE
- ① VALIDATION_ERROR(limit), 공통 DB_UNAVAILABLE · METHOD_NOT_ALLOWED
"""

from datetime import date

import pytest

from api.deps import get_today
from tests.api_helpers import FIXED_TODAY, failure, single_error, success

# ── ③ program 상세 ───────────────────────────────────────────


def test_program_detail_ok(api_client):
    data = success(api_client.get("/api/programs/1"))["data"]
    assert data == {
        "program_id": 1,
        "name": "[더미] 청년창업 정착지원금 A",
        "agency": "더미 기관(중앙)",
        "amount_max": 20_000_000,
        "district_code": None,
        "district_name": "전국",
        "apply_start": "2026-09-01",
        "apply_end": "2026-10-24",
        "always_open": False,
        "source_url": "https://example.com/townsignal-dummy/programs/national_exclusive_a",
        "is_exclusive": True,
        "eligibility": {"and": [{"field": "age", "op": "<=", "value": 39}]},
        "raw_text": "[더미] 공고 원문 없음 — 전국 배타 사업 1 — 금액이 큰 쪽(2,000만)",
    }


def test_program_detail_district_name(api_client):
    data = success(api_client.get("/api/programs/5"))["data"]
    assert (data["district_code"], data["district_name"]) == ("11620", "관악구")
    always_open = success(api_client.get("/api/programs/3"))["data"]
    assert (always_open["apply_end"], always_open["always_open"]) == (None, True)


def test_program_detail_not_found(api_client):
    body = failure(api_client.get("/api/programs/9999"), 404, "PROGRAM_NOT_FOUND")
    assert body["message"] == "공고를 찾을 수 없어요."


def test_program_detail_unverified_is_404(api_client):
    failure(api_client.get("/api/programs/10"), 404, "PROGRAM_NOT_FOUND")  # verified_by NULL


def test_program_detail_invalid_id(api_client):
    error = single_error(api_client.get("/api/programs/abc"))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("program_id", "INVALID_TYPE", "abc")


def test_program_detail_db_unavailable(dead_db_client):
    failure(dead_db_client.get("/api/programs/1"), 503, "DB_UNAVAILABLE")


# ── ① upcoming ───────────────────────────────────────────────


def ids(body) -> list[int]:
    return [item["program_id"] for item in body["data"]["items"]]


def test_upcoming_order_and_filter(api_client):
    assert FIXED_TODAY == date(2026, 10, 5)
    body = success(api_client.get("/api/programs/upcoming"))
    # 마감일 오름차순, 상시(#3)는 맨 뒤. 마감된 #4 · 미검수 #10은 없다
    assert ids(body) == [5, 2, 8, 1, 9, 7, 6, 11, 3]
    assert body["data"]["total"] == 9
    first, last = body["data"]["items"][0], body["data"]["items"][-1]
    assert (first["district_name"], first["apply_end"]) == ("관악구", "2026-10-06")
    assert first["always_open"] is False
    assert (last["district_name"], last["apply_end"], last["always_open"]) == ("전국", None, True)
    assert body["meta"]["base_quarter"] is None


def test_upcoming_deadline_today_included(api_client):
    api_client.app.dependency_overrides[get_today] = lambda: date(2026, 10, 6)  # #5 마감일
    assert ids(success(api_client.get("/api/programs/upcoming")))[0] == 5
    api_client.app.dependency_overrides[get_today] = lambda: date(2026, 10, 7)
    assert 5 not in ids(success(api_client.get("/api/programs/upcoming")))


def test_upcoming_only_always_open_after_all_deadlines(api_client):
    api_client.app.dependency_overrides[get_today] = lambda: date(2027, 1, 1)
    body = success(api_client.get("/api/programs/upcoming"))
    assert ids(body) == [3] and body["data"]["total"] == 1


def test_upcoming_limit(api_client):
    body = success(api_client.get("/api/programs/upcoming", params={"limit": 3}))
    assert ids(body) == [5, 2, 8]
    assert body["data"]["total"] == 9  # 자르기 전 개수


@pytest.mark.parametrize("limit", [0, 51])
def test_upcoming_limit_out_of_range(api_client, limit):
    error = single_error(api_client.get("/api/programs/upcoming", params={"limit": limit}))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("limit", "OUT_OF_RANGE", str(limit))


def test_upcoming_limit_invalid_type(api_client):
    error = single_error(api_client.get("/api/programs/upcoming", params={"limit": "x"}))
    assert (error["field"], error["reason"]) == ("limit", "INVALID_TYPE")


def test_upcoming_db_unavailable(dead_db_client):
    failure(dead_db_client.get("/api/programs/upcoming"), 503, "DB_UNAVAILABLE")


def test_upcoming_method_not_allowed(api_client):
    failure(api_client.post("/api/programs/upcoming"), 405, "METHOD_NOT_ALLOWED")
