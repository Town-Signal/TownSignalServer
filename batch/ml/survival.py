"""모델 B — 생존기간(개월) XGBoost AFT. 전체 명세 6.3 · 판정 42, 변수정의서 4장, 분석계획서 4 ~ 6장.

학습 단위는 인허가 점포 1건. 아직 영업 중인 점포(close_date 없음)는 버리지 않고 우측 절단으로 넣는다.

- 대상: 2021-01-01 이후 개업 · 행정동 · 업종 매핑 성공 점포. 업종은 인허가로 개폐업이 관측되는
  업종(매핑된 업종) 중 점포 수가 MIN_STORES 이상인 업종만 학습 · 예측한다(판정 42).
  나머지 업종은 생존 값을 내지 않는다.
- 레이블: duration_months = (close_date 또는 관측 종료일 − open_date) ÷ 30.4375, event = 폐업 여부.
- 피처: 모델 A와 같은 패널(batch.ml.sales.build_panel)의 개업 분기 행.
  그 행의 지표는 이미 직전 분기(q − 1) 값이라 점포 자신이 지표에 섞이지 않는다
  (변수정의서 4.2 'q 또는 q − 1' → q − 1로 확정).
- 분할: 개업 연도 — 학습 2021 ~ 2022, 검증 2023, 테스트 2024 이후(판정 37).
- 분포 · 폭: normal · logistic · extreme × 0.5 · 1.0 · 1.5 중 검증 Harrell C-index가 가장 높은 조합.
- 출력: 분위수 q의 생존기간 = exp(μ + σ · z_q). 모든 조합이 같은 σ를 써서 어느 분위수로 줄 세워도 순서가 같다.
- 전 조합 예측: 패널의 다음 분기 행(최신 지표)으로 '지금 개업하는 점포'를 가정한다(6.3).
"""

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from batch.ml import sales

QUANTILES = (0.1, 0.5, 0.9)
START_DATE = date(2021, 1, 1)  # 상권 지표가 2021년 이후뿐이라 그 이후 개업 점포만 학습한다
DAYS_PER_MONTH = 30.4375
MIN_MONTHS = 0.1  # 개업 당일 폐업(기간 0)은 AFT 레이블이 0보다 커야 해서 이 값으로 둔다
MIN_STORES = 100  # TODO(가정): 업종별 최소 표본 수. 업종 커버리지 표가 정해지면 그 기준을 따른다
TRAIN_LAST_YEAR = 2022
VALID_YEAR = 2023
DISTRIBUTIONS = ("normal", "logistic", "extreme")
SCALES = (0.5, 1.0, 1.5)
MAX_MONTHS = 99999.9  # survival_p* 는 NUMERIC(6,1)
SEED = 42

PARAMS: dict[str, Any] = {
    "objective": "survival:aft",
    "eval_metric": "aft-nloglik",
    "tree_method": "hist",
    "learning_rate": 0.05,
    "max_depth": 6,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "seed": SEED,
}
NUM_BOOST_ROUND = 2000
EARLY_STOPPING = 50


# ---------- 레이블 ----------
def label_frame(licenses: pd.DataFrame, obs_end: date | None = None) -> pd.DataFrame:
    """인허가 → 학습 행. 관측 종료일을 주지 않으면 데이터의 마지막 개업 · 폐업일(원천 추출일 근사)을 쓴다.

    칼럼: dong_code · industry_code · open_date · duration_months · event · open_year · qidx(개업 분기).
    """
    df = licenses.dropna(subset=["dong_code", "industry_code", "open_date"]).copy()
    df["open_date"] = pd.to_datetime(df["open_date"])
    df["close_date"] = pd.to_datetime(df["close_date"])
    if obs_end is None:
        obs_end = max(df["open_date"].max(), df["close_date"].max()).date()
    end = pd.Timestamp(obs_end)
    df = df[(df["open_date"] >= pd.Timestamp(START_DATE)) & (df["open_date"] <= end)]
    df["event"] = (df["close_date"].notna() & (df["close_date"] <= end)).astype(int)
    stop = df["close_date"].where(df["event"] == 1, end)
    df["duration_months"] = ((stop - df["open_date"]).dt.days / DAYS_PER_MONTH).clip(lower=MIN_MONTHS)
    df["open_year"] = df["open_date"].dt.year
    df["qidx"] = df["open_date"].dt.year * 4 + (df["open_date"].dt.month - 1) // 3
    for c in sales.KEYS:
        df[c] = df[c].astype(str).str.strip()
    columns = [*sales.KEYS, "open_date", "duration_months", "event", "open_year", "qidx"]
    out = df[columns].reset_index(drop=True)
    out.attrs["obs_end"] = str(obs_end)
    return out


def covered_industries(labels: pd.DataFrame, min_stores: int = MIN_STORES) -> list[str]:
    counts = labels.groupby("industry_code").size()
    return sorted(counts[counts >= min_stores].index)


# ---------- 피처 ----------
def _key_frame(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel[[*sales.KEYS, "qidx", *sales.NUMERIC]].copy()
    for c in sales.KEYS:
        out[c] = out[c].astype(str)
    return out


def attach_features(labels: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """개업 분기 행의 패널 피처를 붙인다. 패널에 없는 조합 · 분기는 피처가 결측(XGBoost가 처리)."""
    feats = _key_frame(panel[~panel["is_future"]])
    df = labels.merge(feats, on=[*sales.KEYS, "qidx"], how="left")
    return _with_categories(df, panel)


def _with_categories(df: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["district_code"] = df["dong_code"].str[:5]
    for c in sales.CATEGORICAL:
        col = panel[c]
        categories = col.cat.categories if hasattr(col, "cat") else sorted(col.astype(str).unique())
        df[c] = pd.Categorical(df[c].astype(str), categories=categories)
    return df


# ---------- 분위수 ----------
def z_score(distribution: str, q: float) -> float:
    """AFT 오차 분포의 q 분위수(표준화). extreme은 xgboost의 극값(최솟값) 분포 F(z) = 1 − exp(−exp(z))."""
    if distribution == "normal":
        from scipy.stats import norm

        return float(norm.ppf(q))
    if distribution == "logistic":
        return math.log(q / (1 - q))
    if distribution == "extreme":
        return math.log(-math.log(1 - q))
    raise ValueError(distribution)


def quantile_months(mu: np.ndarray, distribution: str, scale: float) -> np.ndarray:
    """μ(로그 생존시간) → (n, 3) 개월 p10 · p50 · p90. 정렬 · 상한 처리 후 소수 1자리."""
    cols = [np.exp(mu + scale * z_score(distribution, q)) for q in QUANTILES]
    out = np.sort(np.column_stack(cols), axis=1)
    return np.round(np.clip(out, MIN_MONTHS, MAX_MONTHS), 1)


# ---------- 학습 ----------
def _dmatrix(df: pd.DataFrame):
    import xgboost as xgb

    d = xgb.DMatrix(df[sales.FEATURES], enable_categorical=True)
    lower = df["duration_months"].to_numpy(dtype=float)
    upper = np.where(df["event"].to_numpy() == 1, lower, np.inf)
    d.set_float_info("label_lower_bound", lower)
    d.set_float_info("label_upper_bound", upper)
    return d


def harrell_c(durations, predicted, events) -> float:
    """예측 생존시간이 클수록 오래 버틴다는 방향의 Harrell C-index(lifelines)."""
    from lifelines.utils import concordance_index

    return float(concordance_index(durations, predicted, events))


def within_industry_c(df: pd.DataFrame, predicted: np.ndarray) -> float:
    """업종별 C-index를 점포 수로 가중 평균. 업종 간 차이를 빼고 같은 업종 안에서 구분하는 힘만 본다."""
    scores, weights = [], []
    for _, idx in df.groupby("industry_code", observed=True).indices.items():
        g = df.iloc[idx]
        if g["event"].sum() < 2 or g["event"].sum() == len(g):
            continue
        scores.append(harrell_c(g["duration_months"], predicted[idx], g["event"]))
        weights.append(len(g))
    return float(np.average(scores, weights=weights)) if scores else float("nan")


def evaluate(df: pd.DataFrame, months: np.ndarray) -> dict[str, float]:
    """분석계획서 6장 모델 B 지표. months = (n, 3) 개월 p10 · p50 · p90."""
    t = df["duration_months"].to_numpy()
    e = df["event"].to_numpy() == 1
    lo, mid, hi = months[:, 0], months[:, 1], months[:, 2]
    return {
        "rows": int(len(df)),
        "industries": int(df["industry_code"].nunique()),
        "event_rate": float(e.mean()) if len(df) else float("nan"),
        "c_index": harrell_c(t, mid, e),
        "c_index_within_industry": within_industry_c(df, mid),
        # 폐업한 점포 중 실제 기간이 p10 ~ p90 안에 든 비율. 영업 중 점포는 실제 기간을 몰라 따로 본다
        "coverage_80_events": float(np.mean((t[e] >= lo[e]) & (t[e] <= hi[e]))) if e.any() else float("nan"),
        # 영업 중인데 이미 p90보다 오래 버틴 비율 — 구간이 짧게 잡혔다는 확실한 신호
        "censored_beyond_p90": float(np.mean(t[~e] > hi[~e])) if (~e).any() else float("nan"),
        "p50_median_months": float(np.median(mid)),
        "p90_over_60_months": float(np.mean(hi > 60)),
    }


def _fit(
    train: pd.DataFrame, valid: pd.DataFrame | None, distribution: str, scale: float, rounds: int | None
):
    import xgboost as xgb

    params = {**PARAMS, "aft_loss_distribution": distribution, "aft_loss_distribution_scale": scale}
    dtrain = _dmatrix(train)
    if valid is None:
        return xgb.train(params, dtrain, num_boost_round=rounds or 100, verbose_eval=False)
    dvalid = _dmatrix(valid)
    return xgb.train(
        params,
        dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dvalid, "valid")],
        early_stopping_rounds=EARLY_STOPPING,
        verbose_eval=False,
    )


def _mu(booster, df: pd.DataFrame) -> np.ndarray:
    import xgboost as xgb

    d = xgb.DMatrix(df[sales.FEATURES], enable_categorical=True)
    kwargs = {"iteration_range": (0, booster.best_iteration + 1)} if _has_best(booster) else {}
    return booster.predict(d, output_margin=True, **kwargs)


def _has_best(booster) -> bool:
    try:
        return booster.best_iteration is not None
    except AttributeError:
        return False


def _baselines(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, float]:
    """B0 업종별 Kaplan–Meier 중앙값 · B1 Cox(수치 피처, 결측은 학습셋 중앙값)의 테스트 C-index."""
    from lifelines import CoxPHFitter, KaplanMeierFitter

    out: dict[str, float] = {}
    medians = {}
    overall = KaplanMeierFitter().fit(train["duration_months"], train["event"]).median_survival_time_
    for ind, g in train.groupby("industry_code", observed=True):
        m = KaplanMeierFitter().fit(g["duration_months"], g["event"]).median_survival_time_
        medians[str(ind)] = m if np.isfinite(m) else overall
    pred = test["industry_code"].astype(str).map(medians).fillna(overall).to_numpy(dtype=float)
    pred = np.where(np.isfinite(pred), pred, np.nanmax(pred[np.isfinite(pred)], initial=1.0))
    out["km_industry_c_index"] = harrell_c(test["duration_months"], pred, test["event"])

    sample = train.sample(min(len(train), 50_000), random_state=SEED)
    fill = sample[sales.NUMERIC].median()
    x = sample[sales.NUMERIC].fillna(fill)
    # 학습 구간에서 값이 하나뿐인 피처(예: 2021 ~ 2022 개업은 space_break가 전부 0)는 Cox가 수렴하지 못해 뺀다
    cols = [c for c in sales.NUMERIC if x[c].notna().all() and x[c].nunique() > 1]
    x = x[cols]
    mean, std = x.mean(), x.std()
    fit_df = ((x - mean) / std).assign(duration_months=sample["duration_months"], event=sample["event"])
    try:
        cox = CoxPHFitter(penalizer=0.1).fit(fit_df, "duration_months", "event")
    except Exception as exc:  # 기준선이 실패해도 주력 모델 학습은 계속한다
        out["cox_c_index"] = float("nan")
        out["cox_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    xt = (test[cols].fillna(fill[cols]) - mean) / std
    hazard = cox.predict_partial_hazard(xt).to_numpy()
    out["cox_c_index"] = harrell_c(test["duration_months"], -hazard, test["event"])
    return out


@dataclass
class SurvivalModel:
    booster: Any
    distribution: str
    scale: float
    covered: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)


def train(
    licenses: pd.DataFrame,
    panel: pd.DataFrame,
    obs_end: date | None = None,
    min_stores: int = MIN_STORES,
    baselines: bool = True,
) -> SurvivalModel:
    """레이블 → 적용 업종 → 피처 → 개업 연도 분할 → 분포 · 폭 선택 → 테스트 평가 → 전 기간 재학습."""
    labels = label_frame(licenses, obs_end)
    covered = covered_industries(labels, min_stores)
    data = attach_features(labels[labels["industry_code"].isin(covered)], panel)
    tr = data[data["open_year"] <= TRAIN_LAST_YEAR]
    va = data[data["open_year"] == VALID_YEAR]
    te = data[data["open_year"] > VALID_YEAR]
    if min(len(tr), len(va), len(te)) == 0:
        sizes = f"학습 {len(tr)} · 검증 {len(va)} · 테스트 {len(te)}"
        raise ValueError(f"개업 연도 분할에 빈 구간이 있다({sizes})")

    search = []
    for dist in DISTRIBUTIONS:
        for scale in SCALES:
            booster = _fit(tr, va, dist, scale, None)
            mu = _mu(booster, va)
            c = harrell_c(va["duration_months"], np.exp(mu), va["event"])
            search.append(
                {
                    "distribution": dist,
                    "scale": scale,
                    "valid_c_index": c,
                    "rounds": booster.best_iteration + 1,
                    "booster": booster,
                }
            )
    best = max(search, key=lambda s: s["valid_c_index"])

    test_months = quantile_months(_mu(best["booster"], te), best["distribution"], best["scale"])
    metrics: dict[str, Any] = {
        "obs_end": labels.attrs["obs_end"],
        "covered_industries": covered,
        "rows": {
            "labels": len(labels),
            "covered": len(data),
            "train": len(tr),
            "valid": len(va),
            "test": len(te),
            "uncovered_dropped": int(len(labels) - len(data)),
        },
        "split": {"train": f"~{TRAIN_LAST_YEAR}", "valid": str(VALID_YEAR), "test": f"{VALID_YEAR + 1}~"},
        "search": [{k: v for k, v in s.items() if k != "booster"} for s in search],
        "chosen": {"distribution": best["distribution"], "scale": best["scale"], "rounds": best["rounds"]},
        "test": evaluate(te, test_months),
    }
    if baselines:
        metrics["baselines_test"] = _baselines(tr, te)

    final = _fit(data, None, best["distribution"], best["scale"], best["rounds"])
    return SurvivalModel(final, best["distribution"], best["scale"], covered, metrics)


def predict_next(model: SurvivalModel, panel: pd.DataFrame) -> pd.DataFrame:
    """적용 업종 조합의 '지금 개업하는 점포' 생존기간.

    칼럼: dong_code · industry_code · survival_p10 · p50 · p90(개월).
    """
    future = _key_frame(panel[panel["is_future"]])
    future = future[future["industry_code"].isin(model.covered)]
    if future.empty:
        return pd.DataFrame(columns=[*sales.KEYS, "survival_p10", "survival_p50", "survival_p90"])
    df = _with_categories(future, panel)
    months = quantile_months(_mu(model.booster, df), model.distribution, model.scale)
    return pd.DataFrame(
        {
            "dong_code": df["dong_code"].astype(str).to_numpy(),
            "industry_code": df["industry_code"].astype(str).to_numpy(),
            "survival_p10": months[:, 0],
            "survival_p50": months[:, 1],
            "survival_p90": months[:, 2],
        }
    )


def save(model: SurvivalModel, directory) -> str:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    file = path / "survival_aft.json"
    model.booster.save_model(str(file))
    return str(file)
