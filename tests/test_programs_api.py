"""③ GET /api/programs/{program_id} · ① GET /api/programs/upcoming (명세 8.2 · 8.6). 데이터는 data/dev 더미.

8.6 '엔드포인트별 오류 코드' 표 대응:
- ③ PROGRAM_NOT_FOUND, VALIDATION_ERROR(program_id 형식), 공통 DB_UNAVAILABLE
- ① VALIDATION_ERROR(limit), 공통 DB_UNAVAILABLE · METHOD_NOT_ALLOWED
"""

from datetime import date

import pytest
from sqlalchemy import text

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


def add_program(conn, name, amount, apply_end, verified="검수자"):
    conn.execute(
        text(
            "INSERT INTO support_program (name, amount_max, eligibility, source_url, verified_by, apply_end) "
            "VALUES (:name, :amount, '{\"and\": []}', :url, :verified, :end)"
        ),
        {"name": name, "amount": amount, "url": f"https://example.test/{name}", "verified": verified,
         "end": apply_end},
    )


def test_upcoming_order_and_filter(api_client):
    assert FIXED_TODAY == date(2026, 10, 5)
    body = success(api_client.get("/api/programs/upcoming"))
    # 모집 중 마감 가까운 순, 그다음 마감된 #4. 기간 없는 #3 · 미검수 #10은 없다
    assert ids(body) == [5, 2, 8, 1, 9, 7, 6, 11, 4]
    assert body["data"]["total"] == 9
    first, last = body["data"]["items"][0], body["data"]["items"][-1]
    pick = ("district_name", "apply_end", "status")
    assert tuple(first[k] for k in pick) == ("관악구", "2026-10-06", "모집 중")
    assert tuple(last[k] for k in pick) == ("전국", "2025-12-31", "마감")
    assert all("always_open" not in item for item in body["data"]["items"])
    assert body["meta"]["base_quarter"] is None


def test_upcoming_item_shape(api_client):
    item = success(api_client.get("/api/programs/upcoming"))["data"]["items"][0]
    assert set(item) == {
        "program_id", "name", "agency", "amount_max", "district_code", "district_name",
        "apply_start", "apply_end", "source_url", "status",
    }


def test_upcoming_deadline_today_is_open(api_client):
    api_client.app.dependency_overrides[get_today] = lambda: date(2026, 10, 6)  # #5 마감일
    first = success(api_client.get("/api/programs/upcoming"))["data"]["items"][0]
    assert (first["program_id"], first["status"]) == (5, "모집 중")


def test_upcoming_closed_after_open_most_recent_first(api_client):
    api_client.app.dependency_overrides[get_today] = lambda: date(2026, 10, 7)
    body = success(api_client.get("/api/programs/upcoming"))
    assert ids(body) == [2, 8, 1, 9, 7, 6, 11, 5, 4]  # #5(10-06 마감)가 #4(2025-12-31)보다 먼저
    assert [i["status"] for i in body["data"]["items"]] == ["모집 중"] * 7 + ["마감"] * 2


def test_upcoming_all_closed(api_client):
    api_client.app.dependency_overrides[get_today] = lambda: date(2027, 1, 1)
    body = success(api_client.get("/api/programs/upcoming"))
    assert ids(body) == [11, 6, 7, 9, 1, 8, 2, 5, 4] and body["data"]["total"] == 9
    assert {i["status"] for i in body["data"]["items"]} == {"마감"}


def test_upcoming_excludes_zero_amount_and_no_period(api_client, dummy_db):
    add_program(dummy_db, "금액 0 공간 지원", 0, date(2026, 10, 20))
    add_program(dummy_db, "기간 없는 현금 지원", 5_000_000, None)
    add_program(dummy_db, "미검수 현금 지원", 5_000_000, date(2026, 10, 20), verified=None)
    body = success(api_client.get("/api/programs/upcoming"))
    assert body["data"]["total"] == 9
    assert ids(body) == [5, 2, 8, 1, 9, 7, 6, 11, 4]


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
