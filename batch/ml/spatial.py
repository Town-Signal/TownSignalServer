"""모델 B 위치 피처 — 인허가 좌표로 만드는 반경 경쟁·폐업 밀도(분석계획서 B11).

왜 필요한가
    모델 B의 피처는 상권·이력이 모두 행정동 × 업종 단위다. 같은 동 · 같은 업종이면 대로변
    점포와 골목 안쪽 점포가 같은 값을 받아 업종 내 순위를 가리지 못한다(업종 내 C-index 0.608).
    점포 칼럼 4개(상호 · 면적 · 주소 · 다중이용)에 이어 '위치' 축을 넣는다.

기준일(as-of)
    점포의 개업 분기 **시작일**을 기준으로 센다. 개업일 자신이 아니라 분기 시작일을 쓰는 이유는
    모델 A · B의 상권 피처가 모두 직전 분기(q − 1) 값이라는 규칙과 맞추기 위해서다. 기준일이
    개업일보다 앞이므로 미래 정보가 섞이지 않고, 자기 점포도 기준일에는 아직 없어 빠진다.

좌표
    원천 LOCALDATA의 `좌표정보(X · Y)`(EPSG:5174)를 business_license.lon · lat(EPSG:4326)로
    적재한 값을 쓴다. 거리 계산은 미터 좌표(EPSG:5181 — 행정동 경계와 같은 중부원점)로 바꿔서 한다.
    좌표가 없는 점포(원천 결측 약 8.5%)는 피처가 결측이고, 반경 안의 이웃으로도 세지 않는다.

전 조합 예측
    서빙 표는 행정동 × 업종이라 '지금 개업하는 점포'의 좌표가 없다. 그 행은 같은 행정동 · 업종의
    영업 중 점포들 위치에서 구한 값의 **중앙값**을 쓴다(representative_features). 동 안의 대표적인
    자리라는 가정이며, 동 사이 차이(업종 내 순위)는 그대로 살아 있다.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

COORD_COLUMNS = ["lon", "lat"]
SOURCE_EPSG = 4326  # business_license.lon · lat
METRIC_EPSG = 5181  # 거리 계산용 미터 좌표(행정동 경계와 같은 원점)
RADII_M = (100, 300)
CLOSED_RADIUS_M = 300
CLOSED_WINDOW_DAYS = 365
NEAREST_MAX_M = 2000.0  # 이 거리까지 없으면 결측으로 둔다(동종 점포가 드문 업종)

SPATIAL = [
    "spt_nearest_m",  # 기준일에 영업 중인 가장 가까운 같은 업종 점포까지의 거리(m)
    "spt_same_100m",  # 반경 100m 안 같은 업종 영업 점포 수
    "spt_same_300m",  # 반경 300m 안 같은 업종 영업 점포 수
    "spt_closed_300m_1y",  # 반경 300m 안에서 기준일 전 1년에 폐업한 같은 업종 점포 수
    "spt_all_300m",  # 반경 300m 안 업종 무관 영업 점포 수(상권 밀도)
]


def has_coordinates(frame: pd.DataFrame) -> bool:
    """좌표 칼럼이 있고 값이 하나라도 있으면 True. 적재 전이면 False — 피처를 쓰지 않는다."""
    if not set(COORD_COLUMNS).issubset(frame.columns):
        return False
    lon = pd.to_numeric(frame["lon"], errors="coerce")
    lat = pd.to_numeric(frame["lat"], errors="coerce")
    return bool((lon.notna() & lat.notna()).any())


def to_metric(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    """경위도 → EPSG:5181 미터 좌표. 좌표가 없는 행은 NaN."""
    from pyproj import Transformer

    lon = pd.to_numeric(pd.Series(lon).reset_index(drop=True), errors="coerce").to_numpy(dtype="float64")
    lat = pd.to_numeric(pd.Series(lat).reset_index(drop=True), errors="coerce").to_numpy(dtype="float64")
    ok = np.isfinite(lon) & np.isfinite(lat)
    x = np.full(len(lon), np.nan)
    y = np.full(len(lon), np.nan)
    if ok.any():
        transformer = Transformer.from_crs(SOURCE_EPSG, METRIC_EPSG, always_xy=True)
        x[ok], y[ok] = transformer.transform(lon[ok], lat[ok])
    return x, y


def _days(values) -> np.ndarray:
    """날짜 → 1970-01-01 기준 일수(float). 빈 날짜는 NaN."""
    s = pd.to_datetime(pd.Series(values).reset_index(drop=True))
    days = s.to_numpy(dtype="datetime64[D]").astype("int64").astype("float64")
    days[s.isna().to_numpy()] = np.nan
    return days


def quarter_start(values) -> pd.Series:
    """기준일 — 그 날짜가 든 분기의 첫날."""
    s = pd.to_datetime(pd.Series(values).reset_index(drop=True))
    return s.dt.to_period("Q").dt.start_time


def _tree(x: np.ndarray, y: np.ndarray):
    from scipy.spatial import cKDTree

    return cKDTree(np.column_stack([x, y]))


def spatial_features(
    all_licenses: pd.DataFrame, queries: pd.DataFrame, *, exclude_self: bool = False
) -> pd.DataFrame:
    """queries(industry_code · ref · lon · lat)에 SPATIAL 칼럼을 붙여 돌려준다.

    all_licenses는 기간 제한 없는 전체 인허가(좌표 · 개폐업일). 기준일은 ref가 든 분기의 첫날이다.
    좌표가 없는 쿼리 행과 이웃은 센 값에 들어가지 않는다(결측으로 남는다).

    학습 행은 기준일에 아직 열리지 않아 자기 자신이 이웃에 없다. 반대로 영업 중 점포의 위치에서
    값을 구할 때(representative_features)는 자기 자신이 이웃에 들어가므로 exclude_self로 뺀다.
    """
    q = queries.reset_index(drop=True).copy()
    out = pd.DataFrame(index=q.index, columns=SPATIAL, dtype="float64")
    if not has_coordinates(all_licenses) or not has_coordinates(q):
        return out

    nx, ny = to_metric(all_licenses["lon"], all_licenses["lat"])
    n_open = _days(all_licenses["open_date"])
    n_close = _days(all_licenses["close_date"])
    n_ind = all_licenses["industry_code"].astype(str).to_numpy()
    ok = np.isfinite(nx) & np.isfinite(ny) & np.isfinite(n_open)
    nx, ny, n_open, n_close, n_ind = nx[ok], ny[ok], n_open[ok], n_close[ok], n_ind[ok]

    qx, qy = to_metric(q["lon"], q["lat"])
    q_start = _days(quarter_start(q["ref"]))
    q_ind = q["industry_code"].astype(str).to_numpy()
    usable = np.isfinite(qx) & np.isfinite(qy) & np.isfinite(q_start)
    if not usable.any():
        return out

    values = {name: np.full(len(q), np.nan) for name in SPATIAL}
    buckets = pd.DataFrame({"start": q_start, "industry": q_ind}).loc[usable]

    # 업종 무관 밀도 — 기준일별로 한 번만 만든다
    for start, index in buckets.groupby("start", sort=True).groups.items():
        rows = np.asarray(index)
        active = (n_open < start) & (np.isnan(n_close) | (n_close >= start))
        if active.any():
            tree = _tree(nx[active], ny[active])
            counted = tree.query_ball_point(
                np.column_stack([qx[rows], qy[rows]]), CLOSED_RADIUS_M, return_length=True
            )
            values["spt_all_300m"][rows] = np.maximum(counted - exclude_self, 0)
        else:
            values["spt_all_300m"][rows] = 0.0

    # 같은 업종 — (기준일, 업종)별
    for (start, industry), index in buckets.groupby(["start", "industry"], sort=True).groups.items():
        rows = np.asarray(index)
        points = np.column_stack([qx[rows], qy[rows]])
        same = n_ind == industry
        active = same & (n_open < start) & (np.isnan(n_close) | (n_close >= start))
        if active.any():
            tree = _tree(nx[active], ny[active])
            for radius in RADII_M:
                counted = tree.query_ball_point(points, radius, return_length=True)
                values[f"spt_same_{radius}m"][rows] = np.maximum(counted - exclude_self, 0)
            k = 2 if exclude_self else 1
            distance, _ = tree.query(points, k=k, distance_upper_bound=NEAREST_MAX_M)
            nearest = distance[:, -1] if k > 1 else distance
            values["spt_nearest_m"][rows] = np.where(np.isfinite(nearest), nearest, np.nan)
        else:
            for radius in RADII_M:
                values[f"spt_same_{radius}m"][rows] = 0.0
        closed = same & (n_close >= start - CLOSED_WINDOW_DAYS) & (n_close < start)
        if closed.any():
            tree = _tree(nx[closed], ny[closed])
            values["spt_closed_300m_1y"][rows] = tree.query_ball_point(
                points, CLOSED_RADIUS_M, return_length=True
            )
        else:
            values["spt_closed_300m_1y"][rows] = 0.0

    for name in SPATIAL:
        out[name] = values[name]
    return out


def representative_features(
    all_licenses: pd.DataFrame, keys: pd.DataFrame, ref: date | dict[str, date]
) -> pd.DataFrame:
    """전 조합 예측용 — 같은 행정동 · 업종 영업 점포들 위치에서 구한 SPATIAL의 중앙값.

    keys는 dong_code · industry_code. ref는 관측 종료일(업종별로 다르면 dict).
    영업 중 점포에 좌표가 하나도 없으면 그 조합은 결측이다.
    """
    k = keys.reset_index(drop=True).copy()
    out = pd.DataFrame(index=k.index, columns=SPATIAL, dtype="float64")
    if not has_coordinates(all_licenses):
        return out

    lic = all_licenses.reset_index(drop=True)
    if isinstance(ref, dict):
        ends = lic["industry_code"].astype(str).map(ref)
    else:
        ends = pd.Series(ref, index=lic.index)
    ends = pd.to_datetime(ends)
    live = lic["close_date"].isna() & ends.notna() & pd.to_numeric(lic["lon"], errors="coerce").notna()
    active = lic[live.to_numpy()].copy()
    if active.empty:
        return out
    active["ref"] = ends[live.to_numpy()].to_numpy()
    feats = spatial_features(lic, active[["industry_code", "ref", "lon", "lat"]], exclude_self=True)
    feats[["dong_code", "industry_code"]] = active[["dong_code", "industry_code"]].to_numpy()
    median = feats.groupby(["dong_code", "industry_code"], observed=True)[SPATIAL].median()
    joined = k.merge(median, on=["dong_code", "industry_code"], how="left")
    for name in SPATIAL:
        out[name] = joined[name].to_numpy()
    return out
