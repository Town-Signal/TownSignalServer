"""3단계 지표 변환·적재. 원천과 DB 칼럼의 대응 및 결측·범위 규칙을 명시한다.

잘못된 행은 JSONL로 보류하고 전체 DB 적재를 거부한다. 0을 결측으로 바꾸지 않는다.
중복 키는 값이 같더라도 기록·보류한다. 검증이 끝난 세 Parquet만 함께 공개한다.
DB 적재는 임시 스테이징에 COPY 후 upsert하며, 기존과 같은 값은 갱신하지 않는다.
"""

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from batch.pipeline.masters import TABLE_COLUMNS, load_master_rows
from batch.pipeline.raw_conversion import SOURCE_ROW, fingerprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Field:
    source: str
    target: str
    bits: int = 0
    nullable: bool = True
    pattern: str = ""


@dataclass(frozen=True)
class Indicator:
    dataset: str
    table: str
    keys: tuple[str, ...]
    fields: tuple[Field, ...]


DONG = Field("행정동_코드", "dong_code", nullable=False, pattern=r"[0-9]{8}")
INDUSTRY = Field("서비스_업종_코드", "industry_code", nullable=False, pattern=r"CS[0-9]{6}")
QUARTER = Field("기준_년분기_코드", "year_quarter", nullable=False, pattern=r"[0-9]{4}[1-4]")
SPECS = (
    Indicator(
        "d1",
        "sales_quarterly",
        ("dong_code", "industry_code", "year_quarter"),
        (
            DONG,
            INDUSTRY,
            QUARTER,
            Field("당월_매출_금액", "amount", 64, False),
            Field("당월_매출_건수", "txn_count", 32, False),
            Field("주중_매출_금액", "weekday_amount", 64),
            Field("주말_매출_금액", "weekend_amount", 64),
            *[
                Field(f"시간대_{hour}_매출_금액", f"hour_{hour.replace('~', '_')}", 64)
                for hour in ("00~06", "06~11", "11~14", "14~17", "17~21", "21~24")
            ],
            Field("남성_매출_건수", "male_count", 32),
            Field("여성_매출_건수", "female_count", 32),
            *[Field(f"연령대_{age}_매출_건수", f"age_{age}", 32) for age in ("10", "20", "30", "40", "50")],
            Field("연령대_60_이상_매출_건수", "age_60", 32),
        ),
    ),
    Indicator(
        "d2",
        "store_quarterly",
        ("dong_code", "industry_code", "year_quarter"),
        (
            DONG,
            INDUSTRY,
            QUARTER,
            Field("유사_업종_점포_수", "store_count", 32, False),
            Field("프랜차이즈_점포_수", "franchise_count", 32),
            Field("개업_점포_수", "open_count", 32),
            Field("폐업_점포_수", "close_count", 32),
        ),
    ),
    Indicator(
        "d3",
        "population_quarterly",
        ("dong_code", "year_quarter"),
        (
            DONG,
            QUARTER,
            Field("총_유동인구_수", "total", 64, False),
            Field("남성_유동인구_수", "male", 64),
            Field("여성_유동인구_수", "female", 64),
            *[Field(f"연령대_{age}_유동인구_수", f"age_{age}", 64) for age in ("10", "20", "30", "40", "50")],
            Field("연령대_60_이상_유동인구_수", "age_60", 64),
            *[
                Field(f"시간대_{hour}_유동인구_수", f"time_{hour}", 64)
                for hour in ("00_06", "06_11", "11_14", "14_17", "17_21", "21_24")
            ],
        ),
    ),
)
STORE_BASIS = {
    "source": "유사_업종_점포_수",
    "target": "store_count",
    "meaning": "프랜차이즈를 포함한 전체 점포 수",
    "validation": "유사_업종_점포_수 = 점포_수 + 프랜차이즈_점포_수",
    "reference": "https://data.seoul.go.kr/dataList/OA-22172/S/1/datasetView.do",
    "basis": "서울시 설명의 전체=일반+프랜차이즈 관계와 현재 원천 전체 행의 합계 관계를 대조",
}


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _parse(value, field: Field):
    if value is None or value == "":
        if field.nullable:
            return None
        raise ValueError("필수 값 결측")
    if not isinstance(value, str):
        raise ValueError("1단계 문자열 형식이 아님")
    if field.bits:
        if re.fullmatch(r"[0-9]+", value, flags=re.ASCII) is None:
            raise ValueError("음수 또는 정수가 아닌 값")
        number = int(value)
        if number >= 2 ** (field.bits - 1):
            raise ValueError(f"DB int{field.bits} 범위 초과")
        return number
    if re.fullmatch(field.pattern, value, flags=re.ASCII) is None:
        raise ValueError("코드 또는 분기 형식 오류")
    return value


def _masters(path: Path, source: dict, report: dict) -> tuple[dict, dict]:
    import pyarrow.parquet as pq

    report["inputs"].append(fingerprint(path))
    master = json.loads(path.read_text(encoding="utf-8"))
    if master["status"] not in ("completed", "completed_with_issues"):
        raise ValueError("완료된 2단계 마스터 보고서가 필요합니다")
    if master["source_run_id"] != source["run_id"]:
        raise ValueError("1단계와 2단계의 원천 실행 ID가 다릅니다")
    rows = {}
    for name, columns in TABLE_COLUMNS.items():
        before = master["files"][name]
        file = Path(before["path"]).resolve()
        if fingerprint(file) != before:
            raise ValueError(f"2단계 결과가 변경되었습니다: {name}")
        report["inputs"].append(before)
        table = pq.read_table(file)
        if table.schema.names != columns or len(table) != master["counts"][name]:
            raise ValueError(f"마스터 스키마 또는 행 수가 다릅니다: {name}")
        if any(str(field.type) != "string" for field in table.schema):
            raise ValueError(f"마스터 문자열 타입이 다릅니다: {name}")
        rows[name] = table.to_pylist()
        keys = [row[columns[0]] for row in rows[name]]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError(f"비어 있거나 중복된 마스터: {name}")
    if {row["district_code"] for row in rows["dong"]} - {row["district_code"] for row in rows["district"]}:
        raise ValueError("마스터의 행정동→자치구 연결이 불완전합니다")
    report["master_run_id"] = master["run_id"]
    report["master_warnings"] = master["warnings"]
    report["master_counts"] = {name: len(values) for name, values in rows.items()}
    return rows, master


def _arrow_schema(spec: Indicator):
    import pyarrow as pa

    def dtype(field):
        return pa.int32() if field.bits == 32 else pa.int64() if field.bits == 64 else pa.string()

    return pa.schema([pa.field(f.target, dtype(f), nullable=f.nullable) for f in spec.fields])


def _transform(
    spec: Indicator,
    files: list[dict],
    masters: dict,
    master_report: dict,
    staging: Path,
    report: dict,
    batch_size: int,
) -> set:
    import pyarrow as pa
    import pyarrow.parquet as pq

    result = {
        "dataset": spec.dataset,
        "input_rows": 0,
        "output_rows": 0,
        "held_rows": 0,
        "duplicate_keys": 0,
        "unknown_dong_codes": {},
        "unknown_industry_codes": {},
        "mapping": [
            {
                "source": f.source,
                "target": f.target,
                "type": str(_arrow_schema(spec).field(f.target).type),
                "nullable": f.nullable,
            }
            for f in spec.fields
        ],
        "files": [],
        "columns": {},
        "rows_by_quarter": {},
    }
    report["tables"][spec.table] = result
    known_dongs = {row["dong_code"] for row in masters["dong"]}
    known_industries = {row["industry_code"] for row in masters["industry"]}
    master_marks = {Path(item["path"]).resolve(): item for item in master_report["inputs"]}
    seen_paths, seen_keys = set(), set()
    nulls, zeroes, quarters, unknown_dongs, unknown_industries = (Counter() for _ in range(5))
    minima, maxima, warnings = {}, {}, Counter()
    used = {field.source for field in spec.fields}
    schema = _arrow_schema(spec)
    with (staging / f"{spec.table}.issues.jsonl").open("x", encoding="utf-8") as issues:
        with pq.ParquetWriter(staging / f"{spec.table}.parquet", schema, compression="zstd") as writer:
            for item in files:
                path = Path(item["output"]).resolve()
                if path in seen_paths or path.suffix != ".parquet":
                    raise ValueError(f"중복이거나 Parquet가 아닌 원천: {path}")
                seen_paths.add(path)
                before = fingerprint(path)
                if master_marks.get(path) != before:
                    raise ValueError(f"2단계에 사용한 원천과 현재 파일이 다릅니다: {path.name}")
                report["inputs"].append(before)
                pf = pq.ParquetFile(path)
                required = used | {SOURCE_ROW} | ({"점포_수"} if spec.dataset == "d2" else set())
                if required - set(pf.schema_arrow.names):
                    raise ValueError(
                        f"필수 원천 칼럼 없음: {path.name}, {sorted(required - set(pf.schema_arrow.names))}"
                    )
                if any(str(pf.schema_arrow.field(c).type) != "string" for c in required - {SOURCE_ROW}):
                    raise ValueError(f"1단계 문자열 보존 형식이 아님: {path.name}")
                metadata = pf.schema_arrow.metadata or {}
                if metadata.get(b"source_sha256", b"").decode() != item["source"]["sha256"]:
                    raise ValueError(f"원천 식별자 불일치: {path.name}")
                if item.get("quarantined_rows", 0) or pf.metadata.num_rows != item["output_rows"]:
                    raise ValueError(f"1단계 보류 행이 있거나 행 수가 다름: {path.name}")
                unused = set(pf.schema_arrow.names) - used - {SOURCE_ROW}
                audit_columns = [c for c in unused if c.endswith(("매출_금액", "매출_건수"))]
                file_result = {
                    "source": str(path),
                    "input_rows": 0,
                    "output_rows": 0,
                    "held_rows": 0,
                    "unused_columns": sorted(unused),
                    "unused_columns_preserved_in": str(path),
                }
                result["files"].append(file_result)
                for batch in pf.iter_batches(batch_size=batch_size):
                    data = batch.to_pydict()
                    buffer = []
                    for index in range(batch.num_rows):
                        result["input_rows"] += 1
                        file_result["input_rows"] += 1
                        context = {"source": str(path), "record": data[SOURCE_ROW][index]}
                        row, errors = {}, []
                        for field in spec.fields:
                            try:
                                row[field.target] = _parse(data[field.source][index], field)
                            except ValueError as exc:
                                errors.append({"column": field.source, "reason": str(exc)})
                        for column in audit_columns:
                            value = data[column][index]
                            if isinstance(value, str) and re.fullmatch(r"-[0-9]+", value, flags=re.ASCII):
                                warnings["unused_negative_values"] += 1
                                issues.write(
                                    json.dumps(
                                        {
                                            **context,
                                            "severity": "warning",
                                            "reason": "unused_negative_value",
                                            "column": column,
                                            "value": value,
                                            "action": "원천 Parquet에 그대로 보존",
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                        dong, industry = row.get("dong_code"), row.get("industry_code")
                        if dong is not None and dong not in known_dongs:
                            unknown_dongs[dong] += 1
                            errors.append({"column": DONG.source, "reason": "마스터에 없는 행정동 코드"})
                        if industry is not None and industry not in known_industries:
                            unknown_industries[industry] += 1
                            errors.append({"column": INDUSTRY.source, "reason": "마스터에 없는 업종 코드"})
                        if spec.dataset == "d2" and not errors:
                            try:
                                general = _parse(
                                    data["점포_수"][index], Field("점포_수", "general", 32, False)
                                )
                                franchise = row["franchise_count"]
                                if franchise is None:
                                    warnings["store_sum_unverifiable"] += 1
                                elif general + franchise != row["store_count"]:
                                    raise ValueError("전체 점포 수와 일반+프랜차이즈 합계가 다름")
                            except ValueError as exc:
                                errors.append({"column": "점포_수", "reason": str(exc)})
                        if all(key in row for key in spec.keys):
                            key = tuple(row[k] for k in spec.keys)
                            if key in seen_keys:
                                result["duplicate_keys"] += 1
                                errors.append({"reason": "중복 기본키", "key": key})
                            seen_keys.add(key)
                        if errors:
                            result["held_rows"] += 1
                            file_result["held_rows"] += 1
                            issues.write(
                                json.dumps(
                                    {
                                        **context,
                                        "severity": "error",
                                        "errors": errors,
                                        "raw_values": {c: values[index] for c, values in data.items()},
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            continue
                        for field in spec.fields:
                            value = row[field.target]
                            if value is None:
                                nulls[field.target] += 1
                            elif field.bits:
                                zeroes[field.target] += value == 0
                                minima[field.target] = min(minima.get(field.target, value), value)
                                maxima[field.target] = max(maxima.get(field.target, value), value)
                        quarters[row["year_quarter"]] += 1
                        result["output_rows"] += 1
                        file_result["output_rows"] += 1
                        buffer.append(row)
                    if buffer:
                        writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                logger.info(
                    "지표 검증: %s (%s행, 보류 %s행)",
                    path.name,
                    file_result["input_rows"],
                    file_result["held_rows"],
                )
    result["unknown_dong_codes"] = dict(sorted(unknown_dongs.items()))
    result["unknown_industry_codes"] = dict(sorted(unknown_industries.items()))
    result["rows_by_quarter"] = dict(sorted(quarters.items()))
    result["columns"] = {
        field.target: {
            "nulls": nulls[field.target],
            "zeroes": zeroes[field.target] if field.bits else None,
            "min": minima.get(field.target),
            "max": maxima.get(field.target),
        }
        for field in spec.fields
    }
    for kind, count in sorted(warnings.items()):
        report["warnings"].append({"table": spec.table, "kind": kind, "count": count})
    return seen_keys


def _coverage(keys: dict, masters: dict) -> dict:
    sales, store, population = (keys[spec.table] for spec in SPECS)
    sales_industries = {key[1] for key in sales}
    return {
        "sales_without_store": len(sales - store),
        "store_without_sales": len(store - sales),
        "sales_without_population": sum((dong, quarter) not in population for dong, _, quarter in sales),
        "store_without_population": sum((dong, quarter) not in population for dong, _, quarter in store),
        "industries_with_sales": len(sales_industries),
        "industries_without_sales": [
            row for row in masters["industry"] if row["industry_code"] not in sales_industries
        ],
        "note": "커버리지 집계이며 조인으로 원천 행을 제거하거나 결측 매출 행을 생성하지 않음",
    }


def load_indicator_tables(conn, report: dict, masters: dict) -> dict:
    """열린 PostgreSQL 트랜잭션 안에서만 호출한다. 여섯 테이블 모두 같은 트랜잭션에 속한다."""
    import pyarrow.parquet as pq
    from sqlalchemy import BigInteger, Integer, MetaData, String, text

    if conn.dialect.name != "postgresql" or conn.dialect.driver != "psycopg":
        raise ValueError("DB 적재에는 postgresql+psycopg 드라이버가 필요합니다")
    if not conn.in_transaction():
        raise ValueError("마스터·지표 적재는 트랜잭션 안에서 실행해야 합니다")
    metadata = MetaData()
    metadata.reflect(conn, only=list(TABLE_COLUMNS) + [s.table for s in SPECS])
    for name, columns in TABLE_COLUMNS.items():
        codes = set(conn.execute(text(f"SELECT {columns[0]} FROM {name}")).scalars())
        if codes - {row[columns[0]] for row in masters[name]}:
            raise ValueError(f"입력 마스터 밖의 기존 코드가 있어 혼합 적재를 거부합니다: {name}")
    for spec in SPECS:
        table = metadata.tables[spec.table]
        if set(table.primary_key.columns.keys()) != set(spec.keys):
            raise ValueError(f"DB 기본키가 다릅니다: {spec.table}")
        for field in spec.fields:
            if field.target not in table.c:
                raise ValueError(f"DB 칼럼 없음: {spec.table}.{field.target}")
            column = table.c[field.target]
            correct = (
                isinstance(column.type, BigInteger)
                if field.bits == 64
                else isinstance(column.type, Integer) and not isinstance(column.type, BigInteger)
                if field.bits == 32
                else isinstance(column.type, String)
            )
            if not correct or column.nullable != field.nullable:
                raise ValueError(f"DB 타입/NULL 규칙 불일치: {spec.table}.{field.target}")
    db_result = {"master_upserted_rows": load_master_rows(conn, masters), "tables": {}}
    raw_connection = conn.connection.driver_connection
    for spec in SPECS:
        before = report["tables"][spec.table]["output"]
        path = Path(before["path"])
        if fingerprint(path) != before:
            raise ValueError(f"3단계 산출물이 변경되었습니다: {spec.table}")
        columns = [f.target for f in spec.fields]
        nonkeys = [name for name in columns if name not in spec.keys]
        staging = f"ts_{spec.table}_{uuid4().hex[:8]}"
        conn.execute(
            text(f"CREATE TEMP TABLE {staging} (LIKE {spec.table} INCLUDING DEFAULTS) ON COMMIT DROP")
        )
        with raw_connection.cursor() as cursor:
            with cursor.copy(f"COPY pg_temp.{staging} ({', '.join(columns)}) FROM STDIN") as copy:
                for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000, columns=columns):
                    for row in zip(*(batch.column(i).to_pylist() for i in range(len(columns))), strict=True):
                        copy.write_row(row)
        if fingerprint(path) != before:
            raise ValueError(f"DB 스테이징 적재 중 산출물이 변경되었습니다: {spec.table}")
        conn.execute(text(f"CREATE UNIQUE INDEX ON {staging} ({', '.join(spec.keys)})"))
        source_count = conn.scalar(text(f"SELECT count(*) FROM {staging}"))
        if source_count != report["tables"][spec.table]["output_rows"]:
            raise ValueError(f"DB 스테이징 행 수 불일치: {spec.table}")
        join = " AND ".join(f"t.{key}=s.{key}" for key in spec.keys)
        different = (
            f"({', '.join('t.' + col for col in nonkeys)}) IS DISTINCT FROM "
            f"({', '.join('s.' + col for col in nonkeys)})"
        )
        existing = conn.scalar(text(f"SELECT count(*) FROM {staging} s JOIN {spec.table} t ON {join}"))
        changed = conn.scalar(
            text(f"SELECT count(*) FROM {staging} s JOIN {spec.table} t ON {join} WHERE {different}")
        )
        updates = ", ".join(f"{col}=EXCLUDED.{col}" for col in nonkeys)
        change_filter = (
            f"({', '.join(spec.table + '.' + col for col in nonkeys)}) IS DISTINCT FROM "
            f"({', '.join('EXCLUDED.' + col for col in nonkeys)})"
        )
        touched = conn.execute(
            text(
                f"INSERT INTO {spec.table} ({', '.join(columns)}) SELECT {', '.join(columns)} FROM {staging} "
                f"ON CONFLICT ({', '.join(spec.keys)}) DO UPDATE SET {updates} WHERE {change_filter}"
            )
        ).rowcount
        missing_or_different = conn.scalar(
            text(
                f"SELECT count(*) FROM {staging} s LEFT JOIN {spec.table} t ON {join} "
                f"WHERE t.{spec.keys[0]} IS NULL OR {different}"
            )
        )
        if missing_or_different or touched != source_count - existing + changed:
            raise ValueError(f"DB 적재 후 값 또는 행 수 검증 실패: {spec.table}")
        db_result["tables"][spec.table] = {
            "input_rows": source_count,
            "inserted": source_count - existing,
            "updated": changed,
            "unchanged": existing - changed,
            "matched_input_rows": source_count,
            "total_rows": conn.scalar(text(f"SELECT count(*) FROM {spec.table}")),
        }
        conn.execute(text(f"DROP TABLE {staging}"))
        logger.info("DB 적재 검증: %s (%s행)", spec.table, source_count)
    return db_result


def process_indicators(
    source_report: Path,
    master_report: Path,
    processed_dir: Path,
    *,
    batch_size: int = 50_000,
    load_db: bool = False,
) -> dict:
    """입력 검증→세 지표 Parquet 공개→선택적 DB 적재. 실패 보고서도 별도 새 파일로 남긴다."""
    source_report, master_report = source_report.resolve(), master_report.resolve()
    processed_dir = processed_dir.resolve()
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다")
    source = json.loads(source_report.read_text(encoding="utf-8"))
    raw_dir = Path(source["raw_dir"]).resolve()
    if processed_dir == raw_dir or raw_dir in processed_dir.parents or processed_dir in raw_dir.parents:
        raise ValueError("원본과 산출물 디렉터리는 서로 겹칠 수 없습니다")
    seed_dir = raw_dir.parent / "seed"
    if processed_dir == seed_dir or seed_dir in processed_dir.parents or processed_dir in seed_dir.parents:
        raise ValueError("seed와 산출물 디렉터리는 서로 겹칠 수 없습니다")
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    output = processed_dir / "indicators" / run_id
    staging = output.with_name(f".{run_id}.partial")
    report_path = processed_dir / "reports" / f"load_indicators_{run_id}.json"
    report = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "source_run_id": source["run_id"],
        "source_report": str(source_report),
        "master_report": str(master_report),
        "output_root": str(output),
        "report_path": str(report_path),
        "inputs": [fingerprint(source_report)],
        "tables": {},
        "failures": [],
        "warnings": [],
        "database": {"status": "not_requested"},
        "store_count_basis": STORE_BASIS,
        "notes": ["2024년 공간 기준 변경을 넘어 성장세를 계산하지 않음", "인구의 2026년 분기도 보존"],
    }
    try:
        masters, master = _masters(master_report, source, report)
        selected = {}
        for spec in SPECS:
            selected[spec.table] = [f for f in source["files"] if f["dataset"] == spec.dataset]
            if not selected[spec.table] or any(f.get("dataset") == spec.dataset for f in source["failures"]):
                raise ValueError(f"완전히 변환되지 않은 원천: {spec.dataset}")
        staging.mkdir(parents=True, exist_ok=False)
        keys = {}
        for spec in SPECS:
            keys[spec.table] = _transform(
                spec, selected[spec.table], masters, master, staging, report, batch_size
            )
        for before in report["inputs"]:
            if fingerprint(Path(before["path"])) != before:
                raise RuntimeError(f"처리 중 입력이 변경되었습니다: {before['path']}")
        report["coverage"] = _coverage(keys, masters)
        del keys
        if report["coverage"]["industries_without_sales"]:
            report["warnings"].append(
                {
                    "kind": "industries_without_sales",
                    "count": len(report["coverage"]["industries_without_sales"]),
                }
            )
        held = sum(result["held_rows"] for result in report["tables"].values())
        if held:
            raise ValueError(
                f"보류한 행 {held}건이 있어 마스터/지표 DB 적재를 중단했습니다. JSONL을 확인하세요"
            )
        staging.rename(output)
        for spec in SPECS:
            result = report["tables"][spec.table]
            result["output"] = fingerprint(output / f"{spec.table}.parquet")
            result["issues_file"] = str(output / f"{spec.table}.issues.jsonl")
        if load_db:
            from dotenv import load_dotenv
            from sqlalchemy import create_engine
            from sqlalchemy.engine import make_url

            report["database"] = {"status": "failed"}
            load_dotenv()
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                raise ValueError("--load-db에는 명시적으로 설정한 DATABASE_URL이 필요합니다")
            url = make_url(database_url)
            report["database"]["target"] = {"host": url.host, "port": url.port, "database": url.database}
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            try:
                with engine.begin() as conn:
                    result = load_indicator_tables(conn, report, masters)
            finally:
                engine.dispose()
            report["database"].update({"status": "completed", **result, "existing_rows_deleted": 0})
        report["status"] = "completed_with_issues" if report["warnings"] else "completed"
    except Exception as exc:
        logger.exception("지표 처리 실패")
        report["failures"].append({"error_type": type(exc).__name__, "error": str(exc)})
        report["status"] = "failed"
        if staging.exists():
            report["held_artifacts"] = str(staging)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, report)
    return report
