"""모델 B 위치 피처(batch.ml.spatial) — 기준일 이전만 세는지, 반경이 맞는지 본다."""

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyproj", reason="좌표 변환은 배치 의존성이다")
pytest.importorskip("scipy", reason="반경 조회는 배치 의존성이다")

from batch.ml import spatial  # noqa: E402

BASE_LON, BASE_LAT = 127.0, 37.50
NEAR = 0.0005  # 약 55m
MID = 0.0018  # 약 200m
FAR = 0.0050  # 약 555m


def licenses(rows):
    frame = pd.DataFrame(
        rows, columns=["license_id", "dong_code", "industry_code", "open_date", "close_date", "lat"]
    )
    frame["open_date"] = pd.to_datetime(frame["open_date"])
    frame["close_date"] = pd.to_datetime(frame["close_date"])
    frame["lon"] = BASE_LON
    return frame


def query(industry="CS100001", ref="2024-05-15", lat=BASE_LAT):
    return pd.DataFrame([{"industry_code": industry, "ref": pd.Timestamp(ref), "lon": BASE_LON, "lat": lat}])


def test_radius_counts_and_nearest_distance():
    frame = licenses(
        [
            ("near", "11110515", "CS100001", "2020-01-01", None, BASE_LAT + NEAR),
            ("mid", "11110515", "CS100001", "2020-01-01", None, BASE_LAT + MID),
            ("far", "11110515", "CS100001", "2020-01-01", None, BASE_LAT + FAR),
            ("other", "11110515", "CS200001", "2020-01-01", None, BASE_LAT + NEAR),
        ]
    )
    out = spatial.spatial_features(frame, query()).iloc[0]
    assert out["spt_same_100m"] == 1  # near만
    assert out["spt_same_300m"] == 2  # near + mid
    assert 40 < out["spt_nearest_m"] < 70
    assert out["spt_all_300m"] == 3  # 다른 업종(other)까지 센다


def test_only_shops_open_before_the_quarter_start_are_counted():
    frame = licenses(
        [
            # 기준 분기(2024Q2) 시작 전에 열려 영업 중 — 센다
            ("before", "11110515", "CS100001", "2024-03-31", None, BASE_LAT + NEAR),
            # 같은 분기에 열림 — 기준일(2024-04-01)에는 아직 없다
            ("same_quarter", "11110515", "CS100001", "2024-04-02", None, BASE_LAT + NEAR),
            # 기준일 전에 이미 닫힘 — 영업 점포로 세지 않는다
            ("closed", "11110515", "CS100001", "2020-01-01", "2024-02-01", BASE_LAT + NEAR),
        ]
    )
    out = spatial.spatial_features(frame, query(ref="2024-05-15")).iloc[0]
    assert out["spt_same_100m"] == 1
    assert out["spt_closed_300m_1y"] == 1  # 기준일 전 1년에 닫힌 한 곳


def test_closures_outside_the_one_year_window_are_not_counted():
    frame = licenses(
        [("old", "11110515", "CS100001", "2015-01-01", "2022-01-01", BASE_LAT + NEAR)]
    )
    out = spatial.spatial_features(frame, query(ref="2024-05-15")).iloc[0]
    assert out["spt_closed_300m_1y"] == 0
    assert out["spt_same_100m"] == 0
    assert pd.isna(out["spt_nearest_m"])


def test_queries_and_neighbours_without_coordinates_stay_missing():
    frame = licenses([("near", "11110515", "CS100001", "2020-01-01", None, BASE_LAT + NEAR)])
    no_coords = frame.drop(columns=["lon", "lat"])
    assert spatial.spatial_features(no_coords, query()).isna().all().all()
    blank = query()
    blank["lon"] = None
    assert spatial.spatial_features(frame, blank).isna().all().all()
    assert not spatial.has_coordinates(no_coords)


def test_representative_features_use_the_median_of_active_shops():
    frame = licenses(
        [
            ("a", "11110515", "CS100001", "2020-01-01", None, BASE_LAT),
            ("b", "11110515", "CS100001", "2020-01-01", None, BASE_LAT + NEAR),
            ("c", "11110515", "CS100001", "2020-01-01", None, BASE_LAT + FAR),
            ("gone", "11110515", "CS100001", "2020-01-01", "2023-01-01", BASE_LAT),
        ]
    )
    keys = pd.DataFrame([{"dong_code": "11110515", "industry_code": "CS100001"}])
    out = spatial.representative_features(frame, keys, {"CS100001": pd.Timestamp("2026-10-05").date()})
    # a · b는 서로 55m 안, c는 500m 넘게 떨어져 있다 → 100m 안 점포 수의 중앙값은 1
    assert out.loc[0, "spt_same_100m"] == 1
    assert out.loc[0, "spt_same_300m"] == 1
    # 폐업한 점포는 대표값 계산에서 빠진다(영업 중만)
    assert out.loc[0, "spt_all_300m"] == 1
