"""기존 인허가 행의 점포 칼럼 4개를 원천 ZIP에서 보충한다.

준비: python -m batch.pipeline.license_store_fields --raw-zip RAW.zip --output-dir DIR
점검: python -m batch.pipeline.license_store_fields --csv DIR/store_fields.csv --report REPORT.json
적재: 위 점검 명령에 --write 추가. DATABASE_URL은 적용할 DB를 명시한다.

원래 license_id를 다시 만들고 개업일을 대조한다. 기존 행 전체가 연결되지 않거나
이미 채운 값과 충돌하면 중단한다. 행 추가/삭제와 기존 6개 칼럼 변경은 하지 않는다.
빈 면적은 NULL로 남긴다. 실행 전 대상 business_license를 pg_dump로 백업한다.
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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

FIELDS = ("business_name", "area_sqm", "address", "multi_use")
CSV_COLUMNS = ("license_id", "source_open_date", *FIELDS)
AREA_COLUMNS = ("소재지면적", "시설면적", "시설총규모")
INVALID_BYTES = re.compile(r"[\udc80-\udcff]")
CORE_SQL = """
SELECT count(*) AS rows,
       md5(string_agg(md5(ROW(license_id, dong_code, industry_code,
           open_date, close_date, status)::text), '' ORDER BY license_id)) AS core_sha
FROM business_license
"""
COUNTS_SQL = """
SELECT count(*) AS rows, count(business_name) AS business_name,
       count(area_sqm) AS area_sqm, count(address) AS address,
       count(multi_use) AS multi_use
FROM business_license
"""


def clean(value: str | None) -> str | None:
    """디코딩 불가 셀은 NULL. 원문 표기는 유지하고 앞뒤 공백/NUL만 제거한다."""
    if value is None or INVALID_BYTES.search(value):
        return None
    return value.replace("\x00", "").strip() or None


def store_fields(row: dict) -> tuple:
    name = clean(row.get("사업장명"))
    address = clean(row.get("도로명주소")) or clean(row.get("지번주소"))
    if (name and len(name) > 200) or (address and len(address) > 300):
        raise ValueError("상호/주소가 DB 칼럼 길이를 초과합니다. 잘라서 적재하지 않습니다")
    area = None
    for column in AREA_COLUMNS:
        value = clean(row.get(column))
        try:
            candidate = Decimal(value.replace(",", "")) if value else None
        except InvalidOperation:
            continue
        if candidate is not None and candidate.is_finite() and candidate > 0:
            candidate = candidate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if candidate == 0:
                continue
            if candidate > Decimal("99999999.99"):
                raise ValueError("면적이 NUMERIC(10,2) 범위를 초과합니다")
            area = str(candidate)
            break
    flag = clean(row.get("다중이용업소여부"))
    if flag is not None and flag.upper() not in {"Y", "N"}:
        raise ValueError("알 수 없는 다중이용업소여부 값입니다")
    multi = None if flag is None else ("true" if flag.upper() == "Y" else "false")
    return name, area, address, multi


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(raw_zip: Path, output_dir: Path) -> dict:
    from batch.pipeline.license_mapping import SOURCES, normalize
    from batch.pipeline.licenses import parse_date, stable_id

    output_dir.mkdir(parents=True, exist_ok=False)
    index_path = output_dir / "source_index.sqlite"
    counts, sources = Counter(), []
    input_sha = sha256(raw_zip)
    with sqlite3.connect(index_path) as index, zipfile.ZipFile(raw_zip) as archive:
        index.execute(
            "CREATE TABLE fields (license_id TEXT PRIMARY KEY, source_open_date TEXT NOT NULL, "
            "business_name TEXT, area_sqm TEXT, address TEXT, multi_use TEXT)"
        )
        for kind in SOURCES:
            filename = f"d4_licences/서울시 {kind} 인허가 정보.csv"
            if archive.namelist().count(filename) != 1:
                raise ValueError(f"원천 CSV가 유일하게 있어야 합니다: {filename}")
            count = Counter()
            with archive.open(filename) as binary:
                reader = csv.DictReader(
                    io.TextIOWrapper(binary, encoding="cp949", errors="surrogateescape", newline=""),
                    strict=True,
                )
                required = {
                    "개방자치단체코드",
                    "관리번호",
                    "인허가일자",
                    "사업장명",
                    "도로명주소",
                    "지번주소",
                }
                if not required.issubset(reader.fieldnames or []) or not set(AREA_COLUMNS).intersection(
                    reader.fieldnames or []
                ):
                    raise ValueError(f"필수 원천 칼럼이 없습니다: {filename}")
                for row in reader:
                    count["input_rows"] += 1
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"CSV 너비 오류: {filename}, 행 {reader.line_num}")
                    try:
                        lid, _ = stable_id(
                            kind, normalize(row["개방자치단체코드"]), normalize(row["관리번호"])
                        )
                        opened = parse_date(row["인허가일자"], required=True)
                    except ValueError:
                        count["invalid_identity_or_open_date"] += 1
                        continue
                    values = store_fields(row)
                    count["undecodable_store_cells"] += sum(
                        bool(INVALID_BYTES.search(row.get(c, "")))
                        for c in ("사업장명", "도로명주소", "지번주소", *AREA_COLUMNS, "다중이용업소여부")
                    )
                    record = (lid, opened.isoformat(), *values)
                    old = index.execute("SELECT * FROM fields WHERE license_id=?", (lid,)).fetchone()
                    if old is not None:
                        if old != record:
                            raise ValueError(f"점포 값이 다른 중복 license_id: {lid}")
                        count["equivalent_duplicates"] += 1
                        continue
                    index.execute("INSERT INTO fields VALUES (?,?,?,?,?,?)", record)
                    count["unique_rows"] += 1
                    for field, value in zip(FIELDS, values, strict=True):
                        count[field] += value is not None
                index.commit()
            sources.append({"kind": kind, **count})
            counts.update(count)
            print(json.dumps(sources[-1], ensure_ascii=False), flush=True)
        csv_path = output_dir / "store_fields.csv"
        with csv_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(index.execute("SELECT * FROM fields ORDER BY license_id"))
    if sha256(raw_zip) != input_sha:
        raise ValueError("처리 중 원천 ZIP이 바뀌었습니다")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "raw_zip_sha256": input_sha,
        "csv_sha256": sha256(csv_path),
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
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("LOCK TABLE business_license IN SHARE ROW EXCLUSIVE MODE")
            cur.execute(CORE_SQL)
            before_core = cur.fetchone()
            cur.execute(COUNTS_SQL)
            before = dict(zip(("rows", *FIELDS), cur.fetchone(), strict=True))
            cur.execute("""
                CREATE TEMP TABLE ts_license_store_fields (
                    license_id VARCHAR(40) PRIMARY KEY, source_open_date DATE NOT NULL,
                    business_name VARCHAR(200), area_sqm NUMERIC(10,2), address VARCHAR(300),
                    multi_use BOOLEAN, CHECK (area_sqm IS NULL OR area_sqm > 0)
                ) ON COMMIT DROP
            """)
            with cur.copy("COPY ts_license_store_fields FROM STDIN WITH (FORMAT CSV, HEADER)") as copy:
                with csv_path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        copy.write(block)
            cur.execute("ANALYZE ts_license_store_fields")
            cur.execute("""
                SELECT count(*) FILTER (WHERE s.license_id IS NULL),
                       count(*) FILTER (WHERE s.license_id IS NOT NULL
                                         AND s.source_open_date IS DISTINCT FROM t.open_date)
                FROM business_license t LEFT JOIN ts_license_store_fields s USING (license_id)
            """)
            missing, date_conflicts = cur.fetchone()
            if not before["rows"] or missing or date_conflicts:
                raise ValueError(f"기존 행 연결 실패: 미연결 {missing}, 개업일 불일치 {date_conflicts}")
            conflicts = " OR ".join(f"(t.{c} IS NOT NULL AND t.{c} IS DISTINCT FROM s.{c})" for c in FIELDS)
            cur.execute(
                "SELECT count(*) FROM business_license t JOIN ts_license_store_fields s "
                f"USING (license_id) WHERE {conflicts}"
            )
            if cur.fetchone()[0]:
                raise ValueError("이미 채운 점포 값과 원천이 다릅니다. 기존 값을 덮어쓰지 않습니다")
            cur.execute("""
                SELECT count(*) FROM ts_license_store_fields s
                LEFT JOIN business_license t USING (license_id) WHERE t.license_id IS NULL
            """)
            source_only = cur.fetchone()[0]
            assignments = ", ".join(f"{c} = COALESCE(t.{c}, s.{c})" for c in FIELDS)
            different = " OR ".join(f"t.{c} IS DISTINCT FROM s.{c}" for c in FIELDS)
            cur.execute(
                f"UPDATE business_license t SET {assignments} FROM ts_license_store_fields s "
                f"WHERE t.license_id = s.license_id AND ({different})"
            )
            updated = cur.rowcount
            cur.execute(CORE_SQL)
            if cur.fetchone() != before_core:
                raise ValueError("기존 행 수 또는 점포 외 칼럼이 바뀌어 전체 롤백합니다")
            cur.execute(
                "SELECT count(*) FROM business_license t JOIN ts_license_store_fields s "
                f"USING (license_id) WHERE {different}"
            )
            if cur.fetchone()[0]:
                raise ValueError("적재 후 원천 값 대조 실패 — 전체 롤백합니다")
            cur.execute(COUNTS_SQL)
            after = dict(zip(("rows", *FIELDS), cur.fetchone(), strict=True))
            if sha256(csv_path) != csv_sha:
                raise ValueError("적재 중 준비 CSV가 바뀌었습니다")
            cur.execute("DROP TABLE pg_temp.ts_license_store_fields")
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
