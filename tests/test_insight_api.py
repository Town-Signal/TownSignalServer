"""⑫ predictions · ⑪ analytics · ⑬ summary · ⑱ rankings · ⑭ compare (명세 8.4 · 8.6 · 판정 22 · 30 · 31).

데이터는 data/dev 더미. 8.6 '엔드포인트별 오류 코드' 표 대응:
- ⑪ · ⑫ · ⑬ DONG_NOT_FOUND, INDUSTRY_NOT_FOUND(⑫ 경로),
  VALIDATION_ERROR(industry_code · area_sqm 쿼리), 공통 503
- ⑱ VALIDATION_ERROR(industry_code · limit), 공통 503 · 405
- ⑭ VALIDATION_ERROR(dong_codes TOO_FEW · TOO_MANY · DUPLICATE · UNKNOWN_CODE, industry_code, rec_id 형식),
  REC_NOT_FOUND, 공통 400 · 503 · 405
"""

import pytest
from sqlalchemy import text

from tests.api_helpers import failure, single_error, success

R = "/api/regions"
SILLIM, NAKSEONGDAE, SINSA_GWANAK = "11620901", "11620902", "11620903"
CHEONGUN, HOEGI, BANGHAK3, MANGWON1, WIRYE = "11110901", "11230901", "11320901", "11440903", "11710903"
MISSING_REC = "00000000-0000-4000-8000-000000000000"
QUARTERS = ["20233", "20234", "20241", "20242", "20243", "20244", "20251", "20252"]


def stored(db, dong_code: str, industry_code: str = "CS100001", model: str = "xgb_v1") -> dict | None:
    row = (
        db.execute(
            text(
                "SELECT * FROM prediction WHERE dong_code = :d AND industry_code = :i AND model_version = :m"
            ),
            {"d": dong_code, "i": industry_code, "m": model},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


# ── ⑫ predictions ───────────────────────────────────────────


def predict(client, dong_code, industry_code="CS100001"):
    return client.get(f"{R}/predictions/{dong_code}/{industry_code}")


def test_prediction_ok_uses_stored_values(api_client, dummy_db):
    body = success(predict(api_client, SILLIM))
    data, xgb, mlp = body["data"], stored(dummy_db, SILLIM), stored(dummy_db, SILLIM, model="mlp_v1")
    assert xgb["sales_p50"] != mlp["sales_p50"]  # 두 모델 값이 실제로 다르다
    assert (data["model_version"], body["meta"]["model_version"], body["meta"]["base_quarter"]) == (
        "xgb_v1",
        "xgb_v1",
        "20252",
    )
    assert (data["sales_quarterly_p10"], data["sales_quarterly_p50"], data["sales_quarterly_p90"]) == (
        xgb["sales_p10"],
        xgb["sales_p50"],
        xgb["sales_p90"],
    )
    assert (data["sales_monthly_p10"], data["sales_monthly_p50"], data["sales_monthly_p90"]) == (
        xgb["sales_p10"] // 3,
        xgb["sales_p50"] // 3,
        xgb["sales_p90"] // 3,
    )
    assert data["survival_p50"] == float(xgb["survival_p50"])
    assert data["total_score"] == data["score_breakdown"]["total_score"] == float(xgb["total_score"])
    assert data["score_breakdown"]["sales_percentile"] == float(xgb["sales_percentile"])
    assert data["data_status"] == "정상" and data["store_count_avg"] >= 5
    assert data["store_level"] in ("적음", "보통", "많음") and data["store_count_latest"] > 0
    assert data["geo_code"] == "1121069" and data["computed_at"] is not None
    assert (data["dong_name"], data["district_code"], data["district_name"]) == ("신림동", "11620", "관악구")


def test_prediction_no_row_is_200_null(api_client):
    data = success(predict(api_client, HOEGI))["data"]  # 점포는 있지만 예측 행이 없다
    assert data["data_status"] == "예측 불가"
    for key in (
        "sales_quarterly_p50",
        "sales_monthly_p50",
        "survival_p50",
        "growth_rate",
        "total_score",
        "score_breakdown",
        "computed_at",
    ):
        assert data[key] is None, key
    assert data["store_count_avg"] is not None and data["store_count_latest"] is not None
    assert (data["dong_name"], data["district_name"]) == ("회기동", "동대문구")  # 예측이 없어도 이름은 있다


def test_prediction_no_store_dong(api_client):
    data = success(predict(api_client, CHEONGUN))["data"]  # 점포 · 매출 · 예측 모두 없음
    assert data["data_status"] == "예측 불가"
    assert (data["store_count_avg"], data["store_count_latest"], data["store_level"]) == (None, None, None)


def test_prediction_sample_short(api_client):
    data = success(predict(api_client, BANGHAK3))["data"]
    assert data["data_status"] == "표본 부족" and data["store_count_avg"] < 5
    assert data["store_level"] == "적음"
    assert data["total_score"] is not None  # 표본 부족이어도 점수는 그대로


def test_prediction_growth_null(api_client, dummy_db):
    data = success(predict(api_client, MANGWON1))["data"]
    assert data["growth_rate"] is None and data["growth_confidence"] is None
    assert data["score_breakdown"]["applied_weights"] == {"sales": 0.5, "survival": 0.5, "growth": 0.0}
    assert data["total_score"] == float(stored(dummy_db, MANGWON1)["total_score"])


def test_prediction_geo_code_null_for_wirye(api_client):
    assert success(predict(api_client, WIRYE))["data"]["geo_code"] is None


def test_prediction_dong_not_found(api_client):
    body = failure(predict(api_client, "99999999"), 404, "DONG_NOT_FOUND")
    assert body["message"] == "찾을 수 없는 동네예요."


def test_prediction_industry_not_found(api_client):
    failure(predict(api_client, SILLIM, "CS999999"), 404, "INDUSTRY_NOT_FOUND")


@pytest.mark.parametrize(
    ("dong", "industry", "field"), [("1162", "CS100001", "dong_code"), (SILLIM, "CS1", "industry_code")]
)
def test_prediction_path_format(api_client, dong, industry, field):
    error = single_error(predict(api_client, dong, industry))
    assert (error["field"], error["reason"]) == (field, "INVALID_FORMAT")


def test_prediction_db_unavailable(dead_db_client):
    failure(predict(dead_db_client, SILLIM), 503, "DB_UNAVAILABLE")


# ── ⑪ analytics ─────────────────────────────────────────────


def analytics(client, dong_code, **params):
    return client.get(f"{R}/analytics/{dong_code}", params=params)


def test_analytics_ok(api_client, dummy_db):
    body = success(analytics(api_client, SILLIM, industry_code="CS100001"))
    data = body["data"]
    assert (data["dong_name"], data["district_name"], data["data_status"], data["latest_quarter"]) == (
        "신림동",
        "관악구",
        "정상",
        "20252",
    )
    assert body["meta"]["base_quarter"] == "20252"
    # 매출 추이: 최근 8분기, 2024Q1 경계
    trend = data["sales_trend"]
    assert [p["year_quarter"] for p in trend] == QUARTERS and data["space_standard_break"] == "20241"
    raw = dummy_db.execute(
        text(
            "SELECT s.amount, t.store_count FROM sales_quarterly s JOIN store_quarterly t USING "
            "(dong_code, industry_code, year_quarter) "
            "WHERE s.dong_code = :d AND s.industry_code = 'CS100001' "
            "AND s.year_quarter = '20252'"
        ),
        {"d": SILLIM},
    ).one()
    last = trend[-1]
    assert (last["amount"], last["store_count"]) == (raw.amount, raw.store_count)
    assert last["per_store_amount"] == round(raw.amount / raw.store_count)
    assert last["per_store_monthly_amount"] == last["per_store_amount"] // 3
    # 업종 분포
    shares = data["industry_distribution"]
    assert sum(i["store_count"] for i in shares) == data["total_store_count"]
    assert [i["is_selected"] for i in shares].count(True) == 1
    assert shares[-1]["industry_code"] is None and shares[-1]["name"] == "기타"
    # 임대료 · 인구 · 성장세
    assert data["rent"] == {
        "basis": "관악구 평균",
        "rent_per_sqm": 28.5,
        "area_sqm": 33.0,
        "monthly_rent": 940_500,
        "estimated_rent_cost": 16_929_000,
        "confidence": "높음",
        "base_quarter": "2026Q2",
    }
    assert [p["year_quarter"] for p in data["population_trend"]] == QUARTERS
    latest = data["population_latest"]
    assert list(latest["time_slots"]) == ["00-06", "06-11", "11-14", "14-17", "17-21", "21-24"]
    assert latest["peak_time_slot"] == max(latest["time_slots"], key=latest["time_slots"].get)
    xgb = stored(dummy_db, SILLIM)
    assert data["growth"] == {
        "growth_rate": float(xgb["growth_rate"]),
        "growth_confidence": xgb["growth_confidence"],
    }


def test_analytics_without_industry_sums_all(api_client, dummy_db):
    data = success(analytics(api_client, SILLIM))["data"]
    total = dummy_db.scalar(
        text("SELECT sum(amount) FROM sales_quarterly WHERE dong_code = :d AND year_quarter = '20252'"),
        {"d": SILLIM},
    )
    assert data["sales_trend"][-1]["amount"] == total
    assert data["growth"] is None
    assert not any(i["is_selected"] for i in data["industry_distribution"])


def test_analytics_selected_industry_outside_top5(api_client):
    shares = success(analytics(api_client, SILLIM))["data"]["industry_distribution"]
    top5 = {i["industry_code"] for i in shares if i["industry_code"]}
    outside = next(
        code
        for code in ("CS100001", "CS100010", "CS100008", "CS200001", "CS200002", "CS300001", "CS300002")
        if code not in top5
    )
    selected = success(analytics(api_client, SILLIM, industry_code=outside))["data"]["industry_distribution"]
    picked = [i for i in selected if i["is_selected"]]
    assert [i["industry_code"] for i in picked] == [outside]
    assert len([i for i in selected if i["industry_code"]]) == 6  # 상위 5 + 선택 업종


def test_analytics_no_sales_dong(api_client):
    cheongun = success(analytics(api_client, CHEONGUN, industry_code="CS100001"))["data"]
    assert cheongun["data_status"] == "조회 불가"
    assert (cheongun["industry_distribution"], cheongun["sales_trend"], cheongun["total_store_count"]) == (
        None,
        None,
        None,
    )
    assert cheongun["latest_quarter"] is None
    assert cheongun["population_latest"] is not None and cheongun["rent"]["basis"] == "종로구 평균"
    assert cheongun["growth"] == {"growth_rate": None, "growth_confidence": None}
    hoegi = success(analytics(api_client, HOEGI))["data"]  # 점포는 있지만 매출 기록이 없다
    assert hoegi["data_status"] == "조회 불가" and hoegi["sales_trend"] is None
    assert hoegi["industry_distribution"]  # 있는 항목은 그대로 보인다


def test_analytics_dobong_rent_unknown(api_client):
    rent = success(analytics(api_client, BANGHAK3))["data"]["rent"]
    assert (rent["rent_per_sqm"], rent["monthly_rent"], rent["estimated_rent_cost"], rent["confidence"]) == (
        None,
        None,
        None,
        "없음",
    )


def test_analytics_area_changes_rent_cost(api_client):
    rent = success(analytics(api_client, SILLIM, area_sqm=66))["data"]["rent"]
    assert (rent["area_sqm"], rent["estimated_rent_cost"]) == (66.0, 33_858_000)


def test_analytics_dong_not_found(api_client):
    failure(analytics(api_client, "99999999"), 404, "DONG_NOT_FOUND")


def test_analytics_dong_code_format(api_client):
    error = single_error(analytics(api_client, "1162"))
    assert (error["field"], error["reason"]) == ("dong_code", "INVALID_FORMAT")


def test_analytics_industry_code_format(api_client):
    error = single_error(analytics(api_client, SILLIM, industry_code="CS1"))
    assert (error["field"], error["reason"]) == ("industry_code", "INVALID_FORMAT")


def test_analytics_industry_code_unknown(api_client):
    error = single_error(analytics(api_client, SILLIM, industry_code="CS999999"))
    assert (error["field"], error["reason"], error["rejected_value"]) == (
        "industry_code",
        "UNKNOWN_CODE",
        "CS999999",
    )


@pytest.mark.parametrize("area", [0, 1000.1])
def test_analytics_area_out_of_range(api_client, area):
    error = single_error(analytics(api_client, SILLIM, area_sqm=area))
    assert (error["field"], error["reason"]) == ("area_sqm", "OUT_OF_RANGE")


def test_analytics_db_unavailable(dead_db_client):
    failure(analytics(dead_db_client, SILLIM), 503, "DB_UNAVAILABLE")


# ── ⑬ summary ───────────────────────────────────────────────


def summary(client, dong_code, **params):
    return client.get(f"{R}/summary/{dong_code}", params=params)


def test_summary_ok(api_client):
    data = success(summary(api_client, SINSA_GWANAK, industry_code="CS100001"))["data"]
    assert data["summary_text"].startswith("[더미 요약] 신사동 한식음식점")
    assert data["model_name"] == "dummy" and data["generated_at"] is not None


@pytest.mark.parametrize(("dong", "industry"), [(SILLIM, "CS100001"), (SINSA_GWANAK, "CS100010")])
def test_summary_missing_is_null(api_client, dong, industry):
    data = success(summary(api_client, dong, industry_code=industry))["data"]
    assert (data["summary_text"], data["model_name"], data["generated_at"]) == (None, None, None)


def test_summary_dong_not_found(api_client):
    failure(summary(api_client, "99999999", industry_code="CS100001"), 404, "DONG_NOT_FOUND")


def test_summary_industry_code_required(api_client):
    error = single_error(summary(api_client, SILLIM))
    assert (error["field"], error["reason"]) == ("industry_code", "REQUIRED")


def test_summary_industry_code_unknown(api_client):
    error = single_error(summary(api_client, SILLIM, industry_code="CS999999"))
    assert (error["field"], error["reason"]) == ("industry_code", "UNKNOWN_CODE")


def test_summary_db_unavailable(dead_db_client):
    failure(summary(dead_db_client, SILLIM, industry_code="CS100001"), 503, "DB_UNAVAILABLE")


# ── ⑱ rankings ──────────────────────────────────────────────


def rankings(client, **params):
    return client.get(f"{R}/rankings", params=params)


def test_rankings_order_and_stored_rank(api_client, dummy_db):
    body = success(rankings(api_client, industry_code="CS100001"))
    data = body["data"]
    assert (data["industry_code"], data["industry_name"], data["total"], len(data["items"])) == (
        "CS100001",
        "한식음식점",
        54,
        54,
    )
    assert (body["meta"]["base_quarter"], body["meta"]["model_version"]) == ("20252", "xgb_v1")
    expected = list(
        dummy_db.execute(
            text(
                "SELECT d.dong_code FROM dong d LEFT JOIN prediction p ON p.dong_code = d.dong_code "
                "AND p.industry_code = 'CS100001' AND p.model_version = 'xgb_v1' "
                "ORDER BY p.score_rank NULLS LAST, d.dong_code"
            )
        ).scalars()
    )
    assert [i["dong_code"] for i in data["items"]] == expected
    ranks = dict(
        dummy_db.execute(
            text(
                "SELECT dong_code, score_rank FROM prediction WHERE industry_code = 'CS100001' "
                "AND model_version = 'xgb_v1'"
            )
        ).all()
    )
    for item in data["items"]:
        assert item["rank"] == ranks.get(item["dong_code"])  # 저장된 score_rank 그대로
        p = stored(dummy_db, item["dong_code"])
        if p:
            assert item["total_score"] == float(p["total_score"])
            assert item["sales_monthly_p50"] == p["sales_p50"] // 3
    ranks_in_order = [i["rank"] for i in data["items"] if i["rank"] is not None]
    assert ranks_in_order == list(range(1, len(ranks_in_order) + 1))  # 목록 순서 = rank, 빈틈 없음


def test_rankings_tie_follows_score_rank(api_client, dummy_db):
    """total_score가 같으면 dong_code가 아니라 score_rank(7.6 동점 규칙: sales_p50↓) 순서를 따른다."""
    top = dummy_db.execute(
        text(
            "SELECT dong_code, sales_p50 FROM prediction WHERE industry_code = 'CS100001' "
            "AND model_version = 'xgb_v1' AND score_rank IN (1, 2) ORDER BY dong_code"
        )
    ).all()
    (low_code, low_sales), (high_code, high_sales) = top
    # 동점 · dong_code가 큰 쪽이 매출이 커서 1위 — dong_code 정렬이었다면 순서가 뒤집힌다
    for code, sales, rank in ((high_code, max(low_sales, high_sales) + 1, 1), (low_code, low_sales, 2)):
        dummy_db.execute(
            text(
                "UPDATE prediction SET total_score = 99.0, sales_p50 = :s, score_rank = :r "
                "WHERE dong_code = :d AND industry_code = 'CS100001' AND model_version = 'xgb_v1'"
            ),
            {"s": sales, "r": rank, "d": code},
        )
    items = success(rankings(api_client, industry_code="CS100001", limit=3))["data"]["items"]
    assert [(i["dong_code"], i["rank"], i["total_score"]) for i in items[:2]] == [
        (high_code, 1, 99.0),
        (low_code, 2, 99.0),
    ]


def test_rankings_unpredicted_last(api_client):
    items = success(rankings(api_client, industry_code="CS100001"))["data"]["items"]
    tail = items[-2:]
    assert {i["dong_code"] for i in tail} == {CHEONGUN, HOEGI}
    assert all(
        i["rank"] is None and i["total_score"] is None and i["data_status"] == "예측 불가" for i in tail
    )
    assert all(i["rank"] is not None for i in items[:-2])


def test_rankings_flags(api_client):
    items = {
        i["dong_code"]: i for i in success(rankings(api_client, industry_code="CS100001"))["data"]["items"]
    }
    assert items[BANGHAK3]["data_status"] == "표본 부족"
    assert items[WIRYE]["geo_code"] is None
    assert items[MANGWON1]["growth_rate"] is None
    assert items[CHEONGUN]["store_count_latest"] is None and items[CHEONGUN]["store_level"] is None


def test_rankings_limit(api_client):
    data = success(rankings(api_client, industry_code="CS100001", limit=10))["data"]
    assert len(data["items"]) == 10 and data["total"] == 54
    assert [i["rank"] for i in data["items"]] == list(range(1, 11))


def test_rankings_industry_code_required(api_client):
    error = single_error(rankings(api_client))
    assert (error["field"], error["reason"]) == ("industry_code", "REQUIRED")


def test_rankings_industry_code_unknown(api_client):
    error = single_error(rankings(api_client, industry_code="CS999999"))
    assert (error["field"], error["reason"]) == ("industry_code", "UNKNOWN_CODE")


@pytest.mark.parametrize("limit", [0, 427])
def test_rankings_limit_out_of_range(api_client, limit):
    error = single_error(rankings(api_client, industry_code="CS100001", limit=limit))
    assert (error["field"], error["reason"]) == ("limit", "OUT_OF_RANGE")


def test_rankings_limit_invalid_type(api_client):
    error = single_error(rankings(api_client, industry_code="CS100001", limit="x"))
    assert (error["field"], error["reason"]) == ("limit", "INVALID_TYPE")


def test_rankings_db_unavailable(dead_db_client):
    failure(rankings(dead_db_client, industry_code="CS100001"), 503, "DB_UNAVAILABLE")


def test_rankings_method_not_allowed(api_client):
    failure(api_client.post(f"{R}/rankings"), 405, "METHOD_NOT_ALLOWED")


# ── ⑭ compare ───────────────────────────────────────────────


def compare(client, **body):
    return client.post(f"{R}/compare", json={"industry_code": "CS100001", **body})


def test_compare_order_and_values(api_client, dummy_db):
    body = success(compare(api_client, dong_codes=[NAKSEONGDAE, SILLIM, MANGWON1]))
    data = body["data"]
    assert (data["industry_code"], data["industry_name"]) == ("CS100001", "한식음식점")
    items = data["comparison_items"]
    assert [i["dong_code"] for i in items] == [NAKSEONGDAE, SILLIM, MANGWON1]  # 요청 순서
    for item in items:
        p = stored(dummy_db, item["dong_code"])
        assert item["total_score"] == float(p["total_score"])
        assert item["sales_quarterly_range"] == [p["sales_p10"], p["sales_p90"]]
        assert item["sales_monthly_p50"] == p["sales_p50"] // 3
        assert item["sales_monthly_range"] == [p["sales_p10"] // 3, p["sales_p90"] // 3]
        assert item["budget_margin"] is None and item["passed"] is None  # rec_id 없음
        assert item["store_count"] > 0 and item["total_population"] > 0
    assert items[0]["estimated_rent_cost"] == items[1]["estimated_rent_cost"] == 16_929_000  # 같은 구 · 33㎡
    assert items[0]["monthly_rent"] == items[1]["monthly_rent"] == 940_500  # 16,929,000 ÷ 18
    assert items[2]["growth_rate"] is None


def test_compare_with_rec_id_margin(api_client):
    step2 = success(
        api_client.post(
            "/api/programs/calculate-budgets",
            json={"age": 27, "capital": 23_000_000, "industry_code": "CS100001", "target_area_sqm": 66},
        )
    )["data"]
    gwanak = next(d for d in step2["district_budgets"] if d["district_code"] == "11620")
    items = success(compare(api_client, dong_codes=[SILLIM, SINSA_GWANAK, BANGHAK3], rec_id=step2["rec_id"]))[
        "data"
    ]["comparison_items"]
    for item in items[:2]:  # 같은 구 → 같은 값
        assert (item["budget_margin"], item["passed"]) == (gwanak["budget_margin"], gwanak["passed"])
        assert item["estimated_rent_cost"] == gwanak["estimated_rent_cost"] == 33_858_000  # 스냅샷 면적 66㎡
    dobong = items[2]
    assert (
        dobong["passed"],
        dobong["budget_margin"],
        dobong["monthly_rent"],
        dobong["estimated_rent_cost"],
        dobong["rent_confidence"],
    ) == (
        "확인불가",
        None,
        None,
        None,
        "없음",
    )
    assert dobong["data_status"] == "표본 부족"


def test_compare_includes_unpredicted(api_client):
    item = success(compare(api_client, dong_codes=[HOEGI]))["data"]["comparison_items"][0]  # 1개도 허용
    assert item["data_status"] == "예측 불가"
    assert (item["sales_quarterly_p50"], item["sales_quarterly_range"], item["total_score"]) == (
        None,
        None,
        None,
    )
    assert item["estimated_rent_cost"] is not None  # 임대료는 자치구 값으로 보인다


def test_compare_wirye_geo_code_null(api_client):
    assert success(compare(api_client, dong_codes=[WIRYE]))["data"]["comparison_items"][0]["geo_code"] is None


def test_compare_too_few(api_client):
    error = single_error(compare(api_client, dong_codes=[]))
    assert (error["field"], error["reason"]) == ("dong_codes", "TOO_FEW")


def test_compare_too_many(api_client):
    error = single_error(compare(api_client, dong_codes=[SILLIM, NAKSEONGDAE, SINSA_GWANAK, HOEGI, BANGHAK3]))
    assert (error["field"], error["reason"], error["message"]) == (
        "dong_codes",
        "TOO_MANY",
        "비교는 4곳까지 할 수 있어요.",
    )


def test_compare_duplicate(api_client):
    error = single_error(compare(api_client, dong_codes=[SILLIM, SILLIM]))
    assert (error["field"], error["reason"]) == ("dong_codes", "DUPLICATE")


def test_compare_unknown_dong(api_client):
    error = single_error(compare(api_client, dong_codes=[SILLIM, "99999999"]))
    assert (error["field"], error["reason"], error["rejected_value"]) == (
        "dong_codes.1",
        "UNKNOWN_CODE",
        "99999999",
    )


def test_compare_dong_code_format(api_client):
    error = single_error(compare(api_client, dong_codes=[SILLIM, "1162"]))
    assert (error["field"], error["reason"]) == ("dong_codes.1", "INVALID_FORMAT")


def test_compare_industry_unknown(api_client):
    error = single_error(compare(api_client, dong_codes=[SILLIM], industry_code="CS999999"))
    assert (error["field"], error["reason"]) == ("industry_code", "UNKNOWN_CODE")


def test_compare_industry_required(api_client):
    error = single_error(api_client.post(f"{R}/compare", json={"dong_codes": [SILLIM]}))
    assert (error["field"], error["reason"]) == ("industry_code", "REQUIRED")


def test_compare_rec_id_invalid(api_client):
    error = single_error(compare(api_client, dong_codes=[SILLIM], rec_id="abc"))
    assert (error["field"], error["reason"]) == ("rec_id", "INVALID_TYPE")


def test_compare_rec_not_found(api_client):
    failure(compare(api_client, dong_codes=[SILLIM], rec_id=MISSING_REC), 404, "REC_NOT_FOUND")


def test_compare_body_not_json(api_client):
    response = api_client.post(
        f"{R}/compare", content=b"{dong_codes:", headers={"Content-Type": "application/json"}
    )
    failure(response, 400, "BAD_REQUEST")


def test_compare_db_unavailable(dead_db_client):
    failure(compare(dead_db_client, dong_codes=[SILLIM]), 503, "DB_UNAVAILABLE")


def test_compare_method_not_allowed(api_client):
    failure(api_client.get(f"{R}/compare"), 405, "METHOD_NOT_ALLOWED")
