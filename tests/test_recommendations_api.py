"""④ POST /api/recommendations · ⑤ GET /api/recommendations/{rec_id} · ⑥ …/{rec_id}/margin.

명세 7.6 ~ 7.8 · 8.3 · 8.6 · 판정 14 · 20 · 21 · 22 · 30 · 41. 데이터는 data/dev 더미.
각 테스트가 ②로 rec_id를 먼저 발급한다.

8.6 '엔드포인트별 오류 코드' 표 대응:
- ④ REC_NOT_FOUND, VALIDATION_ERROR(rec_id 형식 · top_k),
  공통 BAD_REQUEST · DB_UNAVAILABLE · METHOD_NOT_ALLOWED
- ⑤ REC_NOT_FOUND, VALIDATION_ERROR(형식), 공통 DB_UNAVAILABLE
- ⑥ REC_NOT_FOUND, DONG_NOT_FOUND, VALIDATION_ERROR(형식), 공통 DB_UNAVAILABLE
"""

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.api_helpers import failure, single_error, success

URL = "/api/recommendations"
BASE = {"age": 27, "capital": 30_000_000, "industry_code": "CS100001", "certificates": ["한식조리사"]}
HOEGI, BANGHAK3, MANGWON1, SILLIM = "11230901", "11320901", "11440903", "11620901"
GWANAK, DOBONG = "11620", "11320"
MISSING_REC = "00000000-0000-4000-8000-000000000000"


def budgets(client, **overrides) -> dict:
    """② 호출 → data(rec_id · district_budgets · summary)."""
    return success(client.post("/api/programs/calculate-budgets", json={**BASE, **overrides}))["data"]


def recommend(client, rec_id: str, **body) -> dict:
    return success(client.post(URL, json={"rec_id": rec_id, **body}))


def expected_candidates(db, step2: dict, industry_code: str = "CS100001") -> list[str]:
    """DB에서 직접 구한 후보 순서.

    통과 + 확인불가 구 × xgb_v1 예측을 total_score↓ · sales_p50↓ · dong_code↑로 정렬.
    """
    eligible = [d["district_code"] for d in step2["district_budgets"] if d["passed"] in ("통과", "확인불가")]
    return list(
        db.execute(
            text(
                "SELECT p.dong_code FROM prediction p JOIN dong d ON d.dong_code = p.dong_code "
                "WHERE p.industry_code = :ind AND p.model_version = 'xgb_v1' "
                "AND d.district_code = ANY(:codes) "
                "ORDER BY p.total_score DESC NULLS LAST, p.sales_p50 DESC NULLS LAST, p.dong_code"
            ),
            {"ind": industry_code, "codes": eligible},
        ).scalars()
    )


def prediction_rows(db, model_version: str = "xgb_v1") -> dict[str, dict]:
    rows = db.execute(
        text("SELECT * FROM prediction WHERE industry_code = 'CS100001' AND model_version = :m"),
        {"m": model_version},
    ).mappings()
    return {r["dong_code"]: dict(r) for r in rows}


def by_dong(body: dict) -> dict[str, dict]:
    return {r["dong_code"]: r for r in body["data"]["recommendations"]}


# ── ④ 성공 · 필수 확인 ────────────────────────────────────────


def test_execute_candidates_and_order(api_client, dummy_db):
    step2 = budgets(api_client, capital=0)  # 자본금 0 → 비싼 구는 '제외'
    excluded = {d["district_code"] for d in step2["district_budgets"] if d["passed"] == "제외"}
    assert excluded  # 제외된 구가 실제로 있다
    body = recommend(api_client, step2["rec_id"], top_k=50)
    rows = body["data"]["recommendations"]
    expected = expected_candidates(dummy_db, step2)
    assert len(expected) < 50  # 후보 전부가 한 페이지에 들어오는 조건
    assert [r["dong_code"] for r in rows] == expected
    assert [r["rank_no"] for r in rows] == list(range(1, len(rows) + 1))
    assert not {r["district_code"] for r in rows} & excluded
    scores = [r["score_breakdown"]["total_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_execute_excludes_no_prediction_dong(api_client):
    step2 = budgets(api_client, capital=0)
    hoegi_district = next(d for d in step2["district_budgets"] if d["district_code"] == HOEGI[:5])
    assert hoegi_district["passed"] == "통과"  # 구는 후보인데
    body = recommend(api_client, step2["rec_id"], top_k=50)
    assert HOEGI not in by_dong(body)  # 예측 행이 없어 빠진다


def test_execute_sample_short_included_as_residential(api_client):
    step2 = budgets(api_client, capital=0)
    banghak = by_dong(recommend(api_client, step2["rec_id"], top_k=50))[BANGHAK3]
    # 순위에는 포함(판정 20)
    assert (banghak["data_status"], banghak["is_residential"]) == ("표본 부족", True)
    assert banghak["passed"] == "확인불가"
    assert banghak["budget_margin"] is None and banghak["estimated_rent_cost"] is None
    assert banghak["rent_confidence"] == "없음"


def test_execute_growth_null_uses_stored_score(api_client, dummy_db):
    step2 = budgets(api_client, capital=0)
    mangwon = by_dong(recommend(api_client, step2["rec_id"], top_k=50))[MANGWON1]
    stored = prediction_rows(dummy_db)[MANGWON1]
    breakdown = mangwon["score_breakdown"]
    assert mangwon["growth_rate"] is None and breakdown["growth_percentile"] is None
    assert breakdown["applied_weights"] == {"sales": 0.5, "survival": 0.5, "growth": 0.0}
    assert breakdown["total_score"] == float(stored["total_score"])


def test_execute_scores_are_stored_values_not_mlp(api_client, dummy_db):
    step2 = budgets(api_client)
    rows = recommend(api_client, step2["rec_id"], top_k=50)["data"]["recommendations"]
    xgb, mlp = prediction_rows(dummy_db, "xgb_v1"), prediction_rows(dummy_db, "mlp_v1")
    assert len({r["dong_code"] for r in rows}) == len(rows) == 50  # 동 중복 없음(mlp 행이 섞이지 않음)
    assert any(xgb[d]["sales_p50"] != mlp[d]["sales_p50"] for d in xgb if d in mlp)  # 둘이 실제로 다르다
    for r in rows:
        stored = xgb[r["dong_code"]]
        b = r["score_breakdown"]
        assert b["total_score"] == float(stored["total_score"])  # 요청 경로에서 다시 계산하지 않는다
        assert (b["sales_percentile"], b["survival_percentile"]) == (
            float(stored["sales_percentile"]), float(stored["survival_percentile"]),
        )
        assert r["sales_quarterly_p50"] == stored["sales_p50"]
        assert r["survival_p50"] == float(stored["survival_p50"])


def test_execute_monthly_sales_and_ranges(api_client, dummy_db):
    step2 = budgets(api_client)
    xgb = prediction_rows(dummy_db)
    for r in recommend(api_client, step2["rec_id"])["data"]["recommendations"]:
        p = xgb[r["dong_code"]]
        assert r["sales_quarterly_range"] == [p["sales_p10"], p["sales_p90"]]
        assert r["sales_monthly_p50"] == p["sales_p50"] // 3
        assert r["sales_monthly_range"] == [p["sales_p10"] // 3, p["sales_p90"] // 3]
        assert r["survival_range"] == [float(p["survival_p10"]), float(p["survival_p90"])]


def test_execute_top_k_default_20_and_max_50(api_client, dummy_db):
    step2 = budgets(api_client)
    assert len(expected_candidates(dummy_db, step2)) == 52
    default = recommend(api_client, step2["rec_id"])["data"]
    assert default["summary"]["recommended_count"] == len(default["recommendations"]) == 20
    maximum = recommend(api_client, step2["rec_id"], top_k=50)["data"]["recommendations"]
    assert [r["rank_no"] for r in maximum] == list(range(1, 51))
    assert [r["dong_code"] for r in maximum] == expected_candidates(dummy_db, step2)[:50]


def test_execute_budget_values_come_from_snapshot(api_client):
    step2 = budgets(api_client, capital=0)
    saved = {d["district_code"]: d for d in step2["district_budgets"]}
    for r in recommend(api_client, step2["rec_id"], top_k=50)["data"]["recommendations"]:
        d = saved[r["district_code"]]
        assert (r["passed"], r["budget_margin"], r["estimated_rent_cost"], r["rent_confidence"]) == (
            d["passed"], d["budget_margin"], d["estimated_rent_cost"], d["rent_confidence"],
        )


def test_execute_reads_snapshot_without_recalculation(api_client, dummy_db):
    step2 = budgets(api_client, capital=0)
    gwanak = next(d for d in step2["district_budgets"] if d["district_code"] == GWANAK)
    # ② 뒤에 원천을 바꿔도 ④는 스냅샷을 읽는다(재계산하지 않는다)
    dummy_db.execute(
        text("UPDATE district_rent SET rent_per_sqm = 999 WHERE district_code = :c"), {"c": GWANAK}
    )
    dummy_db.execute(text("DELETE FROM support_program"))
    rows = recommend(api_client, step2["rec_id"], top_k=50)["data"]["recommendations"]
    gwanak_rows = [r for r in rows if r["district_code"] == GWANAK]
    assert gwanak_rows
    for r in gwanak_rows:
        assert (r["passed"], r["budget_margin"], r["estimated_rent_cost"]) == (
            "통과", gwanak["budget_margin"], gwanak["estimated_rent_cost"],
        )


def test_execute_saves_and_replaces_rec_items(api_client, dummy_db):
    step2 = budgets(api_client)
    rec_id = step2["rec_id"]

    def saved_items():
        return dummy_db.execute(
            text(
                "SELECT rank_no, dong_code, budget_margin, survival_p50, sales_p50 FROM rec_item "
                "WHERE rec_id = :id ORDER BY rank_no"
            ),
            {"id": uuid.UUID(rec_id)},
        ).all()

    rows = recommend(api_client, rec_id)["data"]["recommendations"]
    assert [tuple(r) for r in saved_items()] == [
        (
            r["rank_no"],
            r["dong_code"],
            r["budget_margin"],
            Decimal(str(r["survival_p50"])),
            r["sales_quarterly_p50"],
        )
        for r in rows
    ]
    recommend(api_client, rec_id, top_k=5)  # 같은 rec_id로 다시 → 지우고 다시 넣는다
    assert [r.rank_no for r in saved_items()] == [1, 2, 3, 4, 5]


def test_execute_summary_and_meta(api_client):
    step2 = budgets(api_client, capital=0)
    body = recommend(api_client, step2["rec_id"], top_k=50)
    data = body["data"]
    assert data["summary"] == {
        "recommended_count": len(data["recommendations"]),
        "passed_district_count": step2["summary"]["passed_district_count"],
        "eligible_district_count": step2["summary"]["eligible_district_count"],
        "industry_code": "CS100001",
        "industry_name": "한식음식점",
        "category": "외식업",
    }
    assert (data["model_version"], data["base_quarter"], data["degraded"]) == ("xgb_v1", "2026Q2", False)
    assert (body["meta"]["model_version"], body["meta"]["base_quarter"]) == ("xgb_v1", "2026Q2")
    assert data["disclaimer"].startswith("본 서비스의 추정 임대비용은 자치구 평균 기준")
    rows = by_dong(body)
    assert rows[SILLIM]["summary_text"] is None  # 더미: 신림동 한식 요약 없음
    with_summary = [r for d, r in rows.items() if d not in (SILLIM, "11620902")]
    assert all(r["summary_text"].startswith("[더미 요약]") for r in with_summary)
    assert all(r["geo_code"] is None or len(r["geo_code"]) == 7 for r in rows.values())


def test_execute_no_candidates_is_empty_success(api_client, dummy_db):
    step2 = budgets(api_client)
    dummy_db.execute(
        text("DELETE FROM prediction WHERE industry_code = 'CS100001' AND model_version = 'xgb_v1'")
    )
    data = recommend(api_client, step2["rec_id"])["data"]
    assert data["recommendations"] == [] and data["summary"]["recommended_count"] == 0  # 판정 21


# ── ④ 오류 코드 ───────────────────────────────────────────────


def test_execute_rec_not_found(api_client):
    body = failure(api_client.post(URL, json={"rec_id": MISSING_REC}), 404, "REC_NOT_FOUND")
    assert body["message"] == "추천 결과를 찾을 수 없어요. 다시 추천받아 주세요."


def test_execute_rec_id_invalid(api_client):
    error = single_error(api_client.post(URL, json={"rec_id": "abc"}))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("rec_id", "INVALID_TYPE", "abc")


def test_execute_rec_id_required(api_client):
    error = single_error(api_client.post(URL, json={"top_k": 20}))
    assert (error["field"], error["reason"]) == ("rec_id", "REQUIRED")


@pytest.mark.parametrize("top_k", [0, 51])
def test_execute_top_k_out_of_range(api_client, top_k):
    step2 = budgets(api_client)
    error = single_error(api_client.post(URL, json={"rec_id": step2["rec_id"], "top_k": top_k}))
    assert (error["field"], error["reason"], error["rejected_value"]) == ("top_k", "OUT_OF_RANGE", top_k)


def test_execute_body_not_json(api_client):
    response = api_client.post(URL, content=b"{rec_id:", headers={"Content-Type": "application/json"})
    failure(response, 400, "BAD_REQUEST")


def test_execute_db_unavailable(dead_db_client):
    failure(dead_db_client.post(URL, json={"rec_id": MISSING_REC}), 503, "DB_UNAVAILABLE")


def test_execute_method_not_allowed(api_client):
    failure(api_client.get(URL), 405, "METHOD_NOT_ALLOWED")


# ── ⑤ 스냅샷 재조회 ──────────────────────────────────────────


def test_snapshot_before_execute_has_no_items(api_client):
    step2 = budgets(api_client)
    body = success(api_client.get(f"{URL}/{step2['rec_id']}"))
    data = body["data"]
    assert data["items"] == []
    assert data["district_budgets"] == step2["district_budgets"]
    assert data["summary"] == step2["summary"]
    assert data["input_condition"]["normalized_certificates"] == ["조리기능사"]
    assert (data["industry_name"], data["base_quarter"], data["degraded"]) == ("한식음식점", "2026Q2", False)
    assert datetime.fromisoformat(data["created_at"]).tzinfo is not None


def test_snapshot_items_match_rec_item(api_client):
    step2 = budgets(api_client, capital=0)
    rows = recommend(api_client, step2["rec_id"], top_k=10)["data"]["recommendations"]
    items = success(api_client.get(f"{URL}/{step2['rec_id']}"))["data"]["items"]
    assert [(i["rank_no"], i["dong_code"], i["budget_margin"]) for i in items] == [
        (r["rank_no"], r["dong_code"], r["budget_margin"]) for r in rows
    ]
    first = items[0]
    assert first["total_score"] == rows[0]["score_breakdown"]["total_score"]
    assert first["sales_monthly_p50"] == rows[0]["sales_monthly_p50"]
    assert first["sales_p50"] == rows[0]["sales_quarterly_p50"]
    assert first["data_status"] == rows[0]["data_status"]


def test_snapshot_uses_rec_item_rank_and_latest_prediction(api_client, dummy_db):
    """판정 41: 순위 · 예산 여유는 rec_item, 점수 · 예측은 응답 시점 최신 prediction."""
    step2 = budgets(api_client, capital=0)
    rows = recommend(api_client, step2["rec_id"], top_k=5)["data"]["recommendations"]
    first, second = rows[0]["dong_code"], rows[1]["dong_code"]
    dummy_db.execute(
        text("UPDATE prediction SET total_score = 1.0 WHERE dong_code = :d AND industry_code = 'CS100001'"),
        {"d": first},
    )
    dummy_db.execute(
        text("DELETE FROM prediction WHERE dong_code = :d AND industry_code = 'CS100001'"), {"d": second}
    )
    items = {i["dong_code"]: i for i in success(api_client.get(f"{URL}/{step2['rec_id']}"))["data"]["items"]}
    assert (items[first]["rank_no"], items[first]["budget_margin"]) == (1, rows[0]["budget_margin"])
    assert items[first]["total_score"] == 1.0  # 최신 prediction 값
    gone = items[second]
    assert (gone["rank_no"], gone["budget_margin"]) == (2, rows[1]["budget_margin"])
    assert gone["data_status"] == "예측 불가"
    for key in ("total_score", "survival_p50", "sales_p50", "sales_monthly_p50", "growth_rate"):
        assert gone[key] is None, key
    assert gone["sales_monthly_range"] == [None, None]


def test_snapshot_rec_not_found(api_client):
    failure(api_client.get(f"{URL}/{MISSING_REC}"), 404, "REC_NOT_FOUND")


def test_snapshot_rec_id_invalid(api_client):
    error = single_error(api_client.get(f"{URL}/not-a-uuid"))
    assert (error["field"], error["reason"]) == ("rec_id", "INVALID_TYPE")


def test_snapshot_db_unavailable(dead_db_client):
    failure(dead_db_client.get(f"{URL}/{MISSING_REC}"), 503, "DB_UNAVAILABLE")


# ── ⑥ 예산 여유 ──────────────────────────────────────────────


def margin(client, rec_id, dong_code=None):
    params = {} if dong_code is None else {"dong_code": dong_code}
    return client.get(f"{URL}/{rec_id}/margin", params=params)


def test_margin_gwanak(api_client):
    step2 = budgets(api_client, capital=23_000_000)
    data = success(margin(api_client, step2["rec_id"], SILLIM))["data"]
    assert data == {
        "rec_id": step2["rec_id"],
        "dong_code": SILLIM,
        "district_code": GWANAK,
        "district_name": "관악구",
        "available_budget": 55_000_000,
        "estimated_rent_cost": 16_929_000,
        "budget_margin": 38_071_000,
        "passed": "통과",
        "rent_confidence": "높음",
    }
    other = success(margin(api_client, step2["rec_id"], "11620903"))["data"]  # 같은 구의 신사동
    same_district = {k: v for k, v in data.items() if k != "dong_code"}
    assert {k: v for k, v in other.items() if k != "dong_code"} == same_district


def test_margin_dobong_unknown(api_client):
    step2 = budgets(api_client)
    data = success(margin(api_client, step2["rec_id"], BANGHAK3))["data"]
    assert (data["district_code"], data["passed"]) == (DOBONG, "확인불가")
    assert data["estimated_rent_cost"] is None and data["budget_margin"] is None
    assert data["rent_confidence"] == "없음"


def test_margin_rec_not_found(api_client):
    failure(margin(api_client, MISSING_REC, SILLIM), 404, "REC_NOT_FOUND")


def test_margin_dong_not_found(api_client):
    step2 = budgets(api_client)
    body = failure(margin(api_client, step2["rec_id"], "99999999"), 404, "DONG_NOT_FOUND")
    assert body["message"] == "찾을 수 없는 동네예요."


def test_margin_dong_code_required(api_client):
    step2 = budgets(api_client)
    error = single_error(margin(api_client, step2["rec_id"]))
    assert (error["field"], error["reason"]) == ("dong_code", "REQUIRED")


def test_margin_dong_code_format(api_client):
    step2 = budgets(api_client)
    error = single_error(margin(api_client, step2["rec_id"], "1162"))
    assert (error["field"], error["reason"]) == ("dong_code", "INVALID_FORMAT")
    assert error["rejected_value"] == "1162"


def test_margin_rec_id_invalid(api_client):
    error = single_error(margin(api_client, "abc", SILLIM))
    assert (error["field"], error["reason"]) == ("rec_id", "INVALID_TYPE")


def test_margin_db_unavailable(dead_db_client):
    failure(margin(dead_db_client, MISSING_REC, SILLIM), 503, "DB_UNAVAILABLE")
