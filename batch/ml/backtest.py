"""추천 순위 백테스트 — 과거 시점 T까지의 데이터만으로 순위를 만들고, T 이후 실제 성과와 비교한다.

모델 지표(R² · C-index)는 '값을 맞혔는가'만 본다. 서비스가 실제로 내놓는 것은 같은 업종 안의
행정동 순위이므로, '순위가 높았던 동네가 실제로 나았는가'를 따로 잰다(분석계획서 v0.4 6.1 · 실험 R1).

절차
1. 기준 분기 T 이후의 매출 · 점포 · 인구 행을 지운다 — 학습 · 예측이 T 이후를 보지 못한다.
2. 남은 데이터로 운영과 같은 코드(sales.build_panel → sales.train → sales.predict_next)를 돌려
   T + 1 분기를 예측하고, 운영과 같은 점수 계산(batch.pipeline.scores.fill_scores)으로 total_score를 매긴다.
3. 지운 분기 T + 1 ~ T + horizon의 실제 점포당 매출 평균 · 폐업률과 비교한다.
4. 같은 비교를 naive 순위(T 분기의 실제 점포당 매출 순 — 기존 상권분석 서비스가 보여주는
   '지금 잘 버는 동네')로도 해서 모델 순위가 그보다 나은지 본다.

비교는 업종 안에서 한다(순위가 업종 안의 순위이므로). 표본 부족 조합(T까지의 최근 4분기 평균 점포
5개 미만)은 실제 성과가 점포 몇 개에 좌우되므로 평가에서 뺀다. 순위에는 그대로 포함된다(판정 20).

한계 — rent_per_sqm은 과거 분기 값이 없어 현재 기준 분기 값을 그대로 쓴다(변수정의서 3.2).
모델 B · 성장세가 구현되면 예측 행에 함께 채워야 total_score가 운영과 같아진다.
지금은 매출 하나라 total_score 순위 = sales_p50 순위다.
"""

import math
from typing import Any

import numpy as np
import pandas as pd

from batch.ml import sales
from batch.pipeline.scores import fill_scores
from common.constants import MIN_STORE_COUNT

HORIZON = 2  # T 이후 몇 개 분기의 실제 성과를 볼지
TOP_SHARE = 0.2  # 상위 · 하위 20%
MIN_GROUP = 10  # 업종 안에서 비교할 최소 행정동 수. 이보다 적은 업종은 순위 비교가 의미 없어 뺀다
MODEL_VERSION = "backtest"

_TIME_FRAMES = ("sales", "stores", "population")


def truncate(frames: dict[str, pd.DataFrame], cutoff: str) -> dict[str, pd.DataFrame]:
    """cutoff 분기 이후의 매출 · 점포 · 인구 행을 지운 사본. district_rent는 분기 구분이 없어 그대로 둔다."""
    out = dict(frames)
    limit = sales.quarter_index(cutoff)
    for name in _TIME_FRAMES:
        df = frames[name]
        out[name] = df[df["year_quarter"].map(sales.quarter_index) <= limit].copy()
    return out


def outcomes(full_panel: pd.DataFrame, cutoff: str, horizon: int) -> pd.DataFrame:
    """조합별 실제 성과와 naive 기준값.

    칼럼: dong_code · industry_code · actual_sales(T+1 ~ T+h 점포당 매출 평균, 원) ·
    actual_close_rate(같은 기간 폐업 수 합 ÷ 점포 수 합) · naive(T 분기 점포당 매출) ·
    eligible(T+1 행의 store_mean4 ≥ 5 — T까지의 점포 수로만 계산되므로 미래 정보가 아니다).
    """
    t = sales.quarter_index(cutoff)
    observed = full_panel[~full_panel["is_future"]]
    keys = sales.KEYS

    window = observed[(observed["qidx"] > t) & (observed["qidx"] <= t + horizon)]
    grouped = window.groupby(keys, observed=True)
    stores_sum = grouped["store_count"].sum(min_count=1)
    result = pd.DataFrame(
        {
            "actual_sales": grouped["sales_per_store"].mean(),
            "actual_close_rate": grouped["close_count"].sum(min_count=1) / stores_sum.where(stores_sum > 0),
        }
    ).reset_index()

    first = observed[observed["qidx"] == t + 1][[*keys, "store_mean4"]]
    naive = observed[observed["qidx"] == t][[*keys, "sales_per_store"]].rename(
        columns={"sales_per_store": "naive"}
    )
    for frame in (result, first, naive):
        for c in keys:
            frame[c] = frame[c].astype(str)
    result = result.merge(first, on=keys, how="left").merge(naive, on=keys, how="left")
    result["eligible"] = result["store_mean4"] >= MIN_STORE_COUNT
    return result.drop(columns="store_mean4")


def _top_k(df: pd.DataFrame, column: str, k: int, largest: bool) -> set[str]:
    ordered = df.sort_values([column, "dong_code"], ascending=[not largest, True])
    return set(ordered["dong_code"].head(k))


def compare(
    df: pd.DataFrame,
    score: str,
    outcome: str,
    higher_is_better: bool = True,
    min_group: int = MIN_GROUP,
) -> dict[str, Any]:
    """업종별로 score 순위와 실제 outcome을 비교해 행정동 수 가중 평균을 낸다.

    - spearman — 순위 상관. 매출은 양수, 폐업률은 음수일수록 순위가 맞는 방향이다
    - top_lift · bottom_lift — 점수 상위 · 하위 20% 동네의 실제 outcome 평균 ÷ 업종 전체 평균.
      매출은 top_lift > 1 > bottom_lift면 맞는 방향(폐업률은 반대)
    - precision_top — 점수 상위 20% 중 실제로도 상위 20%(폐업률은 하위 20%)인 비율. 무작위면 약 0.2
    비교할 업종이 하나도 없으면 {"industries": 0, "rows": 0}만 돌려준다.
    """
    data = df.dropna(subset=[score, outcome])
    per_industry = []
    for industry, g in data.groupby("industry_code"):
        n = len(g)
        if n < min_group or g[outcome].nunique() < 2 or g[score].nunique() < 2:
            continue
        k = max(1, math.floor(n * TOP_SHARE))
        mean = float(g[outcome].mean())
        top = _top_k(g, score, k, largest=True)
        bottom = _top_k(g, score, k, largest=False)
        best = _top_k(g, outcome, k, largest=higher_is_better)
        per_industry.append(
            {
                "industry_code": str(industry),
                "n": n,
                "spearman": float(g[score].corr(g[outcome], method="spearman")),
                "top_lift": float(g.loc[g["dong_code"].isin(top), outcome].mean() / mean) if mean else np.nan,
                "bottom_lift": (
                    float(g.loc[g["dong_code"].isin(bottom), outcome].mean() / mean) if mean else np.nan
                ),
                "precision_top": len(top & best) / k,
            }
        )

    if not per_industry:
        return {"industries": 0, "rows": 0}
    table = pd.DataFrame(per_industry)
    weights = table["n"]

    def weighted(column: str) -> float:
        valid = table[column].notna()
        if not valid.any():
            return float("nan")
        return float(np.average(table.loc[valid, column], weights=weights[valid]))

    return {
        "industries": len(table),
        "rows": int(weights.sum()),
        "spearman": weighted("spearman"),
        "top_lift": weighted("top_lift"),
        "bottom_lift": weighted("bottom_lift"),
        "precision_top": weighted("precision_top"),
        "random_precision": TOP_SHARE,
    }


def run(
    frames: dict[str, pd.DataFrame],
    cutoff: str | None = None,
    horizon: int = HORIZON,
    params: dict[str, Any] | None = None,
    min_group: int = MIN_GROUP,
) -> dict[str, Any]:
    """백테스트 한 번.

    cutoff를 안 주면 '데이터 마지막 분기 − horizon'(실제 성과가 horizon개 분기 다 있는 가장 최근 시점)이다.
    T부터 T + horizon 사이에 2024Q1 공간 기준 경계가 끼면 기준이 다른 값을 비교하게 되므로 거부한다.
    출력: 기준 분기 · 평가 분기 · 행 수, 그 시점 모델의 테스트 지표, model · naive 각각의 매출 · 폐업률 비교.
    """
    full = sales.build_panel(frames["sales"], frames["stores"], frames["population"], frames["rent"])
    last = int(full.loc[~full["is_future"], "qidx"].max())
    t = last - horizon if cutoff is None else sales.quarter_index(cutoff)
    if t + horizon > last:
        raise ValueError(
            f"기준 분기 {sales.quarter_label(t)} 이후 {horizon}개 분기의 실제 값이 없다 "
            f"(데이터 마지막 분기 {sales.quarter_label(last)})"
        )
    if t < sales.BREAK_INDEX <= t + horizon:
        raise ValueError(
            f"기준 분기 {sales.quarter_label(t)} ~ {sales.quarter_label(t + horizon)}이 "
            f"{sales.SPACE_BREAK_QUARTER} 공간 기준 경계를 넘는다"
        )
    cutoff = sales.quarter_label(t)

    past = truncate(frames, cutoff)
    panel = sales.build_panel(past["sales"], past["stores"], past["population"], past["rent"])
    model = sales.train(panel, params)
    predicted = sales.predict_next(model, panel)

    rows = [
        {
            "dong_code": r.dong_code,
            "industry_code": r.industry_code,
            "model_version": MODEL_VERSION,
            "sales_p50": int(r.sales_p50),
            "survival_p50": None,
            "growth_rate": None,
        }
        for r in predicted.itertuples(index=False)
    ]
    scored = pd.DataFrame(fill_scores(rows))
    scored["total_score"] = pd.to_numeric(scored["total_score"], errors="coerce").astype(float)

    actual = outcomes(full, cutoff, horizon)
    df = scored[[*sales.KEYS, "total_score"]].merge(actual, on=sales.KEYS, how="inner")
    evaluated = df[df["eligible"]]

    def both(score: str) -> dict[str, Any]:
        return {
            "sales": compare(evaluated, score, "actual_sales", min_group=min_group),
            "close_rate": compare(
                evaluated, score, "actual_close_rate", higher_is_better=False, min_group=min_group
            ),
        }

    return {
        "cutoff": cutoff,
        "horizon": horizon,
        "outcome_quarters": [sales.quarter_label(q) for q in range(t + 1, t + horizon + 1)],
        "rows": {"predicted": len(scored), "with_outcome": len(df), "evaluated": len(evaluated)},
        "model_test_calibrated": model.metrics["test_calibrated"],
        "model": both("total_score"),
        "naive": both("naive"),
    }
