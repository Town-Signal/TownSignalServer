"""추천 순위 백테스트(batch.ml.backtest) — 시점 자르기 · 실제 성과 · 순위 비교 · 전체 흐름.

배치 의존성(pandas · xgboost · scikit-learn)이 없으면 skip한다(기본 CI는 설치하지 않고 ci-batch가 돌린다).
입력은 개발용 더미 데이터(2023Q3 ~ 2025Q2 8개 분기, 행정동 52개)라 DB가 필요 없다.
"""

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("xgboost")
pytest.importorskip("sklearn")

from batch.dev.fixtures import build  # noqa: E402
from batch.ml import backtest, sales  # noqa: E402

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
def result(frames):
    return backtest.run(frames, params=FAST)


def test_truncate_drops_later_quarters(frames):
    cut = backtest.truncate(frames, "20244")
    for name in ("sales", "stores", "population"):
        assert cut[name]["year_quarter"].max() == "20244"
        assert len(cut[name]) < len(frames[name])
    assert cut["rent"] is frames["rent"]


def test_outcomes_average_window_and_naive(panel):
    out = backtest.outcomes(panel, "20244", 2)
    r = out.iloc[0]
    combo = panel[(panel.dong_code == r.dong_code) & (panel.industry_code == r.industry_code)]
    window = combo[combo.year_quarter.isin(["20251", "20252"])]
    assert r.actual_sales == pytest.approx(window.sales_per_store.mean())
    at_cutoff = combo[combo.year_quarter == "20244"].sales_per_store
    assert r.naive == pytest.approx(at_cutoff.iloc[0])
    assert out["eligible"].dtype == bool


def _ranking(n=20, industries=("A", "B")):
    rows = []
    for ind in industries:
        for i in range(n):
            rows.append({"industry_code": ind, "dong_code": f"{ind}{i:02d}", "y": float(i + 1)})
    return pd.DataFrame(rows)


def test_compare_perfect_and_reversed():
    df = _ranking()
    df["good"] = df["y"]
    df["bad"] = -df["y"]
    good = backtest.compare(df, "good", "y")
    bad = backtest.compare(df, "bad", "y")
    assert good["industries"] == 2 and good["rows"] == 40
    assert good["spearman"] == pytest.approx(1.0)
    assert good["precision_top"] == pytest.approx(1.0)
    assert good["top_lift"] > 1 > good["bottom_lift"]
    assert bad["spearman"] == pytest.approx(-1.0)
    assert bad["precision_top"] == pytest.approx(0.0)


def test_compare_lower_is_better_for_close_rate():
    df = _ranking()
    df["score"] = -df["y"]  # 점수가 높을수록 폐업률(y)이 낮음 — 맞는 순위
    out = backtest.compare(df, "score", "y", higher_is_better=False)
    assert out["precision_top"] == pytest.approx(1.0)
    assert out["spearman"] == pytest.approx(-1.0)


def test_compare_skips_small_industries():
    df = _ranking(n=5)
    df["score"] = df["y"]
    assert backtest.compare(df, "score", "y") == {"industries": 0, "rows": 0}


def test_run_reports_model_and_naive(result):
    assert result["cutoff"] == "20244"
    assert result["outcome_quarters"] == ["20251", "20252"]
    assert result["rows"]["evaluated"] > 0
    for side in ("model", "naive"):
        s = result[side]["sales"]
        assert s["industries"] > 0
        assert -1 <= s["spearman"] <= 1
        assert 0 <= s["precision_top"] <= 1
        # 더미 데이터에는 폐업 수가 없어 폐업률 비교는 비어 있다
        assert result[side]["close_rate"]["industries"] == 0
    assert "r2" in result["model_test_calibrated"]


def test_run_uses_only_data_up_to_cutoff(frames, monkeypatch):
    seen = {}
    real_train = sales.train

    def spy(panel, params=None):
        seen["last"] = panel.loc[~panel["is_future"], "year_quarter"].max()
        return real_train(panel, params)

    monkeypatch.setattr(backtest.sales, "train", spy)
    backtest.run(frames, params=FAST)
    assert seen["last"] == "20244"


@pytest.mark.parametrize(
    ("cutoff", "message"),
    [("20252", "실제 값이 없다"), ("20234", "공간 기준 경계")],
)
def test_run_rejects_bad_cutoff(frames, cutoff, message):
    with pytest.raises(ValueError, match=message):
        backtest.run(frames, cutoff=cutoff, params=FAST)
