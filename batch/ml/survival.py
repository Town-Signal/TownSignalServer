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
  - 점포(STORE): business_license의 점포 칼럼에서 만든 면적 · 프랜차이즈 · 지하 · 다중이용업소.
    피처가 전부 행정동 × 업종 단위면 같은 칸의 점포끼리 구분하지 못한다(분석계획서 8장 ③).
    칼럼이 없거나 비어 있으면 전부 결측이라 이 피처를 쓰지 않던 모델과 같게 돈다
- 제외: 행사용 한시적 신고 · 팝업은 학습 · 평가 · 인허가 이력에서 모두 뺀다(분석계획서 B9).
  상호로만 판정해 개업 시점에 알 수 있는 정보만 쓴다. 1년 안에 99% 닫혀 C를 부풀리는데
  서비스가 답하려는 '지금 일반 점포를 연다면'과는 다른 모집단이다
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
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from batch.ml import sales, spatial
from common.constants import DEFAULT_AREA_SQM

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
# 점포 단위 피처 — business_license의 점포 칼럼(2026-11-16 마이그레이션)에서 만든다
STORE_COLUMNS = ["business_name", "area_sqm", "address", "multi_use"]
STORE = [
    "log_area",  # log1p(점포 면적 ㎡). 빈 값은 같은 업종의 실제 면적 중 무작위로 채운다
    "log_brand_prior",  # log1p(개업일 이전에 문 연 같은 상호 점포 수) — 프랜차이즈 대용
    "is_basement",  # 주소가 지하
    "is_multi_use",  # 다중이용업소
]
REFERENCE_AREA_SQM = DEFAULT_AREA_SQM  # 전 조합 예측('지금 개업하는 점포')의 면적 — 화면 기본 희망 면적
# 행사용 한시적 신고 · 팝업으로 보는 상호 낱말. '행사' · '부스'는 '은행사거리'처럼 오탐이 많아 뺐다
TEMPORARY_KEYWORDS = ("한시", "팝업", "축제", "페스티벌", "박람회", "주민자치", "먹거리장터")
NUMERIC = [*sales.NUMERIC, *HISTORY, *STORE]
FEATURES = [*sales.CATEGORICAL, *NUMERIC]
# 위치 피처(batch.ml.spatial)는 business_license에 좌표가 있을 때만 붙인다. 좌표가 없으면 피처 목록에
# 넣지 않아 좌표 이전 모델과 칼럼 구성이 같다 — colsample_bytree가 칼럼을 뽑으므로 전부 결측인 칼럼을
# 끼워 두기만 해도 결과가 달라진다
SPATIAL = spatial.SPATIAL

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


def feature_names(df: pd.DataFrame) -> list[str]:
    """이 데이터프레임으로 학습·예측할 피처 목록. 위치 피처는 칼럼이 있을 때만 들어간다."""
    return [*FEATURES, *[c for c in SPATIAL if c in df.columns]]


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
    columns += [c for c in STORE_COLUMNS if c in df]  # 있을 때만 — 점포 피처는 없으면 결측으로 간다
    columns += [c for c in spatial.COORD_COLUMNS if c in df]  # 좌표도 같다(위치 피처)
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


# ---------- 행사용 한시적 신고 · 팝업 제외 ----------
def drop_temporary(licenses: pd.DataFrame) -> pd.DataFrame:
    """상호에 행사 · 팝업 낱말이 든 인허가를 뺀다(분석계획서 B9). business_name이 없으면 그대로 둔다.

    개업 시점에 알 수 있는 정보(상호)로만 판정한다 — 영업 기간으로 거르면 결과를 보고 표본을 고르는 셈이다.
    """
    if "business_name" not in licenses:
        return licenses
    name = licenses["business_name"].astype("string").fillna("")
    hit = name.str.contains("|".join(TEMPORARY_KEYWORDS) + r"|pop[\s-]?up", case=False, regex=True, na=False)
    return licenses[~hit.to_numpy()]


# ---------- 점포 피처 ----------
def _groups(keys: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """같은 값끼리 묶은 (고유값, 시작, 끝, 정렬 순서). 순서가 정해져 있어 같은 입력이면 결과가 같다."""
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    uniq = np.unique(sorted_keys)
    lo = np.searchsorted(sorted_keys, uniq, "left")
    hi = np.searchsorted(sorted_keys, uniq, "right")
    return uniq, lo, hi, order


def fill_area(
    area: pd.Series,
    industry: pd.Series,
    seed: int = SEED,
    *,
    reference_area: pd.Series | None = None,
    reference_industry: pd.Series | None = None,
) -> pd.Series:
    """0 이하 · 빈 면적을 같은 업종의 실제 면적 중 무작위로 채운다(시드 고정).

    한 값(업종 중앙값 등)으로 채우면 누수다 — 면적이 빈 비율이 영업 중 0.4% · 폐업 17.4%라
    그 한 값이 곧 '폐업' 표시가 된다(분석계획서 B4 · 기각한 B5a). 업종에 실제 값이 하나도
    없으면 결측으로 둔다. 학습/평가에서는 reference_*에 학습 구간만 전달한다.
    독립적인 변환 호출은 reference_* 생략 시 입력 자체를 표본 풀로 쓴다.
    """
    values = pd.to_numeric(area, errors="coerce").to_numpy(dtype="float64")
    values = np.where(np.isfinite(values) & (values > 0), values, np.nan)
    out = values.copy()
    rng = np.random.default_rng(seed)
    if (reference_area is None) != (reference_industry is None):
        raise ValueError("면적 표본 풀의 값과 업종을 함께 지정해야 합니다")
    ref_area = area if reference_area is None else reference_area
    ref_industry = industry if reference_industry is None else reference_industry
    ref_values = pd.to_numeric(ref_area, errors="coerce").to_numpy(dtype="float64")
    ref_values = np.where(np.isfinite(ref_values) & (ref_values > 0), ref_values, np.nan)
    keys, ref_lo, ref_hi, ref_order = _groups(ref_industry.astype(str).to_numpy())
    pools = {}
    for key, a, b in zip(keys, ref_lo, ref_hi, strict=True):
        pool = ref_values[ref_order[a:b]]
        pools[key] = pool[~np.isnan(pool)]
    uniq, lo, hi, order = _groups(industry.astype(str).to_numpy())
    for key, a, b in zip(uniq, lo, hi, strict=True):
        idx = order[a:b]
        pool = pools.get(key, np.array([]))
        missing = idx[np.isnan(values[idx])]
        if len(pool) and len(missing):
            out[missing] = rng.choice(pool, size=len(missing))
    return pd.Series(out, index=area.index)


def brand_key(names: pd.Series) -> pd.Series:
    """상호의 첫 낱말 — 가맹 브랜드 대용(B6). 상호가 없으면 빈 문자열이라 서로 묶지 않는다.

    TODO(가정): '카페' · '치킨' 같은 일반 낱말도 한 브랜드로 묶인다. 가맹 브랜드 목록이 생기면 바꾼다.
    """
    normalized = names.astype("string").fillna("").map(lambda s: unicodedata.normalize("NFKC", s))
    first = normalized.str.strip().str.split().str[0].fillna("")
    first = first.str.replace(r"[^\w가-힣]", "", regex=True)
    return first.where(first.str.len() >= 2, "").astype(str)


def brand_prior(all_licenses: pd.DataFrame, queries: pd.DataFrame) -> np.ndarray:
    """queries(industry_code · brand_key · ref)마다 같은 업종·상호의 이전 점포 수(B6)."""
    if "business_name" not in all_licenses:
        return np.full(len(queries), np.nan)
    lic = all_licenses.dropna(subset=["open_date"])
    brand = brand_key(lic["business_name"]).to_numpy()
    keys = np.where(brand != "", lic["industry_code"].astype(str).to_numpy() + "|" + brand, "")
    opened = _days(lic["open_date"])
    uniq, lo, hi, order = _groups(keys)
    table = {k: np.sort(opened[order[a:b]]) for k, a, b in zip(uniq, lo, hi, strict=True) if k}

    ref = _days(queries["ref"])
    qbrand = queries["brand_key"].to_numpy()
    qkeys = np.where(qbrand != "", queries["industry_code"].astype(str).to_numpy() + "|" + qbrand, "")
    out = np.full(len(queries), np.nan)
    quniq, qlo, qhi, qorder = _groups(qkeys)
    for k, a, b in zip(quniq, qlo, qhi, strict=True):
        if not k:
            continue  # 상호를 모르는 점포 — 0으로 두면 '처음 여는 브랜드'라고 잘못 알려 준다
        idx = qorder[a:b]
        arr = table.get(k)
        out[idx] = 0.0 if arr is None else np.searchsorted(arr, ref[idx], "left")
    return out


def store_features(labels: pd.DataFrame, all_licenses: pd.DataFrame) -> pd.DataFrame:
    """labels에 STORE 칼럼을 붙여 돌려준다. 원천 칼럼이 없으면 그 피처는 전부 결측이다."""
    out = pd.DataFrame(index=labels.index)
    if "area_sqm" in labels:
        # 채움은 시간 분할 후 학습 구간으로만 한다. 여기서 전체 레이블을 표본 풀로 쓰면
        # 검증/테스트 면적의 분포가 학습 피처로 들어간다.
        area = pd.to_numeric(labels["area_sqm"], errors="coerce")
        out["log_area"] = np.log1p(area.where(np.isfinite(area) & (area > 0)))
    else:
        out["log_area"] = np.nan
    if "business_name" in labels:
        queries = pd.DataFrame(
            {
                "brand_key": brand_key(labels["business_name"]).to_numpy(),
                "industry_code": labels["industry_code"].astype(str).to_numpy(),
                "ref": labels["open_date"].to_numpy(),
            }
        )
        out["log_brand_prior"] = np.log1p(brand_prior(all_licenses, queries))
    else:
        out["log_brand_prior"] = np.nan
    if "address" in labels:
        address = labels["address"].astype("string").fillna("")
        found = address.str.contains(r"지하|지층|\bB\d|비\d층", case=False, regex=True, na=False)
        out["is_basement"] = np.where(address.str.len() > 0, found.astype(float), np.nan)
    else:
        out["is_basement"] = np.nan
    if "multi_use" in labels:
        # 원천은 Y · N, DB는 boolean이다. 둘 다 받고 모르는 값은 결측으로 둔다
        mapped = labels["multi_use"].map({True: 1.0, False: 0.0, "Y": 1.0, "N": 0.0})
        out["is_multi_use"] = pd.to_numeric(mapped, errors="coerce")
    else:
        out["is_multi_use"] = np.nan
    return out


def fill_store_area(frame: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """학습 구간의 실제 면적만으로 결측을 채운다. 원천 면적 칼럼은 보존한다."""
    if "area_sqm" not in frame:
        return frame
    out = frame.copy()
    out["log_area"] = np.log1p(
        fill_area(
            frame["area_sqm"],
            frame["industry_code"],
            seed=SEED,
            reference_area=reference["area_sqm"],
            reference_industry=reference["industry_code"],
        )
    )
    return out


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
    store = store_features(df, all_licenses)
    for c in STORE:
        df[c] = store[c].to_numpy()
    if spatial.has_coordinates(all_licenses) and spatial.has_coordinates(df):
        place = spatial.spatial_features(
            all_licenses, df[["industry_code", "lon", "lat"]].assign(ref=df["open_date"])
        )
        for c in SPATIAL:
            df[c] = place[c].to_numpy()
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
def _dmatrix(df: pd.DataFrame, features: list[str] | None = None):
    import xgboost as xgb

    d = xgb.DMatrix(df[features or FEATURES], enable_categorical=True)
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
    train: pd.DataFrame,
    valid: pd.DataFrame | None,
    distribution: str,
    scale: float,
    rounds: int | None,
    features: list[str] | None = None,
):
    import xgboost as xgb

    params = {**PARAMS, "aft_loss_distribution": distribution, "aft_loss_distribution_scale": scale}
    dtrain = _dmatrix(train, features)
    if valid is None:
        return xgb.train(params, dtrain, num_boost_round=rounds or 100, verbose_eval=False)
    dvalid = _dmatrix(valid, features)
    return xgb.train(
        params,
        dtrain,
        num_boost_round=NUM_BOOST_ROUND,
        evals=[(dvalid, "valid")],
        early_stopping_rounds=EARLY_STOPPING,
        verbose_eval=False,
    )


def _mu(booster, df: pd.DataFrame, features: list[str] | None = None) -> np.ndarray:
    import xgboost as xgb

    d = xgb.DMatrix(df[features or FEATURES], enable_categorical=True)
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
    features: list[str] = field(default_factory=lambda: list(FEATURES))


def _search_one(tr, va, dist, scale, start, features=None) -> dict[str, Any]:
    booster = _fit(tr, va, dist, scale, None, features)
    c = harrell_c(va["duration_months"], np.exp(_mu(booster, va, features)), va["event"])
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
    행사용 한시적 신고 · 팝업은 학습 · 평가 · 인허가 이력에서 한 번에 뺀다(B9).
    """
    licenses = drop_temporary(licenses)
    everything = label_frame(licenses, obs_end, start=min(starts))
    if covered is None:
        covered = covered_industries(
            everything[everything["open_date"] >= pd.Timestamp(START_DATE)], min_stores
        )
    data = attach_features(everything[everything["industry_code"].isin(covered)], panel, licenses)
    names = feature_names(data)
    va_raw = data[data["open_year"] == VALID_YEAR]
    te_raw = data[data["open_year"] > VALID_YEAR]

    def train_part(start: date) -> pd.DataFrame:
        return data[(data["open_year"] <= TRAIN_LAST_YEAR) & (data["open_date"] >= pd.Timestamp(start))]

    tr0_raw = train_part(starts[0])
    if min(len(tr0_raw), len(va_raw), len(te_raw)) == 0:
        sizes = f"학습 {len(tr0_raw)} · 검증 {len(va_raw)} · 테스트 {len(te_raw)}"
        raise ValueError(f"개업 연도 분할에 빈 구간이 있다({sizes})")
    tr0 = fill_store_area(tr0_raw, tr0_raw)
    va0 = fill_store_area(va_raw, tr0_raw)

    search = [_search_one(tr0, va0, d, s, starts[0], names) for d in DISTRIBUTIONS for s in SCALES]
    first = min(search, key=lambda s: s["valid_nloglik"])
    for start in starts[1:]:
        reference = train_part(start)
        search.append(
            _search_one(
                fill_store_area(reference, reference),
                fill_store_area(va_raw, reference),
                first["distribution"],
                first["scale"],
                start,
                names,
            )
        )
    best = min(search, key=lambda s: s["valid_nloglik"])
    start = date.fromisoformat(best["train_start"])
    reference = train_part(start)
    tr = fill_store_area(reference, reference)
    va = fill_store_area(va_raw, reference)
    te = fill_store_area(te_raw, reference)

    mu_test = _mu(best["booster"], te, names)
    metrics: dict[str, Any] = {
        "obs_end": everything.attrs["obs_end"],
        "covered_industries": covered,
        "features": names,
        "spatial": {
            "used": [c for c in SPATIAL if c in names],
            "coverage": (
                float(data["lon"].notna().mean()) if "lon" in data else None
            ),  # 좌표가 있는 학습 행 비율
        },
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
        "area_imputation": {"reference_split": "train", "reference_rows": len(reference), "seed": SEED},
        "test": evaluate(te, mu_test, best["distribution"], best["scale"]),
        # 테스트(2024년 이후 개업)는 3년이 안 지나 3년 생존 확률은 검증 코호트(2023년 개업)로 본다.
        # 검증은 분포 · 폭 선택에 쓰였으므로 약간 낙관적인 값이다
        "valid_survival_prob_36m": prob_metrics(
            va, _mu(best["booster"], va, names), best["distribution"], best["scale"], 36
        ),
    }
    if baselines:
        metrics["baselines_test"] = _baselines(tr, te)

    full = data[data["open_date"] >= pd.Timestamp(start)]
    # 모델 선택과 테스트 평가가 끝난 뒤의 운영 모델은 전 기간으로 다시 학습한다.
    full = fill_store_area(full, full)
    final = _fit(full, None, best["distribution"], best["scale"], best["rounds"], names)
    return SurvivalModel(final, best["distribution"], best["scale"], covered, obs_end, metrics, names)


def predict_next(model: SurvivalModel, panel: pd.DataFrame, licenses: pd.DataFrame) -> pd.DataFrame:
    """적용 업종 조합의 '지금 개업하는 점포' 생존기간 · 생존 확률.

    칼럼: dong_code · industry_code · survival_p10 · p50 · p90(개월) · survival_prob_12m · survival_prob_36m.
    인허가 이력은 업종별 관측 종료일을 기준일로 계산한다.
    """
    columns = [*sales.KEYS, "survival_p10", "survival_p50", "survival_p90"]
    columns += [f"survival_prob_{h}m" for h in PROB_HORIZONS]
    licenses = drop_temporary(licenses)
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
    # '지금 개업하는 일반 점포' 기준값 — 화면 기본 희망 면적 · 독립 점포 · 지상 · 다중이용업소 아님.
    # 예측표가 행정동 × 업종이라 점포 값을 하나로 고정한다. 희망 면적별 확률은 스키마 · API가 바뀌는 일이다
    future["log_area"] = math.log1p(REFERENCE_AREA_SQM)
    future["log_brand_prior"] = math.log1p(0)
    future["is_basement"] = 0.0
    future["is_multi_use"] = 0.0
    if any(c in model.features for c in SPATIAL):
        # 예측 행은 좌표가 없다 — 같은 행정동 · 업종 영업 점포들 위치의 중앙값을 쓴다(batch.ml.spatial)
        place = spatial.representative_features(licenses, future[sales.KEYS], model.obs_end)
        for c in SPATIAL:
            future[c] = place[c].to_numpy()
    df = _with_categories(future, panel)
    mu = _mu(model.booster, df, model.features)
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
