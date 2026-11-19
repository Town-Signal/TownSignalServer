import csv
import io
import zipfile

import pytest

from batch.pipeline.license_mapping import SOURCES
from batch.pipeline.license_store_fields import CSV_COLUMNS, apply_csv, clean, prepare, store_fields
from batch.pipeline.licenses import stable_id


def test_area_fallback_and_missing_values_are_not_imputed():
    row = {"소재지면적": "0", "시설면적": "-1", "시설총규모": "1,234.567"}
    assert store_fields(row) == (None, "1234.57", None, None)
    assert store_fields({"소재지면적": "NaN", "시설면적": "Infinity"}) == (None,) * 4


def test_road_address_falls_back_and_no_is_distinct_from_unknown():
    row = {"사업장명": " 점포\x00 ", "도로명주소": " ", "지번주소": " 서울시 중구 ", "다중이용업소여부": "N"}
    assert store_fields(row) == ("점포", None, "서울시 중구", "false")
    assert store_fields({"다중이용업소여부": "Y"})[-1] == "true"
    assert store_fields({})[-1] is None
    with pytest.raises(ValueError, match="다중이용"):
        store_fields({"다중이용업소여부": "unknown"})


def test_undecodable_cells_are_null_and_long_names_are_not_truncated():
    assert clean("이름\udc81") is None
    assert store_fields({"도로명주소": "\udc81", "지번주소": "지번"})[2] == "지번"
    with pytest.raises(ValueError, match="길이"):
        store_fields({"사업장명": "가" * 201})


def make_zip(tmp_path, *, duplicate=None, omit_kind=None):
    path = tmp_path / "raw.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for kind in SOURCES:
            if kind == omit_kind:
                continue
            row = {
                "개방자치단체코드": "3010000",
                "관리번호": "0001",
                "인허가일자": "20220101",
                "사업장명": "가게",
                "도로명주소": "서울특별시 중구",
                "지번주소": "",
                "시설면적": "33",
            }
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
            if duplicate and kind == next(iter(SOURCES)):
                writer.writerow({**row, **duplicate})
            archive.writestr(f"d4_licences/서울시 {kind} 인허가 정보.csv", stream.getvalue().encode("cp949"))
    return path


def test_prepare_uses_existing_identity_and_collapses_equivalent_duplicates(tmp_path):
    output = tmp_path / "prepared"
    report = prepare(make_zip(tmp_path, duplicate={"시설면적": "33.00"}), output)
    assert report["counts"]["unique_rows"] == len(SOURCES)
    assert report["counts"]["equivalent_duplicates"] == 1
    with (output / "store_fields.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(CSV_COLUMNS)
        rows = list(reader)
    expected = {stable_id(kind, "3010000", "0001")[0] for kind in SOURCES}
    assert {r["license_id"] for r in rows} == expected
    assert all(r["source_open_date"] == "2022-01-01" and r["multi_use"] == "" for r in rows)


def test_prepare_rejects_conflicting_duplicates(tmp_path):
    with pytest.raises(ValueError, match="중복"):
        prepare(make_zip(tmp_path, duplicate={"사업장명": "다른 점포"}), tmp_path / "out")


def test_prepare_requires_all_six_sources(tmp_path):
    with pytest.raises(ValueError, match="원천 CSV"):
        prepare(make_zip(tmp_path, omit_kind=next(iter(SOURCES))), tmp_path / "out")


@pytest.fixture
def isolated_license_db(db_engine):
    raw = db_engine.raw_connection()
    conn = raw.driver_connection
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("CREATE TEMP TABLE business_license (LIKE public.business_license INCLUDING ALL)")
            conn.execute("""
                INSERT INTO business_license (license_id, open_date, status)
                VALUES ('test-license', '2022-01-01', '영업/정상')
            """)
            yield conn
    finally:
        raw.rollback()
        raw.close()


def prepared_csv(tmp_path, **changes):
    path = tmp_path / "prepared.csv"
    row = dict(
        zip(CSV_COLUMNS, ("test-license", "2022-01-01", "점포", "33.00", "주소", "false"), strict=True)
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({**row, **changes})
    return path


def test_apply_dry_run_rolls_back_and_write_is_idempotent(isolated_license_db, tmp_path):
    conn = isolated_license_db
    path = prepared_csv(tmp_path)
    result = apply_csv(conn, path)
    assert result["updated"] == 1 and not result["committed"]
    assert conn.execute("SELECT business_name FROM business_license").fetchone() == (None,)
    result = apply_csv(conn, path, write=True)
    assert result["updated"] == 1 and result["core_unchanged"]
    assert conn.execute("SELECT status, multi_use FROM business_license").fetchone() == ("영업/정상", False)
    assert apply_csv(conn, path, write=True)["updated"] == 0


@pytest.mark.parametrize("changes", [{"license_id": "missing"}, {"source_open_date": "2020-01-01"}])
def test_apply_rejects_wrong_identity_or_date(isolated_license_db, tmp_path, changes):
    with pytest.raises(ValueError, match="연결 실패"):
        apply_csv(isolated_license_db, prepared_csv(tmp_path, **changes), write=True)
    assert isolated_license_db.execute("SELECT business_name FROM business_license").fetchone() == (None,)


def test_apply_does_not_overwrite_existing_values(isolated_license_db, tmp_path):
    conn = isolated_license_db
    conn.execute("UPDATE business_license SET business_name = '검수된 값'")
    with pytest.raises(ValueError, match="덮어쓰지"):
        apply_csv(conn, prepared_csv(tmp_path), write=True)
    assert conn.execute("SELECT business_name FROM business_license").fetchone() == ("검수된 값",)
