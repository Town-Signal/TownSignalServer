"""⑦ search · ⑧ districts · ⑨ dongs · ⑩ industries (명세 8.4 · 8.6). 데이터는 data/dev 더미.

8.6 '엔드포인트별 오류 코드' 표 대응:
- ⑦ · ⑧ · ⑩: 공통 코드만 → 405 · 503을 엔드포인트마다 확인(나머지 공통 코드는 test_envelope)
- ⑨: VALIDATION_ERROR(district_code 누락 · 형식) + 가정 UNKNOWN_CODE
- ⑩: 가정 — category 값 오류 422 INVALID_VALUE
"""

import pytest

ENVELOPE_KEYS = {"status", "code", "message", "data", "errors", "warnings", "meta"}
GWANAK, SONGPA = "11620", "11710"
SINSA_GWANAK, SINSA_GANGNAM, WIRYE = "11620903", "11680902", "11710903"


def ok_data(response) -> dict:
    body = response.json()
    assert response.status_code == 200, body
    assert set(body) == ENVELOPE_KEYS
    assert (body["status"], body["code"], body["errors"]) == ("SUCCESS", "OK", [])
    assert body["meta"]["base_quarter"] is None and body["meta"]["model_version"] is None
    return body["data"]


def failure(response, http_status: int, code: str) -> dict:
    body = response.json()
    assert response.status_code == http_status, body
    assert set(body) == ENVELOPE_KEYS and "detail" not in body
    assert (body["status"], body["code"], body["data"]) == ("FAILURE", code, None)
    return body


def single_error(response) -> dict:
    body = failure(response, 422, "VALIDATION_ERROR")
    assert len(body["errors"]) == 1
    return body["errors"][0]


# ── ⑦ search ────────────────────────────────────────────────


def names(data) -> list[str]:
    return [item["dong_name"] for item in data["items"]]


def test_search_prefix_then_alphabetical(api_client):
    data = ok_data(api_client.get("/api/regions/search", params={"q": "신"}))
    assert names(data) == ["신도림동", "신림동", "신사동", "신사동", "신월1동"]
    assert data["total"] == 5


def test_search_same_name_distinguished_by_dong_code(api_client):
    items = ok_data(api_client.get("/api/regions/search", params={"q": "신사"}))["items"]
    assert [(i["dong_code"], i["dong_name"], i["district_name"]) for i in items] == [
        (SINSA_GWANAK, "신사동", "관악구"),
        (SINSA_GANGNAM, "신사동", "강남구"),
    ]
    assert items[0]["geo_code"] != items[1]["geo_code"]


def test_search_partial_match_after_prefix(api_client):
    items = ok_data(api_client.get("/api/regions/search", params={"q": "사"}))["items"]
    assert [(i["dong_name"], i["dong_code"]) for i in items] == [
        ("사직동", "11110902"),
        ("신사동", SINSA_GWANAK),
        ("신사동", SINSA_GANGNAM),
    ]


@pytest.mark.parametrize(
    "params",
    [{"q": ""}, {"q": "   "}, {"q": "\t \u3000"}, {}],
    ids=["empty", "spaces", "tab-ideographic", "none"],
)
def test_search_blank_query_returns_empty(api_client, params):
    """공백만 입력하면 오류가 아니라 빈 목록이다(8.6 '0자면 빈 items')."""
    assert ok_data(api_client.get("/api/regions/search", params=params)) == {"items": [], "total": 0}


def test_search_ignores_inner_spaces(api_client):
    assert names(ok_data(api_client.get("/api/regions/search", params={"q": " 신 림 "}))) == ["신림동"]


def test_search_limit_8_and_total(api_client):
    data = ok_data(api_client.get("/api/regions/search", params={"q": "동"}))
    assert len(data["items"]) == 8
    assert data["total"] == 54  # 더미 행정동 이름이 모두 '동'을 포함한다


@pytest.mark.parametrize("q", ["%", "_", "신%"])
def test_search_wildcards_are_literal(api_client, q):
    assert ok_data(api_client.get("/api/regions/search", params={"q": q})) == {"items": [], "total": 0}


def test_search_item_has_geo_code(api_client):
    items = ok_data(api_client.get("/api/regions/search", params={"q": "위례"}))["items"]
    assert items == [
        {
            "dong_code": WIRYE,
            "dong_name": "위례동",
            "district_code": SONGPA,
            "district_name": "송파구",
            "type": "dong",
            "geo_code": None,
        }
    ]


def test_search_db_unavailable(dead_db_client):
    failure(dead_db_client.get("/api/regions/search", params={"q": "신"}), 503, "DB_UNAVAILABLE")


def test_search_method_not_allowed(api_client):
    failure(api_client.post("/api/regions/search"), 405, "METHOD_NOT_ALLOWED")


# ── ⑧ districts ─────────────────────────────────────────────


def test_districts_list(api_client):
    data = ok_data(api_client.get("/api/regions/districts"))
    items = data["items"]
    assert data["total"] == len(items) == 25
    assert [i["district_code"] for i in items] == sorted(i["district_code"] for i in items)
    confidence = {i["district_code"]: i["rent_confidence"] for i in items}
    assert confidence["11320"] == "없음"  # 도봉구
    assert confidence["11545"] == confidence["11305"] == "낮음"  # 금천구 · 강북구
    assert sum(c == "높음" for c in confidence.values()) == 22
    assert all(len(i["geo_code"]) == 5 for i in items)
    assert items[0] == {
        "district_code": "11110", "name": "종로구", "rent_confidence": "높음", "geo_code": "11010"
    }


def test_districts_db_unavailable(dead_db_client):
    failure(dead_db_client.get("/api/regions/districts"), 503, "DB_UNAVAILABLE")


def test_districts_method_not_allowed(api_client):
    failure(api_client.delete("/api/regions/districts"), 405, "METHOD_NOT_ALLOWED")


# ── ⑨ dongs ─────────────────────────────────────────────────


def test_dongs_of_gwanak(api_client):
    data = ok_data(api_client.get("/api/regions/dongs", params={"district_code": GWANAK}))
    assert [(i["name"], i["dong_code"], i["geo_code"]) for i in data["items"]] == [
        ("낙성대동", "11620902", "1121058"),
        ("신림동", "11620901", "1121069"),
        ("신사동", SINSA_GWANAK, "1121068"),
    ]
    assert data["total"] == 3
    assert {i["district_code"] for i in data["items"]} == {GWANAK}


def test_dongs_geo_code_null_for_wirye(api_client):
    items = ok_data(api_client.get("/api/regions/dongs", params={"district_code": SONGPA}))["items"]
    geo = {i["name"]: i["geo_code"] for i in items}
    assert geo == {"거여1동": "1124053", "위례동": None, "풍납1동": "1124051"}


def test_dongs_requires_district_code(api_client):
    error = single_error(api_client.get("/api/regions/dongs"))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("district_code", "REQUIRED", None)
    assert error["message"] == "자치구를 선택해 주세요."


@pytest.mark.parametrize("value", ["1162", "116200", "1162a", ""])
def test_dongs_district_code_format(api_client, value):
    error = single_error(api_client.get("/api/regions/dongs", params={"district_code": value}))
    assert (error["field"], error["reason"]) == ("district_code", "INVALID_FORMAT")
    assert error["rejected_value"] == value


def test_dongs_unknown_district_code(api_client):
    error = single_error(api_client.get("/api/regions/dongs", params={"district_code": "99999"}))
    assert (error["field"], error["reason"]) == ("district_code", "UNKNOWN_CODE")
    assert error["rejected_value"] == "99999"


def test_dongs_db_unavailable(dead_db_client):
    response = dead_db_client.get("/api/regions/dongs", params={"district_code": GWANAK})
    failure(response, 503, "DB_UNAVAILABLE")


# ── ⑩ industries ────────────────────────────────────────────


def test_industries_all(api_client):
    data = ok_data(api_client.get("/api/regions/industries"))
    assert [(i["category"], i["name"]) for i in data["items"]] == [
        ("외식업", "분식전문점"),
        ("외식업", "커피-음료"),
        ("외식업", "한식음식점"),
        ("서비스업", "외국어학원"),
        ("서비스업", "일반교습학원"),
        ("소매업", "슈퍼마켓"),
        ("소매업", "편의점"),
    ]
    assert data["total"] == 7


@pytest.mark.parametrize(("category", "count"), [("외식업", 3), ("서비스업", 2), ("소매업", 2)])
def test_industries_by_category(api_client, category, count):
    data = ok_data(api_client.get("/api/regions/industries", params={"category": category}))
    assert data["total"] == count and {i["category"] for i in data["items"]} == {category}


@pytest.mark.parametrize("value", ["음식점", "서비스", ""])
def test_industries_invalid_category_is_422(api_client, value):
    error = single_error(api_client.get("/api/regions/industries", params={"category": value}))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("category", "INVALID_VALUE", value)


def test_industries_db_unavailable(dead_db_client):
    failure(dead_db_client.get("/api/regions/industries"), 503, "DB_UNAVAILABLE")


def test_industries_method_not_allowed(api_client):
    failure(api_client.put("/api/regions/industries"), 405, "METHOD_NOT_ALLOWED")


# ── 공통 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/regions/search?q=신",
        "/api/regions/districts",
        f"/api/regions/dongs?district_code={GWANAK}",
        "/api/regions/industries",
        "/api/certificates",
        "/health",
    ],
)
def test_list_responses_are_envelopes(api_client, path):
    data = ok_data(api_client.get(path))
    if path == "/health":
        assert set(data) == {"service_status", "env"}
        return
    assert set(data) == {"items", "total"}
    assert isinstance(data["items"], list) and data["items"]
    if "search" not in path:
        assert data["total"] == len(data["items"])
