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
    assert m["rows"]["uncovered_dropped"] == 30
    assert m["split"] == {"train": "~2022", "valid": "2023", "test": "2024~"}
    assert model.distribution in survival.DISTRIBUTIONS
    assert len(m["search"]) == len(survival.DISTRIBUTIONS)
    t = m["test"]
    assert 0 <= t["c_index"] <= 1 and 0 <= t["coverage_80_events"] <= 1
    assert set(m["baselines_test"]) == {"km_industry_c_index", "cox_c_index"}


def test_predict_next_only_covered(model, panel):
    out = survival.predict_next(model, panel)
    assert set(out["industry_code"]) <= set(COVERED)
    assert len(out) > 0
    assert (out.survival_p10 <= out.survival_p50).all() and (out.survival_p50 <= out.survival_p90).all()


def test_predict_all_fills_survival_for_covered_only(frames, model):
    sales_model = sales.train(
        frames.get("panel")
        if "panel" in frames
        else sales.build_panel(frames["sales"], frames["stores"], frames["population"], frames["rent"]),
        {**sales.DEFAULT_PARAMS, "n_estimators": 40, "early_stopping_rounds": 5, "learning_rate": 0.3},
    )
    rows = predict_all({"sales": sales_model, "survival": model}, dict(frames), "xgb_v1")
    covered = [r for r in rows if r["industry_code"] in COVERED]
    others = [r for r in rows if r["industry_code"] not in COVERED]
    assert covered and all(r["survival_p50"] is not None for r in covered)
    assert others and all(r["survival_p50"] is None for r in others)
    assert all(r["survival_p10"] <= r["survival_p50"] <= r["survival_p90"] for r in covered)
