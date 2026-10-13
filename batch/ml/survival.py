"""모델 B — 생존기간(개월) XGBoost AFT. 전체 명세 6.3 · 판정 42, 변수정의서 4장, 분석계획서 4 ~ 6장.

학습 단위는 인허가 점포 1건. 아직 영업 중인 점포(close_date 없음)는 버리지 않고 우측 절단으로 넣는다.

- 대상 업종: 업종 커버리지 표(data/seed/license_to_industry_mapping.csv, row_type=coverage)의 '커버됨'
  업종만 학습 · 예측한다(판정 42). 표를 주지 않으면 점포 수가 MIN_STORES 이상인 업종으로 대신한다.
- 관측 종료일: 인허가 원천 추출일. 원천(인허가 종류)마다 달라 업종별로 받는다(load_coverage).
- 레이블: duration_months = (close_date 또는 관측 종료일 − open_date) ÷ 30.4375, event = 폐업 여부.
- 피처
  - 상권: 모델 A와 같은 패널(batch.ml.sales.build_panel)의 개업 분기 행. 그 행의 지표는 이미 직전 분기
    (q − 1) 값이라 점포 자신이 섞이지 않는다(변수정의서 4.2). 2021년 이전 개업은 상권 지표가 없어 결측
  - 인허가 이력(HISTORY): 개업일 이전 기록만으로 만든 같은 행정동 · 업종의 경쟁 진입 · 영업 점포 수 ·
    과거 1년 내 폐업률. 상권 지표가 없는 2021년 이전 개업에도 값이 있다
- 학습 시작: 2021년 이후 개업만 쓰는 안과 TRAIN_START_EARLY 이후 개업까지 쓰는 안을
  같은 검증 로그우도로 고른다.
  오래 관측된 점포가 들어가야 5년 넘는 생존기간을 외삽 없이 배운다.
- 분할: 개업 연도 — 학습 ~ 2022, 검증 2023, 테스트 2024 이후(판정 37).
- 분포 · 폭: DISTRIBUTIONS × SCALES 중 검증 음의 로그우도(aft-nloglik)가 가장 낮은 조합.
  C-index는 순위만 보므로 폭(σ)이 바뀌어도 거의 그대로라 폭을 고르지 못한다.
  생존 확률 · 구간이 실제와 맞는지는 로그우도가 반영한다.
- 출력: 분위수 q의 생존기간 = exp(μ + σ · z_q)와 h개월 생존 확률 S(h) = 1 − F((ln h − μ) ÷ σ).
  모든 조합이 같은 σ를 써서 어느 분위수 · 확률로 줄 세워도 순서가 같다.
- 전 조합 예측: 패널의 다음 분기 행(최신 지표)과 관측 종료일 기준 인허가 이력으로 '지금 개업하는 점포'를
  가정한다(6.3).
"""

import csv
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from batch.ml import sales

QUANTILES = (0.1, 0.5, 0.9)
START_DATE = date(2021, 1, 1)  # 상권 지표가 있는 개업 시작일
TRAIN_START_EARLY = date(2016, 1, 1)  # 인허가 이력만 있는 개업까지 넣을 때의 시작일
DAYS_PER_MONTH = 30.4375
MIN_MONTHS = 0.1  # 개업 당일 폐업(기간 0)은 AFT 레이블이 0보다 커야 해서 이 값으로 둔다
MIN_STORES = 100  # 커버리지 표를 주지 않을 때(테스트 · 표 없음)의 업종별 최소 점포 수
TRAIN_LAST_YEAR = 2022
VALID_YEAR = 2023
DISTRIBUTIONS = ("normal", "logistic", "extreme")
SCALES = (0.5, 1.0, 1.5, 2.0, 3.0)
PROB_HORIZONS = (12, 36)  # 개월 — 1년 · 3년 생존 확률(전체 명세 15장 2번 보조 출력)
EVAL_HORIZONS = (12, 24)  # 테스트(2024년 이후 개업)에서 결과를 아는 기간
MAX_MONTHS = 99999.9  # survival_p* 는 NUMERIC(6,1)
SEED = 42

# 인허가 이력 피처 — 기준일(개업일 · 예측 시 관측 종료일) 이전 기록만 쓴다
HISTORY_WINDOW_DAYS = 365  # 최근 진입
COHORT_YEARS = 5  # 과거 폐업률을 볼 개업 코호트 기간
HISTORY = [
    "hist_open_1y",  # 기준일 전 1년 같은 행정동 · 업종 개업 수
    "hist_active",  # 기준일에 영업 중인 같은 행정동 · 업종 점포 수
    "hist_fail1y_rate",  # 기준일 1 ~ 5년 전 개업한 같은 행정동 · 업종 점포 중 1년 안에 폐업한 비율
    "hist_cohort_n",  # 위 비율의 분모
    "gu_fail1y_rate",  # 같은 비율을 자치구 · 업종 단위로(행정동 표본이 적을 때 보완)
]
NUMERIC = [*sales.NUMERIC, *HISTORY]
FEATURES = [*sales.CATEGORICAL, *NUMERIC]

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

ObsEnd = date | dict[str, date] | None


# ---------- 커버리지 표 ----------
def load_coverage(
    path: str | Path, extraction_dates: dict[str, date], default: date | None = None
) -> tuple[list[str], dict[str, date]]:
    """업종 커버리지 표 → ('커버됨' 업종 목록, 업종별 관측 종료일).

    관측 종료일은 그 업종에 매핑된 인허가 종류들의 추출일 중 가장 이른 날이다(그날까지는 모든 원천이 관측함).
    extraction_dates에 없는 종류는 default를 쓴다.
    """
    covered, kinds = [], {}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            code = row["industry_code"]
            if row["row_type"] == "coverage" and row["coverage"] == "커버됨":
                covered.append(code)
            elif row["row_type"] == "mapping" and row["decision"] == "mapped" and code:
                kinds.setdefault(code, set()).add(row["license_type"])
    ends = {}
    for code in covered:
        dates = [extraction_dates.get(k, default) for k in kinds.get(code, ())]
        dates = [d for d in dates if d is not None]
        if dates:
            ends[code] = min(dates)
    return sorted(covered), ends


# ---------- 레이블 ----------
def _end_series(df: pd.DataFrame, obs_end: ObsEnd) -> tuple[pd.Series, str]:
    """행마다 관측 종료일. 업종별 dict에 없는 업종 · None이면 데이터의 마지막 개업 · 폐업일(추출일 근사)."""
    last = max(df["open_date"].max(), df["close_date"].max())
    if isinstance(obs_end, dict):
        ends = df["industry_code"].map({k: pd.Timestamp(v) for k, v in obs_end.items()}).fillna(last)
        label = ", ".join(sorted({str(v) for v in obs_end.values()})) or str(last.date())
        return pd.to_datetime(ends), label
    end = pd.Timestamp(obs_end) if obs_end is not None else last
    return pd.Series(end, index=df.index), str(end.date())


def _clean(licenses: pd.DataFrame) -> pd.DataFrame:
    df = licenses.dropna(subset=["dong_code", "industry_code", "open_date"]).copy()
    df["open_date"] = pd.to_datetime(df["open_date"])
    df["close_date"] = pd.to_datetime(df["close_date"])
    for c in sales.KEYS:
        df[c] = df[c].astype(str).str.strip()
    return df


def label_frame(
    licenses: pd.DataFrame, obs_end: ObsEnd = None, start: date | None = START_DATE
) -> pd.DataFrame:
    """인허가 → 학습 행. start 이후 개업만 남긴다(None이면 전부).

    칼럼: dong_code · industry_code · open_date · obs_end · duration_months · event · open_year ·
    qidx(개업 분기).
    """
    df = _clean(licenses)
    end, label = _end_series(df, obs_end)
    df["obs_end"] = end
    keep = df["open_date"] <= df["obs_end"]
    if start is not None:
        keep &= df["open_date"] >= pd.Timestamp(start)
    df = df[keep]
    df["event"] = (df["close_date"].notna() & (df["close_date"] <= df["obs_end"])).astype(int)
    stop = df["close_date"].where(df["event"] == 1, df["obs_end"])
    df["duration_months"] = ((stop - df["open_date"]).dt.days / DAYS_PER_MONTH).clip(lower=MIN_MONTHS)
    df["open_year"] = df["open_date"].dt.year
    df["qidx"] = df["open_date"].dt.year * 4 + (df["open_date"].dt.month - 1) // 3
    columns = [*sales.KEYS, "open_date", "obs_end", "duration_months", "event", "open_year", "qidx"]
    out = df[columns].reset_index(drop=True)
    out.attrs["obs_end"] = label
    return out


def covered_industries(labels: pd.DataFrame, min_stores: int = MIN_STORES) -> list[str]:
    counts = labels.groupby("industry_code").size()
    return sorted(counts[counts >= min_stores].index)


# ---------- 인허가 이력 피처 ----------
def _days(values) -> np.ndarray:
    """날짜 → 1970-01-01 기준 일수(float). 빈 날짜(NaT)는 정수 변환 대신 NaN으로 둔다(정렬이 깨지지 않게)."""
    s = pd.to_datetime(pd.Series(values))
    days = s.to_numpy(dtype="datetime64[D]").astype("int64").astype("float64")
    days[s.isna().to_numpy()] = np.nan
    return days


def _history_by(lic: pd.DataFrame, queries: pd.DataFrame, keys: list[str]) -> np.ndarray:
    """queries의 각 행(keys · ref)에 대해 같은 keys 인허가의 [1년 개업 수, 영업 수, 1년 폐업률, 코호트 수].

    기준일 ref 이전 기록만 쓴다 — 개업은 ref 전, 폐업도 ref 전에 일어난 것만 센다.
    1년 폐업률의 코호트는 ref − 5년 ~ ref − 1년 개업이라 '1년 안 폐업' 여부가 ref 전에 이미 정해져 있다.
    """
    out = np.full((len(queries), 4), np.nan)
    opened = _days(lic["open_date"])
    closed = _days(lic["close_date"])
    fail = (closed - opened <= HISTORY_WINDOW_DAYS) & ~np.isnan(closed)
    ref = _days(queries["ref"])
    groups = lic.groupby(keys, sort=False).indices
    for key, qi in queries.groupby(keys, sort=False).indices.items():
        t = ref[qi]
        li = groups.get(key)
        if li is None:
            out[qi] = [0.0, 0.0, np.nan, 0.0]
            continue
        order = np.argsort(opened[li], kind="stable")
        o = opened[li][order]
        cum_fail = np.concatenate([[0], np.cumsum(fail[li][order])])
        c = np.sort(closed[li][~np.isnan(closed[li])])
        before = np.searchsorted(o, t, "left")
        open_1y = before - np.searchsorted(o, t - HISTORY_WINDOW_DAYS, "left")
        active = before - np.searchsorted(c, t, "left")
        lo = np.searchsorted(o, t - COHORT_YEARS * 365, "left")
        hi = np.searchsorted(o, t - HISTORY_WINDOW_DAYS, "left")
        n = hi - lo
        rate = np.where(n > 0, (cum_fail[hi] - cum_fail[lo]) / np.maximum(n, 1), np.nan)
        out[qi] = np.column_stack([open_1y, active, rate, n])
    return out


def history_features(all_licenses: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    """queries(dong_code · industry_code · ref)에 HISTORY 칼럼을 붙여 돌려준다.

    all_licenses는 기간 제한 없는 전체 인허가다.
    """
    lic = _clean(all_licenses)
    lic["district_code"] = lic["dong_code"].str[:5]
    q = queries.reset_index(drop=True).copy()
    q["district_code"] = q["dong_code"].astype(str).str[:5]
    dong = _history_by(lic, q, sales.KEYS)
    gu = _history_by(lic, q, ["district_code", "industry_code"])
    for i, name in enumerate(["hist_open_1y", "hist_active", "hist_fail1y_rate", "hist_cohort_n"]):
        q[name] = dong[:, i]
    q["gu_fail1y_rate"] = gu[:, 2]
    return q.drop(columns="district_code")


# ---------- 피처 ----------
def _key_frame(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel[[*sales.KEYS, "qidx", *sales.NUMERIC]].copy()
    for c in sales.KEYS:
        out[c] = out[c].astype(str)
    return out


def attach_features(labels: pd.DataFrame, panel: pd.DataFrame, all_licenses: pd.DataFrame) -> pd.DataFrame:
    """개업 분기 행의 패널 피처 + 개업일 기준 인허가 이력. 패널에 없는 조합 · 분기는 상권 피처가 결측."""
    feats = _key_frame(panel[~panel["is_future"]])
    df = labels.merge(feats, on=[*sales.KEYS, "qidx"], how="left")
    # 달력 변수는 패널 행이 없어도(2021년 이전 개업) 개업 분기에서 바로 나온다
    df["quarter_of_year"] = df["qidx"] % 4 + 1
    df["space_break"] = (df["qidx"] >= sales.quarter_index(sales.SPACE_BREAK_QUARTER)).astype(int)
    hist = history_features(all_licenses, df[[*sales.KEYS]].assign(ref=df["open_date"]))
    for c in HISTORY:
        df[c] = hist[c].to_numpy()
    return _with_categories(df, panel)


def _with_categories(df: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["district_code"] = df["dong_code"].str[:5]
    for c in sales.CATEGORICAL:
        col = panel[c]
        categories = col.cat.categories if hasattr(col, "cat") else sorted(col.astype(str).unique())
        df[c] = pd.Categorical(df[c].astype(str), categories=categories)
    return df


# ---------- 분포 ----------
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


def cdf(distribution: str, z: np.ndarray) -> np.ndarray:
    if distribution == "normal":
        from scipy.stats import norm

        return norm.cdf(z)
    if distribution == "logistic":
        return 1.0 / (1.0 + np.exp(-z))
    if distribution == "extreme":
        return 1.0 - np.exp(-np.exp(z))
    raise ValueError(distribution)


def quantile_months(mu: np.ndarray, distribution: str, scale: float) -> np.ndarray:
    """μ(로그 생존시간) → (n, 3) 개월 p10 · p50 · p90. 정렬 · 상한 처리 후 소수 1자리."""
    cols = [np.exp(mu + scale * z_score(distribution, q)) for q in QUANTILES]
    out = np.sort(np.column_stack(cols), axis=1)
    return np.round(np.clip(out, MIN_MONTHS, MAX_MONTHS), 1)


def survival_prob(mu: np.ndarray, distribution: str, scale: float, months: float) -> np.ndarray:
    """h개월 뒤에도 영업 중일 확률 S(h) = P(T > h) = 1 − F((ln h − μ) ÷ σ)."""
    return 1.0 - cdf(distribution, (math.log(months) - np.asarray(mu)) / scale)


# ---------- 학습 ----------
def _dmatrix(df: pd.DataFrame):
    import xgboost as xgb

    d = xgb.DMatrix(df[FEATURES], enable_categorical=True)
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


def prob_metrics(df: pd.DataFrame, mu: np.ndarray, distribution: str, scale: float, months: float) -> dict:
    """h개월 생존 확률의 Brier · AUC · 보정(평균 예측 vs 실제).

    관측 종료일까지 h개월이 지난 개업(코호트)만 쓴다. '폐업했거나 h개월 넘게 버틴 점포'로 고르면 아직 h개월이
    안 된 최근 개업 중 폐업한 점포만 들어가 생존율이 낮게 잡힌다.
    """
    t = df["duration_months"].to_numpy()
    followed = (df["obs_end"] - df["open_date"]).dt.days.to_numpy() / DAYS_PER_MONTH
    known = followed >= months
    if known.sum() == 0:
        return {"rows": 0}
    alive = (t[known] > months).astype(float)  # 폐업했어도 h개월 뒤라면 h 시점엔 영업 중
    p = survival_prob(mu[known], distribution, scale, months)
    out = {
        "rows": int(known.sum()),
        "actual_survival": float(alive.mean()),
        "mean_predicted": float(p.mean()),
        "brier": float(np.mean((p - alive) ** 2)),
        "brier_constant": float(np.mean((alive.mean() - alive) ** 2)),  # 모두에게 평균을 준 기준선
    }
    if 0 < alive.mean() < 1:
        from sklearn.metrics import roc_auc_score

        out["auc"] = float(roc_auc_score(alive, p))
    return out


def evaluate(df: pd.DataFrame, mu: np.ndarray, distribution: str, scale: float) -> dict[str, Any]:
    """분석계획서 6장 모델 B 지표."""
    months = quantile_months(mu, distribution, scale)
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
        "survival_prob": {f"{h}m": prob_metrics(df, mu, distribution, scale, h) for h in EVAL_HORIZONS},
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

    d = xgb.DMatrix(df[FEATURES], enable_categorical=True)
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
    fill = sample[NUMERIC].median()
    x = sample[NUMERIC].fillna(fill)
    # 학습 구간에서 값이 하나뿐인 피처(예: 2021 ~ 2022 개업은 space_break가 전부 0)는 Cox가 수렴하지 못해 뺀다
    cols = [c for c in NUMERIC if x[c].notna().all() and x[c].nunique() > 1]
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
    obs_end: ObsEnd = None
    metrics: dict[str, Any] = field(default_factory=dict)


def _search_one(tr, va, dist, scale, start) -> dict[str, Any]:
    booster = _fit(tr, va, dist, scale, None)
    c = harrell_c(va["duration_months"], np.exp(_mu(booster, va)), va["event"])
    return {
        "train_start": str(start),
        "distribution": dist,
        "scale": scale,
        "valid_c_index": c,
        "valid_nloglik": float(booster.best_score),
        "rounds": booster.best_iteration + 1,
        "booster": booster,
    }


def train(
    licenses: pd.DataFrame,
    panel: pd.DataFrame,
    obs_end: ObsEnd = None,
    min_stores: int = MIN_STORES,
    baselines: bool = True,
    covered: list[str] | None = None,
    starts: tuple[date, ...] = (START_DATE, TRAIN_START_EARLY),
) -> SurvivalModel:
    """레이블 → 적용 업종 → 피처 → 개업 연도 분할 → 분포 · 폭 · 학습 시작 선택 → 테스트 평가 → 전 기간 재학습.

    licenses는 기간 제한 없는 전체 인허가(인허가 이력 피처에 2021년 이전 기록도 쓴다).
    분포 · 폭은 첫 번째 학습 시작(2021년)으로 고르고, 그 조합으로 나머지 학습 시작을 비교한다.
    """
    everything = label_frame(licenses, obs_end, start=min(starts))
    if covered is None:
        covered = covered_industries(
            everything[everything["open_date"] >= pd.Timestamp(START_DATE)], min_stores
        )
    data = attach_features(everything[everything["industry_code"].isin(covered)], panel, licenses)
    va = data[data["open_year"] == VALID_YEAR]
    te = data[data["open_year"] > VALID_YEAR]

    def train_part(start: date) -> pd.DataFrame:
        return data[(data["open_year"] <= TRAIN_LAST_YEAR) & (data["open_date"] >= pd.Timestamp(start))]

    tr0 = train_part(starts[0])
    if min(len(tr0), len(va), len(te)) == 0:
        sizes = f"학습 {len(tr0)} · 검증 {len(va)} · 테스트 {len(te)}"
        raise ValueError(f"개업 연도 분할에 빈 구간이 있다({sizes})")

    search = [_search_one(tr0, va, d, s, starts[0]) for d in DISTRIBUTIONS for s in SCALES]
    first = min(search, key=lambda s: s["valid_nloglik"])
    for start in starts[1:]:
        search.append(_search_one(train_part(start), va, first["distribution"], first["scale"], start))
    best = min(search, key=lambda s: s["valid_nloglik"])
    start = date.fromisoformat(best["train_start"])
    tr = train_part(start)

    mu_test = _mu(best["booster"], te)
    metrics: dict[str, Any] = {
        "obs_end": everything.attrs["obs_end"],
        "covered_industries": covered,
        "rows": {
            "labels": int((everything["open_date"] >= pd.Timestamp(start)).sum()),
            "covered": int((data["open_date"] >= pd.Timestamp(start)).sum()),
            "train": len(tr),
            "valid": len(va),
            "test": len(te),
        },
        "split": {
            "train": f"{start.year}~{TRAIN_LAST_YEAR}",
            "valid": str(VALID_YEAR),
            "test": f"{VALID_YEAR + 1}~",
        },
        "search": [{k: v for k, v in s.items() if k != "booster"} for s in search],
        "chosen": {
            "train_start": best["train_start"],
            "distribution": best["distribution"],
            "scale": best["scale"],
            "rounds": best["rounds"],
        },
        "test": evaluate(te, mu_test, best["distribution"], best["scale"]),
        # 테스트(2024년 이후 개업)는 3년이 안 지나 3년 생존 확률은 검증 코호트(2023년 개업)로 본다.
        # 검증은 분포 · 폭 선택에 쓰였으므로 약간 낙관적인 값이다
        "valid_survival_prob_36m": prob_metrics(
            va, _mu(best["booster"], va), best["distribution"], best["scale"], 36
        ),
    }
    if baselines:
        metrics["baselines_test"] = _baselines(tr, te)

    full = data[data["open_date"] >= pd.Timestamp(start)]
    final = _fit(full, None, best["distribution"], best["scale"], best["rounds"])
    return SurvivalModel(final, best["distribution"], best["scale"], covered, obs_end, metrics)


def predict_next(model: SurvivalModel, panel: pd.DataFrame, licenses: pd.DataFrame) -> pd.DataFrame:
    """적용 업종 조합의 '지금 개업하는 점포' 생존기간 · 생존 확률.

    칼럼: dong_code · industry_code · survival_p10 · p50 · p90(개월) · survival_prob_12m · survival_prob_36m.
    인허가 이력은 업종별 관측 종료일을 기준일로 계산한다.
    """
    columns = [*sales.KEYS, "survival_p10", "survival_p50", "survival_p90"]
    columns += [f"survival_prob_{h}m" for h in PROB_HORIZONS]
    future = _key_frame(panel[panel["is_future"]])
    future = future[future["industry_code"].isin(model.covered)].reset_index(drop=True)
    if future.empty:
        return pd.DataFrame(columns=columns)
    lic = _clean(licenses)
    ref, _ = _end_series(lic, model.obs_end)
    ends = dict(zip(lic["industry_code"], ref, strict=True))
    fallback = ref.max()
    hist = history_features(
        licenses, future[sales.KEYS].assign(ref=future["industry_code"].map(ends).fillna(fallback))
    )
    for c in HISTORY:
        future[c] = hist[c].to_numpy()
    df = _with_categories(future, panel)
    mu = _mu(model.booster, df)
    months = quantile_months(mu, model.distribution, model.scale)
    out = pd.DataFrame(
        {
            "dong_code": df["dong_code"].astype(str).to_numpy(),
            "industry_code": df["industry_code"].astype(str).to_numpy(),
            "survival_p10": months[:, 0],
            "survival_p50": months[:, 1],
            "survival_p90": months[:, 2],
        }
    )
    for h in PROB_HORIZONS:
        out[f"survival_prob_{h}m"] = np.round(survival_prob(mu, model.distribution, model.scale, h), 3)
    return out


def save(model: SurvivalModel, directory) -> str:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    file = path / "survival_aft.json"
    model.booster.save_model(str(file))
    return str(file)
