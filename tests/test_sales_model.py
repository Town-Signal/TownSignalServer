"""모델 A(batch.ml.sales) — 패널 · 시차 · 분할 · 보정 · 전 조합 예측.

배치 의존성(pandas · xgboost · scikit-learn)이 없으면 skip한다(CI는 설치하지 않음).
입력은 개발용 더미 데이터(batch.dev.fixtures, 2023Q3 ~ 2025Q2 8개 분기)라 DB가 필요 없다.
"""

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("xgboost")
pytest.importorskip("sklearn")

from batch.dev.fixtures import build  # noqa: E402
from batch.jobs.train_and_predict import predict_all  # noqa: E402
from batch.ml import sales  # noqa: E402
from common.constants import MIN_STORE_COUNT  # noqa: E402

FAST = {**sales.DEFAULT_PARAMS, "n_estimators": 60, "early_stopping_rounds": 10, "learning_rate": 0.2}


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
def trained(panel):
    return sales.train(panel, FAST)


def _row(panel, dong, industry, quarter):
    r = panel[(panel.dong_code == dong) & (panel.industry_code == industry) & (panel.year_quarter == quarter)]
    assert len(r) == 1
    return r.iloc[0]


def _any_combo(panel):
    r = panel[~panel.is_future & panel.y.notna()].iloc[0]
    return str(r.dong_code), str(r.industry_code)


def test_quarter_index_roundtrip():
    assert sales.quarter_index("20244") + 1 == sales.quarter_index("20251")
    assert sales.quarter_label(sales.quarter_index("20231")) == "20231"


def test_target_is_log_per_store_sales(frames, panel):
    s = frames["sales"].iloc[0]
    st = frames["stores"]
    count = st[
        (st.dong_code == s.dong_code)
        & (st.industry_code == s.industry_code)
        & (st.year_quarter == s.year_quarter)
    ].store_count.iloc[0]
    row = _row(panel, s.dong_code, s.industry_code, s.year_quarter)
    assert row.y == pytest.approx(np.log1p(s.amount / count))


def test_lag1_is_previous_quarter_target(panel):
    dong, industry = _any_combo(panel)
    prev, cur = _row(panel, dong, industry, "20242"), _row(panel, dong, industry, "20243")
    assert cur.sales_per_store_lag1 == pytest.approx(prev.y)
    assert cur.store_count_lag1 == prev.store_count


def test_lags_crossing_2024_boundary_are_missing(panel):
    """2023 → 2024 경계를 넘는 시차 피처는 결측(시스템 구조 v0.9 5.2 ⑤)."""
    q1 = panel[panel.year_quarter == "20241"]
    assert q1[["sales_per_store_lag1", "store_count_lag1", "pop_total_lag1"]].isna().all().all()
    in_2024 = panel[panel.year_quarter.isin(["20241", "20242", "20243", "20244"])]
    assert in_2024.sales_per_store_lag4.isna().all()
    q5 = panel[(panel.year_quarter == "20251") & ~panel.is_future]
    assert q5.sales_per_store_lag4.notna().any()  # 2024Q1 → 2025Q1은 경계 이후끼리


def test_future_rows_one_per_sales_combo(frames, panel):
    future = panel[panel.is_future]
    combos = frames["sales"][["dong_code", "industry_code"]].drop_duplicates()
    assert len(future) == len(combos)
    assert set(future.year_quarter) == {"20253"}
    assert future.y.isna().all()


def test_training_rows_drop_first_quarter_and_low_sample(panel):
    rows = sales.training_rows(panel)
    assert "20233" not in set(rows.year_quarter)
    assert (rows.store_mean4 >= MIN_STORE_COUNT).all()
    assert rows.y.notna().all()


def test_split_last_two_test_two_valid(panel):
    split = sales.split_quarters(sales.training_rows(panel))
    assert split.test == ["20251", "20252"]
    assert split.valid == ["20243", "20244"]
    assert split.train == ["20234", "20241", "20242"]


def test_split_needs_five_quarters(panel):
    rows = sales.training_rows(panel)
    with pytest.raises(ValueError):
        sales.split_quarters(rows[rows.year_quarter.isin(["20243", "20244", "20251", "20252"])])


def test_conformal_margin_reaches_target_coverage():
    rng = np.random.default_rng(0)
    y = rng.normal(size=2000)
    pred = np.column_stack([np.full(2000, -0.5), np.zeros(2000), np.full(2000, 0.5)])  # 너무 좁은 구간
    margin = sales.conformal_margin(pred, y, 0.8)
    adjusted = sales.apply_margin(pred, margin)
    coverage = np.mean((y >= adjusted[:, 0]) & (y <= adjusted[:, 2]))
    assert margin > 0
    assert coverage == pytest.approx(0.8, abs=0.01)


def test_train_reports_test_metrics(trained):
    m = trained.metrics
    assert m["split"]["test"] == ["20251", "20252"]
    for key in ("r2", "mae", "coverage_80", "improvement_vs_naive"):
        assert key in m["test_calibrated"]
    assert m["rows"]["test"] > 0
    assert trained.predict_quarter == "20253"


def test_predict_all_rows(frames, trained):
    rows = predict_all({"sales": trained}, frames, "xgb_v1")
    combos = frames["sales"][["dong_code", "industry_code"]].drop_duplicates()
    assert len(rows) == len(combos)
    for r in rows:
        assert r["model_version"] == "xgb_v1"
        assert 0 <= r["sales_p10"] <= r["sales_p50"] <= r["sales_p90"]
        assert isinstance(r["sales_p50"], int)
        assert r["survival_p50"] is None and r["growth_rate"] is None  # 모델 B · 성장세 전


def test_save_writes_three_models(trained, tmp_path):
    files = sales.save(trained, tmp_path)
    assert sorted(Path(p).name for p in files) == ["sales_q10.json", "sales_q50.json", "sales_q90.json"]


def test_base_margin_falls_back_in_order():
    df = pd.DataFrame(
        {
            "sales_per_store_lag1": [10.0, np.nan, np.nan, np.nan],
            "sales_per_store_lag4": [9.0, 8.0, np.nan, np.nan],
            "y_mean4": [7.0, 7.0, 6.0, np.nan],
        }
    )
    assert sales.base_margin(df, 5.0).tolist() == [10.0, 8.0, 6.0, 5.0]


def test_group_features_use_only_past_quarters(panel):
    """업종 전체 · 행정동 전체 피처는 t − 1 이전 분기만 쓴다 — t 분기 매출을 바꿔도 값이 같다."""
    dong, industry = _any_combo(panel)
    last = panel.loc[~panel.is_future, "qidx"].max()
    changed = panel.copy()
    hit = (changed.dong_code == dong) & (changed.industry_code == industry) & (changed.qidx == last)
    changed.loc[hit, "y"] = changed.loc[hit, "y"] + 5
    changed.loc[hit, "amount"] = changed.loc[hit, "amount"] * 100
    cols = ["ind_seas", "ind_mom", "dong_mom"]
    before = sales._group_momentum(panel.copy())
    after = sales._group_momentum(changed)
    at_last = (panel.qidx == last).to_numpy()
    pd.testing.assert_frame_equal(before.loc[at_last, cols], after.loc[at_last, cols])


def test_model_uses_previous_quarter_as_starting_point(trained, panel):
    """출발점(base_margin)을 1 올리면 예측(log)도 정확히 1 오른다 — 모델은 출발점에서의 차이만 낸다."""
    future = panel[panel.is_future].head(20).copy()
    future = future[future.sales_per_store_lag1.notna()]
    base = sales.predict_quantiles(trained.models, future[sales.FEATURES], sales.base_margin(future, 0.0))
    shifted = sales.predict_quantiles(
        trained.models, future[sales.FEATURES], sales.base_margin(future, 0.0) + 1.0
    )
    assert np.allclose(shifted - base, 1.0)
