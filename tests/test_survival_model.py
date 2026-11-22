"""모델 B(batch.ml.survival) — 레이블 · 적용 업종 · 분위수 · 학습 · 전 조합 예측 · prediction 결합.

배치 의존성(pandas · xgboost · lifelines · scipy)이 없으면 skip한다
(기본 CI는 설치하지 않고 ci-batch가 돌린다).
인허가는 더미 데이터에 없어서 개발용 더미 패널의 행정동 · 업종으로 합성한다(행정동마다 위험도를 달리 줘서
모델이 배울 신호를 넣는다).
"""

from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("xgboost")
pytest.importorskip("lifelines")
pytest.importorskip("scipy")

from batch.dev.fixtures import build  # noqa: E402
from batch.jobs.train_and_predict import predict_all  # noqa: E402
from batch.ml import sales, survival  # noqa: E402

OBS_END = date(2025, 9, 30)
COVERED = ("CS100001", "CS100010")
UNCOVERED = "CS200001"  # 점포가 적어 학습 대상에서 빠지는 업종


@pytest.fixture(scope="module")
def frames():
    t = build().tables
    stores = pd.DataFrame(t["store_quarterly"])
    for c in ("franchise_count", "open_count", "close_count"):
        if c not in stores:
            stores[c] = None
    return {
        "sales": pd.DataFrame(t["sales_quarterly"]),
        "stores": stores,
        "population": pd.DataFrame(t["population_quarterly"]),
        "rent": pd.DataFrame(t["district_rent"]),
    }


@pytest.fixture(scope="module")
def panel(frames):
    return sales.build_panel(frames["sales"], frames["stores"], frames["population"], frames["rent"])


@pytest.fixture(scope="module")
def licenses(panel):
    rng = np.random.default_rng(7)
    dongs = sorted(panel["dong_code"].astype(str).unique())
    rows = []
    start = date(2021, 1, 1)
    span_days = (date(2025, 6, 30) - start).days
    for industry, n in ((COVERED[0], 900), (COVERED[1], 700), (UNCOVERED, 30)):
        for i in range(n):
            dong = dongs[i % len(dongs)]
            risk = 1.0 + (dongs.index(dong) % 4)  # 행정동마다 폐업 위험이 다르다
            open_date = start + timedelta(days=int(rng.integers(0, span_days)))
            life = timedelta(days=int(rng.exponential(900 / risk)))
            close = open_date + life
            rows.append(
                {
                    "dong_code": dong,
                    "industry_code": industry,
                    "open_date": open_date,
                    "close_date": close if close <= OBS_END else None,
                }
            )
    rows.append(
        {
            "dong_code": dongs[0],
            "industry_code": COVERED[0],
            "open_date": date(2019, 5, 1),
            "close_date": None,
        }
    )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def model(licenses, panel):
    with pytest.MonkeyPatch.context() as mp:  # 테스트 속도용 — 다른 테스트에 남지 않게 되돌린다
        mp.setattr(survival, "NUM_BOOST_ROUND", 80)
        mp.setattr(survival, "EARLY_STOPPING", 10)
        mp.setattr(survival, "SCALES", (1.0,))
        return survival.train(licenses, panel, obs_end=OBS_END, min_stores=100)


def test_label_frame_censoring_and_duration():
    raw = pd.DataFrame(
        [
            {
                "dong_code": "11110515",
                "industry_code": "CS100001",
                "open_date": date(2022, 2, 1),
                "close_date": date(2023, 2, 1),
            },
            {
                "dong_code": "11110515",
                "industry_code": "CS100001",
                "open_date": date(2024, 7, 15),
                "close_date": None,
            },
            {
                "dong_code": "11110515",
                "industry_code": "CS100001",
                "open_date": date(2023, 5, 1),
                "close_date": date(2023, 5, 1),
            },
            {
                "dong_code": "11110515",
                "industry_code": "CS100001",
                "open_date": date(2019, 5, 1),
                "close_date": None,
            },
            {
                "dong_code": None,
                "industry_code": "CS100001",
                "open_date": date(2022, 1, 1),
                "close_date": None,
            },
        ]
    )
    labels = survival.label_frame(raw, OBS_END).set_index("open_date")
    assert len(labels) == 3  # 2019년 개업 · 행정동 미매핑은 빠진다
    closed = labels.loc[pd.Timestamp("2022-02-01")]
    assert closed.event == 1 and closed.duration_months == pytest.approx(365 / 30.4375)
    still_open = labels.loc[pd.Timestamp("2024-07-15")]
    assert still_open.event == 0
    assert still_open.duration_months == pytest.approx(
        (pd.Timestamp(OBS_END) - pd.Timestamp("2024-07-15")).days / 30.4375
    )
    assert still_open.qidx == sales.quarter_index("20243")
    assert labels.loc[pd.Timestamp("2023-05-01")].duration_months == survival.MIN_MONTHS  # 당일 폐업
    assert survival.label_frame(raw, OBS_END).attrs["obs_end"] == "2025-09-30"


def test_obs_end_defaults_to_last_date_in_data(licenses):
    labels = survival.label_frame(licenses)
    last = max(pd.to_datetime(licenses.open_date).max(), pd.to_datetime(licenses.close_date).max())
    assert labels.attrs["obs_end"] == str(last.date())


def test_covered_industries_threshold(licenses):
    labels = survival.label_frame(licenses, OBS_END)
    assert survival.covered_industries(labels, 100) == sorted(COVERED)


@pytest.mark.parametrize(
    ("dist", "expected"), [("normal", 0.0), ("logistic", 0.0), ("extreme", float(np.log(np.log(2))))]
)
def test_median_z_score(dist, expected):
    assert survival.z_score(dist, 0.5) == pytest.approx(expected)


def test_quantile_months_sorted_and_rounded():
    out = survival.quantile_months(np.log(np.array([12.0, 30.0])), "normal", 1.0)
    assert (np.diff(out, axis=1) >= 0).all()
    assert out[0, 1] == pytest.approx(12.0)
    assert np.allclose(out, np.round(out, 1))


def test_harrell_c_perfect_ordering():
    t = np.array([1.0, 2.0, 3.0, 4.0])
    assert survival.harrell_c(t, t, np.ones(4)) == pytest.approx(1.0)


def test_train_reports_test_metrics(model):
    m = model.metrics
    assert model.covered == sorted(COVERED)
    assert m["split"]["valid"] == "2023" and m["split"]["test"] == "2024~"
    assert m["split"]["train"] in {"2021~2022", "2016~2022"}
    assert model.distribution in survival.DISTRIBUTIONS
    # 분포 × 폭(테스트는 폭 1개) + 이른 학습 시작 비교 1개
    assert len(m["search"]) == len(survival.DISTRIBUTIONS) + 1
    assert {s["train_start"] for s in m["search"]} == {"2021-01-01", "2016-01-01"}
    t = m["test"]
    assert 0 <= t["c_index"] <= 1 and 0 <= t["coverage_80_events"] <= 1
    assert set(m["baselines_test"]) == {"km_industry_c_index", "cox_c_index"}
    p12 = t["survival_prob"]["12m"]
    assert p12["rows"] > 0 and 0 <= p12["brier"] <= 1 and 0 <= p12["mean_predicted"] <= 1


def test_predict_next_only_covered(model, panel, licenses):
    out = survival.predict_next(model, panel, licenses)
    assert set(out["industry_code"]) <= set(COVERED)
    assert len(out) > 0
    assert (out.survival_p10 <= out.survival_p50).all() and (out.survival_p50 <= out.survival_p90).all()
    assert (out.survival_prob_36m <= out.survival_prob_12m).all()


def test_predict_all_fills_survival_for_covered_only(frames, model, licenses):
    sales_model = sales.train(
        frames.get("panel")
        if "panel" in frames
        else sales.build_panel(frames["sales"], frames["stores"], frames["population"], frames["rent"]),
        {**sales.DEFAULT_PARAMS, "n_estimators": 40, "early_stopping_rounds": 5, "learning_rate": 0.3},
    )
    rows = predict_all({"sales": sales_model, "survival": model}, {**frames, "licenses": licenses}, "xgb_v1")
    covered = [r for r in rows if r["industry_code"] in COVERED]
    others = [r for r in rows if r["industry_code"] not in COVERED]
    assert covered and all(r["survival_prob_3y"] is not None for r in covered)
    assert others and all(r["survival_prob_3y"] is None for r in others)
    assert all(0 <= r["survival_prob_3y"] <= r["survival_prob_1y"] <= 1 for r in covered)
    # 개월은 외삽이라 적재하지 않는다
    assert all(r["survival_p50"] is None for r in rows)


def _lic(dong, industry, opened, closed=None):
    return {"dong_code": dong, "industry_code": industry, "open_date": opened, "close_date": closed}


def test_label_frame_obs_end_by_industry():
    raw = pd.DataFrame(
        [
            _lic("11110515", "CS100001", date(2024, 1, 1)),
            _lic("11110515", "CS100010", date(2024, 1, 1)),
            _lic("11110515", "CS100010", date(2015, 1, 1)),
        ]
    )
    ends = {"CS100001": date(2025, 1, 1), "CS100010": date(2025, 1, 2)}
    labels = survival.label_frame(raw, ends).set_index("industry_code")
    assert labels.loc["CS100001"].duration_months == pytest.approx(366 / 30.4375)
    assert labels.loc["CS100010"].duration_months == pytest.approx(367 / 30.4375)
    assert len(survival.label_frame(raw, ends, start=None)) == 3  # 2015년 개업도 남긴다


def test_history_features_use_only_records_before_reference():
    d = "11110515"
    raw = pd.DataFrame(
        [
            # 코호트(기준일 1 ~ 5년 전 개업): 1년 안 폐업 1 · 오래 버팀 1
            _lic(d, "CS100001", date(2020, 1, 1), date(2020, 6, 1)),
            _lic(d, "CS100001", date(2021, 1, 1), date(2023, 6, 1)),
            # 최근 1년 진입 · 아직 영업 중
            _lic(d, "CS100001", date(2023, 3, 1)),
            # 기준일 이후 — 어떤 피처에도 들어가면 안 된다
            _lic(d, "CS100001", date(2024, 3, 1), date(2024, 4, 1)),
            # 다른 업종
            _lic(d, "CS100010", date(2023, 5, 1)),
        ]
    )
    q = pd.DataFrame([{"dong_code": d, "industry_code": "CS100001", "ref": pd.Timestamp("2024-01-01")}])
    out = survival.history_features(raw, q).iloc[0]
    assert out.hist_open_1y == 1
    assert out.hist_active == 1  # 2023-03 개업분만. 2021 개업은 2023-06에 폐업
    assert out.hist_cohort_n == 2 and out.hist_fail1y_rate == pytest.approx(0.5)
    assert out.gu_fail1y_rate == pytest.approx(0.5)
    empty = survival.history_features(raw, q.assign(industry_code="CS999999")).iloc[0]
    assert empty.hist_open_1y == 0 and np.isnan(empty.hist_fail1y_rate)


@pytest.mark.parametrize("dist", survival.DISTRIBUTIONS)
def test_survival_prob_matches_quantiles(dist):
    mu = np.log(np.array([24.0, 48.0]))
    months = survival.quantile_months(mu, dist, 1.0)
    # p50 개월에서의 생존 확률은 0.5
    for i in range(2):
        prob = survival.survival_prob(mu[i : i + 1], dist, 1.0, months[i, 1])[0]
        assert prob == pytest.approx(0.5, abs=0.01)
    assert (survival.survival_prob(mu, dist, 1.0, 36) <= survival.survival_prob(mu, dist, 1.0, 12)).all()


def test_load_coverage(tmp_path):
    path = tmp_path / "cov.csv"
    header = ",".join(survival_seed_columns())
    path.write_text(
        "\n".join(
            [
                header,
                "coverage,CS100001,한식음식점,커버됨,,,,,r,u",
                "coverage,CS200028,미용실,커버됨,,,,,r,u",
                "coverage,CS300011,일반의류,인허가 대상 아님,,,,,r,u",
                "mapping,CS100001,한식음식점,,일반음식점,업태구분명,한식,mapped,r,u",
                "mapping,CS100001,한식음식점,,휴게음식점,업태구분명,기타,mapped,r,u",
                "mapping,CS200028,미용실,,미용업,업태구분명,일반미용업,mapped,r,u",
                "mapping,,,,미용업,업태구분명,기타,unclassified,r,u",
            ]
        ),
        encoding="utf-8",
    )
    covered, ends = survival.load_coverage(path, {"일반음식점": date(2026, 10, 4)}, date(2026, 10, 5))
    assert covered == ["CS100001", "CS200028"]
    assert ends == {"CS100001": date(2026, 10, 4), "CS200028": date(2026, 10, 5)}


def test_seed_coverage_file_has_covered_industries():
    from batch.jobs.train_and_predict import COVERAGE_FILE, DEFAULT_EXTRACTION_DATE, EXTRACTION_DATES

    covered, ends = survival.load_coverage(COVERAGE_FILE, EXTRACTION_DATES, DEFAULT_EXTRACTION_DATE)
    assert len(covered) == 15 and set(ends) == set(covered)


def survival_seed_columns() -> list[str]:
    from batch.pipeline.license_mapping import SEED_COLUMNS

    return SEED_COLUMNS


def test_prob_metrics_uses_full_cohort_only():
    end = pd.Timestamp("2025-12-31")
    rows = [
        # 2년 넘게 관측된 코호트: 1년 안 폐업 1 · 계속 영업 1 → 12개월 생존율 0.5
        {"open_date": pd.Timestamp("2023-01-01"), "duration_months": 6.0, "event": 1},
        {"open_date": pd.Timestamp("2023-01-01"), "duration_months": 35.9, "event": 0},
        # 관측 6개월뿐 — 폐업했어도 12개월 평가에 들어가면 안 된다
        {"open_date": pd.Timestamp("2025-07-01"), "duration_months": 2.0, "event": 1},
    ]
    df = pd.DataFrame(rows).assign(obs_end=end, industry_code="CS100001")
    out = survival.prob_metrics(df, np.log(np.full(3, 20.0)), "normal", 1.0, 12)
    assert out["rows"] == 2 and out["actual_survival"] == pytest.approx(0.5)


# ---------- 점포 피처 · 행사용 한시적 신고 제외 (분석계획서 B5 · B6 · B9) ----------
STORE_ROWS = [
    # 상호, 면적, 주소, 다중이용업소
    ("카페큐브(한시적)", 12.0, "중구 1", False),
    ("○○팝업스토어", 9.0, "중구 2", False),
    ("김밥천국 중구점", 40.0, "중구 3 지하1층", True),
    ("김밥천국 종로점", None, "종로구 4", None),
    (None, 25.0, "", False),
]


def licenses_with_store(**overrides) -> "pd.DataFrame":
    """점포 칼럼이 든 인허가. 앞 두 건은 행사용 한시적 신고 · 팝업이다."""
    rows = []
    for i, (name, area, address, multi) in enumerate(STORE_ROWS):
        rows.append(
            {
                "dong_code": "11110515",
                "industry_code": "CS100001",
                "open_date": date(2022, 1, 1) + timedelta(days=30 * i),
                "close_date": None,
                "business_name": name,
                "area_sqm": area,
                "address": address,
                "multi_use": multi,
                **overrides,
            }
        )
    return pd.DataFrame(rows)


def test_drop_temporary_removes_event_stores_only():
    kept = survival.drop_temporary(licenses_with_store())
    names = [n for n in kept["business_name"]]
    assert "카페큐브(한시적)" not in names and "○○팝업스토어" not in names
    assert "김밥천국 중구점" in names and None in names  # 일반 점포 · 상호 없는 점포는 남는다


def test_drop_temporary_is_noop_without_business_name():
    raw = licenses_with_store().drop(columns="business_name")
    assert len(survival.drop_temporary(raw)) == len(raw)


def test_fill_area_draws_from_real_values_not_one_number():
    area = pd.Series([10.0, 20.0, 30.0, None, None, None, None])
    industry = pd.Series(["CS100001"] * 7)

    filled = survival.fill_area(area, industry)

    assert filled.notna().all()
    assert set(filled[3:]) <= {10.0, 20.0, 30.0}  # 실제 있는 값 중에서만 뽑는다
    assert filled[3:].nunique() > 1  # 한 값으로 몰리면 그 값이 곧 '폐업' 표시가 된다(B5a 누수)
    assert filled.tolist() == survival.fill_area(area, industry).tolist()  # 시드 고정


def test_fill_area_keeps_missing_when_industry_has_no_values():
    area = pd.Series([None, None, 50.0])
    industry = pd.Series(["CS200001", "CS200001", "CS100001"])

    filled = survival.fill_area(area, industry)

    assert filled.isna().tolist() == [True, True, False]


def test_fill_area_treats_zero_and_negative_as_missing():
    filled = survival.fill_area(pd.Series([0.0, -3.0, 20.0]), pd.Series(["CS100001"] * 3))
    assert filled.tolist() == [20.0, 20.0, 20.0]


def test_brand_prior_counts_only_earlier_openings():
    lic = licenses_with_store()
    queries = pd.DataFrame(
        {
            "brand_key": ["김밥천국", "김밥천국", ""],
            "industry_code": ["CS100001"] * 3,
            "ref": [pd.Timestamp("2022-01-01"), pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-01")],
        }
    )

    out = survival.brand_prior(lic, queries)

    assert out[0] == 0  # 첫 점포가 열리기 전
    assert out[1] == 2  # 중구점 · 종로점 둘 다 이전에 열렸다
    assert np.isnan(out[2])  # 상호를 모르면 '처음 여는 브랜드'로 치지 않는다


def test_store_features_reads_all_four_columns():
    lic = survival.drop_temporary(licenses_with_store())
    out = survival.store_features(lic.reset_index(drop=True), lic)

    assert list(out.columns) == survival.STORE
    assert out["log_area"].isna().tolist() == [False, True, False]  # 채움은 시간 분할 뒤에만 한다
    assert out["is_basement"].tolist()[:2] == [1.0, 0.0]  # 지하1층 · 지상
    assert np.isnan(out["is_basement"].tolist()[2])  # 주소가 비면 모른다
    assert out["is_multi_use"].tolist()[0] == 1.0


def test_store_features_all_missing_without_store_columns():
    lic = licenses_with_store().drop(columns=survival.STORE_COLUMNS)

    out = survival.store_features(lic, lic)

    assert out.isna().all().all()  # 칼럼이 없으면 이 피처를 쓰지 않던 모델과 같다


def test_store_columns_survive_label_frame():
    out = survival.label_frame(licenses_with_store(), obs_end=date(2025, 12, 31), start=None)
    assert all(c in out.columns for c in survival.STORE_COLUMNS)


def test_predict_next_uses_reference_store_values(model, panel, licenses):
    captured = {}
    real = survival._mu

    def spy(booster, df, features=None):
        captured["df"] = df
        captured["features"] = features
        return real(booster, df, features)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(survival, "_mu", spy)
        survival.predict_next(model, panel, licenses)

    df = captured["df"]
    assert df["log_area"].eq(np.log1p(survival.REFERENCE_AREA_SQM)).all()
    assert df["log_brand_prior"].eq(0.0).all() and df["is_basement"].eq(0.0).all()
    assert df["is_multi_use"].eq(0.0).all()
    # 좌표가 없는 인허가로 학습한 모델은 위치 피처를 쓰지 않는다
    assert captured["features"] == survival.FEATURES


def test_fill_area_never_samples_validation_or_test_values():
    train = pd.DataFrame({"area_sqm": [10.0, 20.0], "industry_code": ["A", "A"]})
    held_out = pd.DataFrame({"area_sqm": [999.0, None, None], "industry_code": ["A", "A", "B"]})
    out = survival.fill_store_area(held_out, train)
    assert out["log_area"].iloc[0] == pytest.approx(np.log1p(999))
    assert out["log_area"].iloc[1] in np.log1p([10.0, 20.0])
    assert np.isnan(out["log_area"].iloc[2])  # 새 업종에 평가 구간 값으로 풀을 만들지 않는다
    assert held_out["area_sqm"].isna().sum() == 2  # 원천/입력을 변경하지 않는다


def test_drop_temporary_matches_b9_english_popup_rule():
    lic = licenses_with_store()
    lic.loc[2, "business_name"] = "Summer POP-UP"
    assert "Summer POP-UP" not in survival.drop_temporary(lic)["business_name"].tolist()


def test_brand_prior_keeps_industries_separate_and_normalizes_names():
    lic = pd.DataFrame(
        {
            "industry_code": ["A", "B", "A"],
            "business_name": ["Ｃａｆｅ! 강남점", "Cafe 강남점", "Cafe 종로점"],
            "open_date": pd.to_datetime(["2020-01-01", "2019-01-01", "2022-01-01"]),
        }
    )
    q = pd.DataFrame({"industry_code": ["A"], "brand_key": ["Cafe"], "ref": pd.to_datetime(["2021-01-01"])})
    assert survival.brand_prior(lic, q).tolist() == [1.0]
    assert survival.brand_key(pd.Series(["Ｃａｆｅ! 강남점", "가", None])).tolist() == ["Cafe", "", ""]


def test_basement_recognizes_source_address_variants():
    lic = licenses_with_store().iloc[:3].copy()
    lic["address"] = ["주소 B1", "주소 비1층", "주소 지층"]
    assert survival.store_features(lic, lic)["is_basement"].tolist() == [1.0, 1.0, 1.0]
