"""기존 인허가 행의 좌표(lon · lat)를 원천 ZIP에서 보충한다.

준비: python -m batch.pipeline.license_coordinates --raw-zip RAW.zip --output-dir DIR
점검: python -m batch.pipeline.license_coordinates --csv DIR/coordinates.csv --report REPORT.json
적재: 위 점검 명령에 --write 추가. DATABASE_URL은 적용할 DB를 명시한다.

점포 칼럼 보충(license_store_fields)과 같은 방식이다 — license_id를 다시 만들고 개업일을 대조한다.
기존 행 전체가 연결되지 않거나 이미 채운 값과 충돌하면 중단한다. 행 추가/삭제와 다른 칼럼 변경은
하지 않는다. 좌표가 없거나 0인 행은 NULL로 남긴다(원천 결측 약 8.5%).

좌표는 원천 `좌표정보(X · Y)`(EPSG:5174)를 WGS84 경위도로 바꿔 넣는다. 미터 거리가 필요한 곳
(batch.ml.spatial)에서 EPSG:5181로 다시 바꾼다. 원천 좌표계는 licenses.SOURCE_CRS와 같은 값을 쓴다.
일반음식점의 별도 `X좌표/Y좌표`는 원점·출처가 달라 쓰지 않는다(licenses.py 보고서 주석).
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import zipfile
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FIELDS = ("lon", "lat")
CSV_COLUMNS = ("license_id", "source_open_date", *FIELDS)
COORD_COLUMNS = ("좌표정보(X)", "좌표정보(Y)")
TARGET_CRS = 4326
INVALID_BYTES = re.compile(r"[\udc80-\udcff]")
# 서울 바깥 좌표는 원천 오류로 보고 버린다(행정동 공간조인과 같은 기준 — 경계 안에 들어가지 않는 점)
LON_RANGE = (126.6, 127.3)
LAT_RANGE = (37.3, 37.75)
CORE_SQL = """
SELECT count(*) AS rows,
       md5(string_agg(md5(ROW(license_id, dong_code, industry_code, open_date, close_date, status,
           business_name, area_sqm, address, multi_use)::text), '' ORDER BY license_id)) AS core_sha
FROM business_license
"""
COUNTS_SQL = "SELECT count(*) AS rows, count(lon) AS lon, count(lat) AS lat FROM business_license"


def clean(value: str | None) -> str | None:
    if value is None or INVALID_BYTES.search(value):
        return None
    return value.replace("\x00", "").strip() or None


def _entry(archive: zipfile.ZipFile, kind: str) -> zipfile.ZipInfo:
    """원천 CSV 항목. ZIP이 파일명을 cp949로 담은 경우도 찾아 준다."""
    name = f"d4_licences/서울시 {kind} 인허가 정보.csv"
    found = []
    for info in archive.infolist():
        candidates = {info.filename}
        try:  # ZIP이 파일명을 cp949로 담으면 zipfile이 cp437로 읽어 깨진 이름이 된다
            candidates.add(info.filename.encode("cp437").decode("cp949"))
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        if name in candidates:
            found.append(info)
    if len(found) != 1:
        raise ValueError(f"원천 CSV가 유일하게 있어야 합니다: {name}")
    return found[0]


def coordinates(row: dict, transform) -> tuple[str | None, str | None]:
    """원천 좌표 → (lon, lat) 문자열. 결측 · 0 · 범위 밖은 (None, None)."""
    raw = [clean(row.get(c)) for c in COORD_COLUMNS]
    if not all(raw):
        return None, None
    try:
        x, y = (float(value.replace(",", "")) for value in raw)
    except ValueError:
        return None, None
    if not (x and y) or x != x or y != y or x in (float("inf"), float("-inf")):
        return None, None
    lon, lat = transform(x, y)
    if not (LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
        return None, None
    quantize = lambda v: str(Decimal(repr(v)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))  # noqa: E731
    return quantize(lon), quantize(lat)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(raw_zip: Path, output_dir: Path) -> dict:
    from pyproj import Transformer

    from batch.pipeline.license_mapping import SOURCES, normalize
    from batch.pipeline.licenses import SOURCE_CRS, parse_date, stable_id

    transformer = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)
    transform = transformer.transform
    output_dir.mkdir(parents=True, exist_ok=False)
    index_path = output_dir / "source_index.sqlite"
    counts, sources = Counter(), []
    input_sha = sha256(raw_zip)
    with sqlite3.connect(index_path) as index, zipfile.ZipFile(raw_zip) as archive:
        index.execute(
            "CREATE TABLE coords (license_id TEXT PRIMARY KEY, source_open_date TEXT NOT NULL, "
            "lon TEXT, lat TEXT)"
        )
        for kind in SOURCES:
            entry = _entry(archive, kind)
            count = Counter()
            with archive.open(entry) as binary:
                reader = csv.DictReader(
                    io.TextIOWrapper(binary, encoding="cp949", errors="surrogateescape", newline=""),
                    strict=True,
                )
                required = {"개방자치단체코드", "관리번호", "인허가일자", *COORD_COLUMNS}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError(f"필수 원천 칼럼이 없습니다: {entry.filename}")
                for row in reader:
                    count["input_rows"] += 1
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"CSV 너비 오류: {kind}, 행 {reader.line_num}")
                    try:
                        lid, _ = stable_id(
                            kind, normalize(row["개방자치단체코드"]), normalize(row["관리번호"])
                        )
                        opened = parse_date(row["인허가일자"], required=True)
                    except ValueError:
                        count["invalid_identity_or_open_date"] += 1
                        continue
                    lon, lat = coordinates(row, transform)
                    count["with_coordinate"] += lon is not None
                    record = (lid, opened.isoformat(), lon, lat)
                    old = index.execute("SELECT * FROM coords WHERE license_id=?", (lid,)).fetchone()
                    if old is not None:
                        if old != record:
                            raise ValueError(f"좌표가 다른 중복 license_id: {lid}")
                        count["equivalent_duplicates"] += 1
                        continue
                    index.execute("INSERT INTO coords VALUES (?,?,?,?)", record)
                    count["unique_rows"] += 1
                index.commit()
            sources.append({"kind": kind, **count})
            counts.update(count)
            print(json.dumps(sources[-1], ensure_ascii=False), flush=True)
        csv_path = output_dir / "coordinates.csv"
        with csv_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(index.execute("SELECT * FROM coords ORDER BY license_id"))
    if sha256(raw_zip) != input_sha:
        raise ValueError("처리 중 원천 ZIP이 바뀌었습니다")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "raw_zip_sha256": input_sha,
        "csv_sha256": sha256(csv_path),
        "source_crs": SOURCE_CRS,
        "target_crs": TARGET_CRS,
        "counts": dict(counts),
        "sources": sources,
    }
    (output_dir / "prepare_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def apply_csv(conn, csv_path: Path, *, write: bool = False) -> dict:
    """PostgreSQL 연결. 기본은 전체 검증 후 롤백하며, --write일 때만 값을 채운다."""
    with csv_path.open(encoding="utf-8", newline="") as stream:
        if next(csv.reader(stream), None) != list(CSV_COLUMNS):
            raise ValueError("준비 CSV의 칼럼 순서가 다릅니다")
    csv_sha = sha256(csv_path)
    with conn.transaction(force_rollback=not write):
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '300s'")
            cur.execute("LOCK TABLE business_license IN SHARE ROW EXCLUSIVE MODE")
            cur.execute(CORE_SQL)
            before_core = cur.fetchone()
            cur.execute(COUNTS_SQL)
            before = dict(zip(("rows", *FIELDS), cur.fetchone(), strict=True))
            cur.execute("""
                CREATE TEMP TABLE ts_license_coordinates (
                    license_id VARCHAR(40) PRIMARY KEY, source_open_date DATE NOT NULL,
                    lon NUMERIC(9,6), lat NUMERIC(8,6),
                    CHECK ((lon IS NULL) = (lat IS NULL))
                ) ON COMMIT DROP
            """)
            with cur.copy("COPY ts_license_coordinates FROM STDIN WITH (FORMAT CSV, HEADER)") as copy:
                with csv_path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        copy.write(block)
            cur.execute("ANALYZE ts_license_coordinates")
            cur.execute("""
                SELECT count(*) FILTER (WHERE s.license_id IS NULL),
                       count(*) FILTER (WHERE s.license_id IS NOT NULL
                                         AND s.source_open_date IS DISTINCT FROM t.open_date)
                FROM business_license t LEFT JOIN ts_license_coordinates s USING (license_id)
            """)
            missing, date_conflicts = cur.fetchone()
            if not before["rows"] or missing or date_conflicts:
                raise ValueError(f"기존 행 연결 실패: 미연결 {missing}, 개업일 불일치 {date_conflicts}")
            conflicts = " OR ".join(f"(t.{c} IS NOT NULL AND t.{c} IS DISTINCT FROM s.{c})" for c in FIELDS)
            cur.execute(
                "SELECT count(*) FROM business_license t JOIN ts_license_coordinates s "
                f"USING (license_id) WHERE {conflicts}"
            )
            if cur.fetchone()[0]:
                raise ValueError("이미 채운 좌표와 원천이 다릅니다. 기존 값을 덮어쓰지 않습니다")
            cur.execute("""
                SELECT count(*) FROM ts_license_coordinates s
                LEFT JOIN business_license t USING (license_id) WHERE t.license_id IS NULL
            """)
            source_only = cur.fetchone()[0]
            assignments = ", ".join(f"{c} = COALESCE(t.{c}, s.{c})" for c in FIELDS)
            different = " OR ".join(f"t.{c} IS DISTINCT FROM s.{c}" for c in FIELDS)
            cur.execute(
                f"UPDATE business_license t SET {assignments} FROM ts_license_coordinates s "
                f"WHERE t.license_id = s.license_id AND ({different})"
            )
            updated = cur.rowcount
            cur.execute(CORE_SQL)
            if cur.fetchone() != before_core:
                raise ValueError("기존 행 수 또는 좌표 외 칼럼이 바뀌어 전체 롤백합니다")
            cur.execute(
                "SELECT count(*) FROM business_license t JOIN ts_license_coordinates s "
                f"USING (license_id) WHERE {different}"
            )
            if cur.fetchone()[0]:
                raise ValueError("적재 후 원천 값 대조 실패 — 전체 롤백합니다")
            cur.execute(COUNTS_SQL)
            after = dict(zip(("rows", *FIELDS), cur.fetchone(), strict=True))
            if sha256(csv_path) != csv_sha:
                raise ValueError("적재 중 준비 CSV가 바뀌었습니다")
            cur.execute("DROP TABLE pg_temp.ts_license_coordinates")
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "committed": write,
        "csv_sha256": csv_sha,
        "before": before,
        "after": after,
        "updated": updated,
        "source_only": source_only,
        "core_unchanged": True,
        "core_checksum": before_core[1],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--raw-zip", type=Path)
    source.add_argument("--csv", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.raw_zip:
        if args.output_dir is None or args.write:
            parser.error("ZIP 준비는 --output-dir이 필요하고 --write를 받지 않습니다")
        result = prepare(args.raw_zip, args.output_dir)
    else:
        import psycopg

        if args.report is None:
            parser.error("DB 점검/적재는 --report가 필요합니다")
        dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            result = apply_csv(conn, args.csv, write=args.write)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
