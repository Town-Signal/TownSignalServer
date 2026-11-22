import csv
import io
import json
import zipfile

import pytest

from batch.pipeline.license_coordinates import CSV_COLUMNS, apply_csv, coordinates, prepare
from batch.pipeline.license_mapping import SOURCES
from batch.pipeline.licenses import SOURCE_CRS, stable_id

# 서울시청 근처(EPSG:5174). 변환하면 경도 127.0 · 위도 37.5 근처
SEOUL_X, SEOUL_Y = "198000", "451500"


def transform():
    """좌표 변환은 배치 의존성이라 기본 CI에서는 건너뛴다."""
    pyproj = pytest.importorskip("pyproj", reason="pyproj는 배치 의존성이다")
    return pyproj.Transformer.from_crs(SOURCE_CRS, 4326, always_xy=True).transform


def test_source_coordinate_becomes_wgs84_with_six_decimals():
    lon, lat = coordinates({"좌표정보(X)": SEOUL_X, "좌표정보(Y)": SEOUL_Y}, transform())
    assert 126.9 < float(lon) < 127.1 and 37.4 < float(lat) < 37.6
    assert len(lon.split(".")[1]) == 6 and len(lat.split(".")[1]) == 6


def test_missing_zero_and_out_of_seoul_coordinates_are_null():
    move = transform()
    assert coordinates({"좌표정보(X)": "", "좌표정보(Y)": SEOUL_Y}, move) == (None, None)
    assert coordinates({"좌표정보(X)": "0", "좌표정보(Y)": "0"}, move) == (None, None)
    assert coordinates({"좌표정보(X)": "글자", "좌표정보(Y)": SEOUL_Y}, move) == (None, None)
    assert coordinates({"좌표정보(X)": "390000", "좌표정보(Y)": "180000"}, move) == (None, None)


def make_zip(tmp_path, *, duplicate=None, cp949_names=False, name="raw.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for kind in SOURCES:
            row = {
                "개방자치단체코드": "3010000",
                "관리번호": "0001",
                "인허가일자": "20220101",
                "좌표정보(X)": SEOUL_X,
                "좌표정보(Y)": SEOUL_Y,
            }
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
            if duplicate is not None and kind == next(iter(SOURCES)):
                writer.writerow({**row, **duplicate})
            entry = f"d4_licences/서울시 {kind} 인허가 정보.csv"
            if cp949_names:
                # 윈도우 기본 압축처럼 파일명을 cp949로 담은 ZIP도 찾아야 한다
                entry = entry.encode("cp949").decode("cp437")
            archive.writestr(entry, stream.getvalue().encode("cp949"))
    return path


def test_prepare_writes_one_row_per_license_with_coordinates(tmp_path):
    transform()
    output = tmp_path / "prepared"
    report = prepare(make_zip(tmp_path), output)
    assert report["counts"]["unique_rows"] == len(SOURCES)
    assert report["counts"]["with_coordinate"] == len(SOURCES)
    assert report["source_crs"] == SOURCE_CRS and report["target_crs"] == 4326
    with (output / "coordinates.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(CSV_COLUMNS)
        rows = list(reader)
    assert {r["license_id"] for r in rows} == {stable_id(k, "3010000", "0001")[0] for k in SOURCES}
    assert all(r["source_open_date"] == "2022-01-01" and r["lon"] and r["lat"] for r in rows)
    assert json.loads((output / "prepare_report.json").read_text(encoding="utf-8"))["csv_sha256"]


def test_prepare_reads_cp949_entry_names(tmp_path):
    transform()
    report = prepare(make_zip(tmp_path, cp949_names=True), tmp_path / "prepared")
    assert report["counts"]["unique_rows"] == len(SOURCES)


def test_prepare_collapses_equivalent_duplicates_and_rejects_conflicts(tmp_path):
    transform()
    same = prepare(make_zip(tmp_path, duplicate={}), tmp_path / "same")
    assert same["counts"]["equivalent_duplicates"] == 1
    assert same["counts"]["unique_rows"] == len(SOURCES)
    conflict = make_zip(tmp_path, duplicate={"좌표정보(X)": "198100"}, name="conflict.zip")
    with pytest.raises(ValueError, match="좌표가 다른 중복"):
        prepare(conflict, tmp_path / "conflict")


def test_prepare_requires_every_source_and_coordinate_columns(tmp_path):
    transform()
    path = tmp_path / "partial.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("d4_licences/서울시 미용업 인허가 정보.csv", "x\n1\n".encode("cp949"))
    with pytest.raises(ValueError, match="유일하게"):
        prepare(path, tmp_path / "out1")

    path = tmp_path / "nocols.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for kind in SOURCES:
            archive.writestr(
                f"d4_licences/서울시 {kind} 인허가 정보.csv",
                "개방자치단체코드,관리번호,인허가일자\n3010000,1,20220101\n".encode("cp949"),
            )
    with pytest.raises(ValueError, match="필수 원천 칼럼"):
        prepare(path, tmp_path / "out2")


# ---------- DB 적재 ----------
@pytest.fixture
def isolated_license_db(db_engine):
    """public.business_license를 건드리지 않고 같은 구조의 임시 표로 검증한다."""
    raw = db_engine.raw_connection()
    conn = raw.driver_connection
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("CREATE TEMP TABLE business_license (LIKE public.business_license INCLUDING ALL)")
            conn.execute("""
                INSERT INTO business_license (license_id, open_date, status)
                VALUES ('test-license', '2022-01-01', '영업/정상'), ('no-coord', '2022-02-01', '폐업')
            """)
            yield conn
    finally:
        raw.rollback()
        raw.close()


def prepared_csv(tmp_path, name="prepared.csv", **changes):
    path = tmp_path / name
    rows = [
        dict(zip(CSV_COLUMNS, ("test-license", "2022-01-01", "127.001000", "37.500000"), strict=True)),
        dict(zip(CSV_COLUMNS, ("no-coord", "2022-02-01", "", ""), strict=True)),
    ]
    rows[0].update(changes)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_apply_dry_run_rolls_back_and_write_is_idempotent(isolated_license_db, tmp_path):
    conn = isolated_license_db
    path = prepared_csv(tmp_path)
    result = apply_csv(conn, path)
    assert result["updated"] == 1 and not result["committed"]
    assert conn.execute("SELECT count(lon) FROM business_license").fetchone() == (0,)

    result = apply_csv(conn, path, write=True)
    assert result["updated"] == 1 and result["core_unchanged"]
    assert result["after"] == {"rows": 2, "lon": 1, "lat": 1}
    lon, lat = conn.execute(
        "SELECT lon, lat FROM business_license WHERE license_id = 'test-license'"
    ).fetchone()
    assert (float(lon), float(lat)) == (127.001, 37.5)
    assert apply_csv(conn, path, write=True)["updated"] == 0


@pytest.mark.parametrize("changes", [{"license_id": "missing"}, {"source_open_date": "2020-01-01"}])
def test_apply_rejects_wrong_identity_or_date(isolated_license_db, tmp_path, changes):
    with pytest.raises(ValueError, match="연결 실패"):
        apply_csv(isolated_license_db, prepared_csv(tmp_path, **changes), write=True)
    assert isolated_license_db.execute("SELECT count(lon) FROM business_license").fetchone() == (0,)


def test_apply_does_not_overwrite_an_existing_coordinate(isolated_license_db, tmp_path):
    conn = isolated_license_db
    conn.execute("UPDATE business_license SET lon = 127.1, lat = 37.51 WHERE license_id = 'test-license'")
    with pytest.raises(ValueError, match="이미 채운 좌표"):
        apply_csv(conn, prepared_csv(tmp_path), write=True)
    assert conn.execute("SELECT count(lon) FROM business_license").fetchone() == (1,)
