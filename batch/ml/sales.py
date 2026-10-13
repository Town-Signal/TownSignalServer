"""모델 A — 점포당 분기 매출 (XGBoost 분위수 회귀). 전체 명세 6.2 · 시스템 구조 v0.9 5.2.

DB에 의존하지 않는다. 표 네 개(DataFrame)를 받아 학습 · 평가 · 다음 분기 예측을 한다.
DB 읽기와 적재는 batch.jobs.train_and_predict가 한다.

- 타깃: 분기 t의 점포당 매출 = amount ÷ store_count, log1p 변환 (변수정의서 3.1)
- 피처: t − 1 분기까지의 값만 (변수정의서 3.2). 2024Q1 공간 기준 경계를 넘는 시차 피처는 결측
  (시스템 구조 v0.9 5.2 ⑤ 주 방식)
- 분할: 마지막 2개 분기 = 테스트, 그 앞 2개 분기 = 검증, 나머지 = 학습 (분석계획서 4장)
- 모델: 분위수 0.1 · 0.5 · 0.9별 XGBRegressor(reg:quantileerror), 검증셋 조기 종료.
  출발점(base_margin)을 직전 분기 점포당 매출(log)로 두고 '얼마나 변할지'만 학습한다(분석계획서 실험 A5).
  직전 분기 값이 없으면 전년 동기 → 최근 4분기 평균 → 학습셋 중앙값 순으로 대신한다
- 구간 보정: 검증셋으로 CQR 보정 폭을 구해 p10 · p90에 더한다 (시스템 구조 v0.9 5.2 ⑥)
- 적재: 하이퍼파라미터를 고정하고 전 기간으로 다시 학습해 다음 분기를 예측한다. 보고 성능은 재학습 전 테스트값
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from common.constants import MIN_STORE_COUNT

QUANTILES = (0.1, 0.5, 0.9)
SPACE_BREAK_QUARTER = "20241"  # 2024년부터 표준단위구역
KEYS = ["dong_code", "industry_code"]
SEED = 42

CATEGORICAL = ["district_code", "dong_code", "industry_code"]
_STORE_COLUMNS = ["store_count", "franchise_count", "open_count", "close_count"]
_AGE_COLUMNS = ["age_10", "age_20", "age_30", "age_40", "age_50", "age_60"]
_POP_FEATURES = ["pop_total", "pop_male_share", *[f"pop_{c.replace('_', '')}_share" for c in _AGE_COLUMNS]]
_STORE_FEATURES = ["store_count", "franchise_ratio", "open_count", "close_count", "close_rate"]
NUMERIC = [
    "quarter_of_year",
    "space_break",
    *[f"{c}_lag1" for c in _STORE_FEATURES],
    *[f"{c}_lag1" for c in _POP_FEATURES],
    "rent_per_sqm",
    "sales_per_store_lag1",
    "sales_per_store_lag4",
]
# 실험 A5에서 추가한 피처. 모두 t − 1 이전 값이며 2024Q1 경계를 넘는 시차는 결측
_MIX_COLUMNS = {  # 매출 구성 — sales_quarterly 칼럼이 있을 때만(없으면 결측)
    "mix_weekend": ("weekend_amount", "amount"),
    "mix_hour_11_14": ("hour_11_14", "amount"),
    "mix_hour_17_21": ("hour_17_21", "amount"),
    "mix_hour_21_24": ("hour_21_24", "amount"),
    "mix_age_20": ("age_20", "_customers"),
    "mix_age_30": ("age_30", "_customers"),
    "mix_age_50": ("age_50", "_customers"),
}
_MIX_FEATURES = [*_MIX_COLUMNS, "log_txn", "ticket"]
_TIME_COLUMNS = ["time_11_14", "time_17_21", "time_21_24"]  # population_quarterly 시간대 인구
EXTRA = [
    "y_lag2",  # 2 · 3분기 전 점포당 매출(log)
    "y_lag3",
    "y_mean4",  # 최근 4분기 평균
    "d1",  # 직전 분기 변화(lag1 − lag2)
    "seas_own",  # 작년 같은 분기의 변화(lag4 − lag5)
    "ind_seas",  # 업종 전체(서울)의 작년 같은 분기 변화
    "ind_mom",  # 업종 전체의 직전 분기 변화
    "dong_mom",  # 같은 행정동 전체 매출의 직전 분기 변화
    *[f"{c}_lag1" for c in _MIX_FEATURES],
    *[f"pop_{c}_share_lag1" for c in _TIME_COLUMNS],
]
NUMERIC = [*NUMERIC, *EXTRA]
FEATURES = [*CATEGORICAL, *NUMERIC]

# TODO(가정): 분석계획서 5장 탐색 범위의 가운데 값. 탐색은 실데이터 1차 결과를 본 뒤 한다
DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 2000,
    "learning_rate": 0.05,
    "max_depth": 6,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "early_stopping_rounds": 100,
}


# ---------- 분기 ----------
def quarter_index(year_quarter: str) -> int:
    """'20251' → 연속 정수. 분기끼리 빼면 분기 차이가 된다."""
    return int(year_quarter[:4]) * 4 + int(year_quarter[4]) - 1


def quarter_label(index: int) -> str:
    return f"{index // 4}{index % 4 + 1}"


BREAK_INDEX = quarter_index(SPACE_BREAK_QUARTER)


# ---------- 패널 ----------
def _lag(df: pd.DataFrame, by: list[str], columns: list[str], k: int, suffix: str) -> pd.DataFrame:
    """분기 qidx − k의 값을 붙인다. 행이 비어 있는 분기는 결측(shift가 아니라 분기 번호로 맞춘다).

    t와 t − k가 2024Q1 공간 기준 경계를 사이에 두면 결측으로 둔다(기준이 다른 값을 이어 붙이지 않음).
    """
    src = df[[*by, "qidx", *columns]].copy()
    src["qidx"] += k
    src = src.rename(columns={c: f"{c}{suffix}" for c in columns})
    out = df.merge(src, on=[*by, "qidx"], how="left")
    crosses = (out["qidx"] >= BREAK_INDEX) & (out["qidx"] - k < BREAK_INDEX)
    out.loc[crosses, [f"{c}{suffix}" for c in columns]] = np.nan
    return out


def build_panel(
    sales: pd.DataFrame, stores: pd.DataFrame, population: pd.DataFrame, rent: pd.DataFrame
) -> pd.DataFrame:
    """행정동 × 업종 × 분기 패널. 마지막 분기 다음 분기(예측 대상)의 행도 함께 만든다(is_future).

    입력 칼럼은 schema.sql 이름 그대로다 — sales_quarterly(amount) · store_quarterly · population_quarterly ·
    district_rent(rent_per_sqm). 매출 기록이 한 번도 없는 조합은 예측 행을 만들지 않는다(6.5).
    """
    s = sales[[*KEYS, "year_quarter", "amount"]]
    st = stores[[*KEYS, "year_quarter", *_STORE_COLUMNS]]
    df = st.merge(s, on=[*KEYS, "year_quarter"], how="outer")
    df = df.merge(_mix(sales), on=[*KEYS, "year_quarter"], how="left")
    df["qidx"] = df["year_quarter"].map(quarter_index)

    last = int(df["qidx"].max())
    future = s[KEYS].drop_duplicates().assign(qidx=last + 1)
    future["year_quarter"] = quarter_label(last + 1)
    df = pd.concat([df.assign(is_future=False), future.assign(is_future=True)], ignore_index=True)

    for c in [*_STORE_COLUMNS, "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    count = df["store_count"]
    df["sales_per_store"] = np.where(count > 0, df["amount"] / count, np.nan)
    df["y"] = np.log1p(df["sales_per_store"])
    df["franchise_ratio"] = np.where(count > 0, df["franchise_count"] / count, np.nan)
    df["close_rate"] = np.where(count > 0, df["close_count"] / count, np.nan)

    pop = population[["dong_code", "year_quarter", "total", "male", *_AGE_COLUMNS]].copy()
    for c in ["total", "male", *_AGE_COLUMNS]:
        pop[c] = pd.to_numeric(pop[c], errors="coerce").astype(float)
    total = pop["total"].where(pop["total"] > 0)
    pop["pop_total"] = pop["total"]
    pop["pop_male_share"] = pop["male"] / total
    for c in _AGE_COLUMNS:
        pop[f"pop_{c.replace('_', '')}_share"] = pop[c] / total
    time_features = [f"pop_{c}_share" for c in _TIME_COLUMNS]
    for c in _TIME_COLUMNS:
        values = pd.to_numeric(population[c], errors="coerce") if c in population else np.nan
        pop[f"pop_{c}_share"] = values / total
    pop["qidx"] = pop["year_quarter"].map(quarter_index)
    pop_features = [*_POP_FEATURES, *time_features]
    pop = _lag(pop[["dong_code", "qidx", *pop_features]], ["dong_code"], pop_features, 1, "_lag1")
    df = df.merge(
        pop[["dong_code", "qidx", *[f"{c}_lag1" for c in pop_features]]],
        on=["dong_code", "qidx"],
        how="left",
    )

    df = _lag(df, KEYS, [*_STORE_FEATURES, *_MIX_FEATURES, "y"], 1, "_lag1")
    for k in (2, 3, 4, 5):
        df = _lag(df, KEYS, ["y"], k, f"_lag{k}")
    df = df.drop(columns=_MIX_FEATURES)
    df = df.rename(columns={"y_lag1": "sales_per_store_lag1", "y_lag4": "sales_per_store_lag4"})
    df["y_mean4"] = df[["sales_per_store_lag1", "y_lag2", "y_lag3", "sales_per_store_lag4"]].mean(axis=1)
    df["d1"] = df["sales_per_store_lag1"] - df["y_lag2"]
    df["seas_own"] = df["sales_per_store_lag4"] - df["y_lag5"]
    df = _group_momentum(df)

    # 표본 판정용 최근 4개 분기 평균 점포 수(t − 1 ~ t − 4). 경계 규칙과 무관한 단순 개수라 따로 계산
    counts = df[[*KEYS, "qidx", "store_count"]]
    recent = []
    for k in range(1, 5):
        lagged = counts.assign(qidx=counts["qidx"] + k).rename(columns={"store_count": f"_c{k}"})
        recent.append(df[[*KEYS, "qidx"]].merge(lagged, on=[*KEYS, "qidx"], how="left")[f"_c{k}"])
    df["store_mean4"] = pd.concat(recent, axis=1).mean(axis=1)

    df["district_code"] = df["dong_code"].str[:5]
    rent_map = rent.set_index("district_code")["rent_per_sqm"]
    df["rent_per_sqm"] = pd.to_numeric(df["district_code"].map(rent_map), errors="coerce").astype(float)
    df["quarter_of_year"] = df["qidx"] % 4 + 1
    df["space_break"] = (df["qidx"] >= BREAK_INDEX).astype(int)
    for c in CATEGORICAL:
        df[c] = df[c].astype("category")
    return df.sort_values([*KEYS, "qidx"], ignore_index=True)


def _mix(sales: pd.DataFrame) -> pd.DataFrame:
    """분기별 매출 구성 비율 · 거래 수 · 객단가(log). 원천 칼럼이 없으면 결측."""
    out = sales[[*KEYS, "year_quarter"]].copy()

    def col(name: str) -> pd.Series:
        if name not in sales:
            return pd.Series(np.nan, index=sales.index)
        return pd.to_numeric(sales[name], errors="coerce").astype(float)

    amount = col("amount").where(lambda s: s > 0)
    customers = (col("male_count") + col("female_count")).where(lambda s: s > 0)
    base = {"amount": amount, "_customers": customers}
    for name, (num, den) in _MIX_COLUMNS.items():
        out[name] = col(num) / base[den]
    txn = col("txn_count")
    out["log_txn"] = np.log1p(txn)
    out["ticket"] = np.log1p(col("amount") / txn.where(txn > 0))
    return out


def _group_lag(df: pd.DataFrame, group: str, value: str, ks: tuple[int, ...]) -> pd.DataFrame:
    """group(업종 · 행정동) 단위로 모은 분기 값의 k분기 전 값. 경계를 넘으면 결측."""
    past = (
        df[~df["is_future"]]
        .groupby([group, "qidx"], observed=True)[value]
        .agg("mean" if value == "y" else "sum")
    )
    past = past.reset_index()
    past[group] = past[group].astype(str)
    if value != "y":
        past[value] = np.log1p(past[value])
    key = df[group].astype(str)
    out = {}
    for k in ks:
        shifted = past.assign(qidx=past["qidx"] + k).set_index([group, "qidx"])[value]
        idx = pd.MultiIndex.from_arrays([key, df["qidx"]])
        v = shifted.reindex(idx).to_numpy()
        crosses = (df["qidx"] >= BREAK_INDEX) & (df["qidx"] - k < BREAK_INDEX)
        v[crosses.to_numpy()] = np.nan
        out[k] = v
    return out


def _group_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """업종 전체(서울)의 계절 · 직전 변화와 같은 행정동 전체 매출의 직전 변화."""
    ind = _group_lag(df, "industry_code", "y", (1, 2, 4, 5))
    df["ind_seas"] = ind[4] - ind[5]
    df["ind_mom"] = ind[1] - ind[2]
    dong = _group_lag(df, "dong_code", "amount", (1, 2))
    df["dong_mom"] = dong[1] - dong[2]
    return df


def base_margin(df: pd.DataFrame, fill: float) -> np.ndarray:
    """출발점(log): 직전 분기 → 전년 동기 → 최근 4분기 평균 → fill(학습셋 중앙값)."""
    b = df["sales_per_store_lag1"].fillna(df["sales_per_store_lag4"]).fillna(df["y_mean4"])
    return b.fillna(fill).to_numpy(dtype=float)


def training_rows(panel: pd.DataFrame) -> pd.DataFrame:
    """학습 가능한 행: 타깃이 있고, 첫 분기가 아니고(시차 피처가 없음), 최근 4개 분기 평균 점포 5개 이상."""
    first = panel.loc[~panel["is_future"], "qidx"].min()
    mask = (
        ~panel["is_future"]
        & panel["y"].notna()
        & (panel["qidx"] > first)
        & (panel["store_mean4"] >= MIN_STORE_COUNT)
    )
    return panel.loc[mask]


@dataclass(frozen=True)
class Split:
    train: list[str]
    valid: list[str]
    test: list[str]


def split_quarters(rows: pd.DataFrame) -> Split:
    """마지막 2개 분기 = 테스트, 그 앞 2개 분기 = 검증, 나머지 = 학습(분석계획서 4장)."""
    quarters = sorted(rows["qidx"].unique())
    if len(quarters) < 5:
        raise ValueError(
            f"학습 분기가 {len(quarters)}개뿐이다 — 학습 · 검증 · 테스트로 나누려면 5개 이상 필요"
        )
    labels = [quarter_label(int(q)) for q in quarters]
    return Split(train=labels[:-4], valid=labels[-4:-2], test=labels[-2:])


# ---------- 모델 ----------
def _xgb_regressor(alpha: float, params: dict[str, Any]):
    from xgboost import XGBRegressor

    return XGBRegressor(
        objective="reg:quantileerror",
        quantile_alpha=alpha,
        tree_method="hist",
        enable_categorical=True,
        max_cat_to_onehot=1,
        random_state=SEED,
        n_jobs=-1,
        **params,
    )


def fit_quantile_models(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_valid: pd.DataFrame | None,
    y_valid: pd.Series | None,
    params: dict[str, Any] | None = None,
    n_estimators: dict[float, int] | None = None,
    margin_train: np.ndarray | None = None,
    margin_valid: np.ndarray | None = None,
) -> dict[float, Any]:
    """분위수별 모델 3개. 검증셋이 있으면 조기 종료, 없으면 n_estimators(분위수별 트리 수)로 고정 학습.

    margin_*는 출발점(base_margin, log). 주면 모델은 출발점에서의 차이만 학습한다.
    """
    models = {}
    for alpha in QUANTILES:
        p = dict(DEFAULT_PARAMS if params is None else params)
        if x_valid is None:
            p.pop("early_stopping_rounds", None)
            if n_estimators:
                p["n_estimators"] = n_estimators[alpha]
            model = _xgb_regressor(alpha, p)
            model.fit(x_train, y_train, base_margin=margin_train, verbose=False)
        else:
            model = _xgb_regressor(alpha, p)
            model.fit(
                x_train,
                y_train,
                base_margin=margin_train,
                eval_set=[(x_valid, y_valid)],
                base_margin_eval_set=None if margin_valid is None else [margin_valid],
                verbose=False,
            )
        models[alpha] = model
    return models


def predict_quantiles(
    models: dict[float, Any], x: pd.DataFrame, margin: np.ndarray | None = None
) -> np.ndarray:
    """(n, 3) — log 단위 p10 · p50 · p90. 분위수가 엇갈리면 행마다 정렬해 순서를 맞춘다."""
    raw = np.column_stack([models[a].predict(x, base_margin=margin) for a in QUANTILES])
    return np.sort(raw, axis=1)


def conformal_margin(pred: np.ndarray, y: np.ndarray, coverage: float = 0.8) -> float:
    """CQR 보정 폭(log 단위). p10에서 빼고 p90에 더하면 검증셋 포함률이 coverage가 된다.

    음수면 구간이 좁아진다.
    """
    scores = np.maximum(pred[:, 0] - y, y - pred[:, 2])
    n = len(scores)
    level = min(1.0, math.ceil((n + 1) * coverage) / n)
    return float(np.quantile(scores, level, method="higher"))


def apply_margin(pred: np.ndarray, margin: float) -> np.ndarray:
    out = pred.copy()
    out[:, 0] -= margin
    out[:, 2] += margin
    return np.sort(out, axis=1)


# ---------- 평가 ----------
def _r2(y: np.ndarray, p: np.ndarray) -> float:
    ss_res = float(np.sum((y - p) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def evaluate(y_log: np.ndarray, pred_log: np.ndarray, naive_log: np.ndarray) -> dict[str, float]:
    """분석계획서 6장 모델 A 지표. 금액 지표는 원 단위(expm1)로 되돌려 계산한다."""
    y = np.expm1(y_log)
    lo, mid, hi = (np.expm1(pred_log[:, i]) for i in range(3))
    has_naive = ~np.isnan(naive_log)
    naive = np.expm1(naive_log[has_naive])
    mae_model_on_naive = (
        float(np.mean(np.abs(y[has_naive] - mid[has_naive]))) if has_naive.any() else float("nan")
    )
    mae_naive = float(np.mean(np.abs(y[has_naive] - naive))) if has_naive.any() else float("nan")
    positive = y > 0
    return {
        "rows": int(len(y)),
        "r2": _r2(y, mid),
        "r2_log": _r2(y_log, pred_log[:, 1]),
        "mae": float(np.mean(np.abs(y - mid))),
        "mape": float(np.mean(np.abs(y[positive] - mid[positive]) / y[positive])),
        "coverage_80": float(np.mean((y >= lo) & (y <= hi))),
        "width_median": float(np.median(hi - lo)),
        "naive_rows": int(has_naive.sum()),
        "naive_mae": mae_naive,
        "mae_on_naive_rows": mae_model_on_naive,
        "improvement_vs_naive": 1.0 - mae_model_on_naive / mae_naive if mae_naive else float("nan"),
    }


# ---------- 학습 전체 흐름 ----------
@dataclass
class SalesModel:
    models: dict[float, Any]
    margin: float
    split: Split
    predict_quarter: str
    metrics: dict[str, Any] = field(default_factory=dict)
    best_iterations: dict[float, int] = field(default_factory=dict)
    base_fill: float = 0.0  # 출발점이 모두 없을 때 쓰는 학습셋 중앙값(log)


def train(panel: pd.DataFrame, params: dict[str, Any] | None = None) -> SalesModel:
    """시간 분할로 학습 · 검증 · 테스트 → CQR 보정 → 전 기간 재학습.

    metrics에는 재학습 전 테스트셋 성능(보정 전 · 후)과 naive 대비 개선율을 담는다.
    테스트셋은 여기서 한 번만 평가한다(분석계획서 6장).
    """
    rows = training_rows(panel)
    split = split_quarters(rows)
    part = rows["qidx"].map(quarter_label)
    tr, va, te = (rows[part.isin(s)] for s in (split.train, split.valid, split.test))

    fill = float(tr["y"].median())
    models = fit_quantile_models(
        tr[FEATURES],
        tr["y"],
        va[FEATURES],
        va["y"],
        params,
        margin_train=base_margin(tr, fill),
        margin_valid=base_margin(va, fill),
    )
    best = {a: int(m.best_iteration) + 1 for a, m in models.items()}

    margin = conformal_margin(
        predict_quantiles(models, va[FEATURES], base_margin(va, fill)), va["y"].to_numpy()
    )
    test_pred = predict_quantiles(models, te[FEATURES], base_margin(te, fill))
    y_test = te["y"].to_numpy()
    naive = te["sales_per_store_lag1"].to_numpy()

    metrics: dict[str, Any] = {
        "rows": {
            "train": len(tr),
            "valid": len(va),
            "test": len(te),
            "excluded_low_sample": int(
                (~panel["is_future"] & panel["y"].notna() & (panel["store_mean4"] < MIN_STORE_COUNT)).sum()
            ),
        },
        "split": {"train": [split.train[0], split.train[-1]], "valid": split.valid, "test": split.test},
        "best_iterations": {str(a): n for a, n in best.items()},
        "conformal_margin_log": margin,
        "test_raw": evaluate(y_test, test_pred, naive),
        "test_calibrated": evaluate(y_test, apply_margin(test_pred, margin), naive),
        "linear_baseline_test": _linear_baseline(tr, te),
        "base_margin": "sales_per_store_lag1 → lag4 → y_mean4 → 학습셋 중앙값",
    }

    full = rows
    full_fill = float(full["y"].median())
    final = fit_quantile_models(
        full[FEATURES],
        full["y"],
        None,
        None,
        params,
        n_estimators=best,
        margin_train=base_margin(full, full_fill),
    )
    future_quarter = quarter_label(int(panel.loc[panel["is_future"], "qidx"].iloc[0]))
    return SalesModel(final, margin, split, future_quarter, metrics, best, full_fill)


def _linear_baseline(tr: pd.DataFrame, te: pd.DataFrame) -> dict[str, float]:
    """A0 — 수치 피처만 쓴 선형 회귀(log 타깃, 결측은 학습셋 중앙값). 하한선 확인용."""
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), Ridge()
    )
    model.fit(tr[NUMERIC], tr["y"])
    p = model.predict(te[NUMERIC])
    y = te["y"].to_numpy()
    return {
        "r2": _r2(np.expm1(y), np.expm1(p)),
        "r2_log": _r2(y, p),
        "mae": float(np.mean(np.abs(np.expm1(y) - np.expm1(p)))),
    }


def predict_next(model: SalesModel, panel: pd.DataFrame) -> pd.DataFrame:
    """다음 분기 전 조합 예측.

    칼럼: dong_code · industry_code · sales_p10 · sales_p50 · sales_p90(원, 분기, 정수).

    매출 기록이 한 번이라도 있는 조합만 나온다(build_panel의 future 행). 표본 부족 조합도 예측한다(판정 20).
    """
    future = panel[panel["is_future"]]
    raw = predict_quantiles(model.models, future[FEATURES], base_margin(future, model.base_fill))
    pred = np.expm1(apply_margin(raw, model.margin))
    pred = np.clip(np.rint(pred), 0, None).astype("int64")
    return pd.DataFrame(
        {
            "dong_code": future["dong_code"].astype(str).to_numpy(),
            "industry_code": future["industry_code"].astype(str).to_numpy(),
            "sales_p10": pred[:, 0],
            "sales_p50": pred[:, 1],
            "sales_p90": pred[:, 2],
        }
    )


def save(model: SalesModel, directory) -> list[str]:
    """분위수별 모델을 XGBoost JSON으로 저장한다. 파일 경로 목록을 돌려준다.

    모델은 출발점(base_margin)에서의 차이만 담는다. 불러서 예측할 때는 base_margin(df, model.base_fill)을
    함께 넘겨야 한다(base_fill은 metrics.json에 남는다).
    """
    from pathlib import Path

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    written = []
    for alpha, m in model.models.items():
        file = path / f"sales_q{int(alpha * 100):02d}.json"
        # sklearn 래퍼의 save_model은 xgboost 2.1 + scikit-learn 1.6 이상에서 실패한다 → Booster로 저장
        m.get_booster().save_model(str(file))
        written.append(str(file))
    return written
