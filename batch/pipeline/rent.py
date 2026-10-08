"""5단계 임대료. source Parquet → seed 상권 매핑 → 자치구 산술평균.

원천 3행 헤더의 분기·임대료·천원/㎡ 단위를 확인한다. 서울 64행 중 명시된 집계행
5개를 제외한 59개 상권을 모두 매핑하며, 누락·중복·필수 결측은 전체 작업 실패다.
임대료는 Decimal, 평균은 마지막에 ROUND_HALF_UP으로 소수 둘째 자리까지 반올림한다.
도봉구의 빈 seed 행은 매핑 대상이 아니다. district 마스터의 25개 구를 기준으로
집계해 도봉구 NULL 행도 보존한다. deposit_multiplier=15는 기존 스키마의 가정이다.
선택 분기만 commercial_market에 추가/갱신한다. district_rent의 최신 분기를 과거
분기로 덮어쓰는 DB 적재는 두 표 모두 롤백한다. Docker 실행 기능은 없다.
"""

import csv
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

from batch.pipeline.indicators import _masters
from batch.pipeline.masters import TABLE_COLUMNS
from batch.pipeline.raw_conversion import SOURCE_ROW, fingerprint
from batch.pipeline.raw_sources import SERVER_ROOT

logger = logging.getLogger(__name__)
AGGREGATES = {"서울", "도심", "강남", "영등포신촌", "기타"}
SEED_COLUMNS = ["자치구", "상권명", "임대료_천원당㎡_월", "기준분기"]
COLUMNS = {
    "commercial_market": ["market_name", "base_quarter", "district_code", "rent_per_sqm"],
    "district_rent": [
        "district_code",
        "base_quarter",
        "rent_per_sqm",
        "deposit_multiplier",
        "market_count",
        "confidence",
    ],
}
KEYS = {"commercial_market": ["market_name", "base_quarter"], "district_rent": ["district_code"]}


def _json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def quarter_code(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("분기는 문자열이어야 합니다")
    if re.fullmatch(r"[0-9]{4}Q[1-4]", value, flags=re.ASCII):
        return value
    match = re.fullmatch(r"([0-9]{4})년 ([1-4])분기", value, flags=re.ASCII)
    if match:
        return f"{match[1]}Q{match[2]}"
    raise ValueError(f"분기 형식 오류: {value!r}")


def rent_decimal(value: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value.strip()):
        raise ValueError("임대료 결측/음수/숫자 형식 오류")
    result = Decimal(value.strip())
    if result >= Decimal("1000000"):
        raise ValueError("임대료 NUMERIC(8,2) 범위 초과")
    return result.quantize(Decimal("0.01"))


def _quarter_columns(headers: list[list[str]]) -> dict[str, str]:
    if len(headers) != 3 or any(len(row) != len(headers[0]) for row in headers):
        raise ValueError("d5 원천의 3행 헤더가 필요합니다")
    if any(row[:4] != ["No", "지역", "지역", "지역"] for row in headers):
        raise ValueError("d5 지역 헤더 불일치")
    result = {}
    for name, measure, unit in zip(headers[0][4:], headers[1][4:], headers[2][4:], strict=True):
        quarter = quarter_code(name)
        if quarter in result or measure != "임대료" or unit != "천원/㎡":
            raise ValueError("분기 중복 또는 임대료/단위 헤더 불일치")
        result[quarter] = name
    if not result:
        raise ValueError("임대료 분기가 없습니다")
    return result


def build_rent_rows(
    rows: list[dict],
    headers: list[list[str]],
    seed: list[dict],
    districts: list[dict],
    base_quarter: str | None = None,
) -> tuple[dict, dict]:
    """전체 매핑 성공 시에만 두 표를 반환한다. 결측을 0으로 채우지 않는다."""
    quarters = _quarter_columns(headers)
    quarter = quarter_code(base_quarter) if base_quarter else max(quarters)
    if quarter not in quarters:
        raise ValueError(f"선택 분기가 원천에 없습니다: {quarter}")
    district_codes = {r["name"]: r["district_code"] for r in districts}
    if len(districts) != 25 or len(district_codes) != 25 or district_codes.get("도봉구") != "11320":
        raise ValueError("도봉구를 포함한 25개 자치구 마스터가 필요합니다")
    mapping, blanks, seed_values = {}, [], {}
    for line, record in enumerate(seed, start=2):
        if set(record) != set(SEED_COLUMNS) or any(v is None for v in record.values()):
            raise ValueError(f"seed 칼럼/너비 불일치: {line}행")
        district, market = record["자치구"].strip(), record["상권명"].strip()
        if district not in district_codes:
            raise ValueError(f"알 수 없는 seed 자치구: {line}행")
        seed_quarter = quarter_code(record["기준분기"].strip())
        if not market:
            if district != "도봉구" or record["임대료_천원당㎡_월"].strip():
                raise ValueError("빈 상권 행은 임대료도 빈 도봉구 행만 허용합니다")
            blanks.append(district)
            continue
        if len(market) > 40 or market in mapping:
            raise ValueError(f"seed 상권명 길이/중복 오류: {market}")
        mapping[market] = district_codes[district]
        seed_values[market] = (seed_quarter, rent_decimal(record["임대료_천원당㎡_월"]))
    if len(mapping) != 59 or blanks != ["도봉구"]:
        raise ValueError("seed는 상권 59개와 빈 도봉구 행 1개여야 합니다")
    seoul = [r for r in rows if r["지역"] == "서울"]
    aggregates = [r for r in seoul if r["지역__3"] in AGGREGATES]
    if (
        len(seoul) != 64
        or len(aggregates) != 5
        or {r["지역__3"] for r in aggregates} != AGGREGATES
        or any(r["지역__2"] != r["지역__3"] for r in aggregates)
    ):
        raise ValueError("서울 64행/지정 집계행 5개 구조가 다릅니다")
    selected = [r for r in seoul if r["지역__3"] not in AGGREGATES]
    names = [r["지역__3"] for r in selected]
    if len(names) != len(set(names)) or set(names) != set(mapping):
        raise ValueError("원천 상권 중복 또는 원천↔seed 상권명 불일치")
    markets, grouped = [], defaultdict(list)
    for row in selected:
        market = row["지역__3"]
        price = rent_decimal(row[quarters[quarter]])
        sq, sv = seed_values[market]
        if sq not in quarters or rent_decimal(row[quarters[sq]]) != sv:
            raise ValueError(f"seed 기준 분기의 임대료가 원천과 다릅니다: {market}")
        code = mapping[market]
        markets.append(
            {
                "market_name": market,
                "base_quarter": quarter,
                "district_code": code,
                "rent_per_sqm": price,
            }
        )
        grouped[code].append(price)
    if grouped.get("11320") or len(grouped) != 24:
        raise ValueError("도봉구 제외 24개 자치구 임대료 커버리지가 다릅니다")
    averages = []
    for district in sorted(districts, key=lambda r: r["district_code"]):
        code = district["district_code"]
        prices = grouped.get(code, [])
        count = len(prices)
        average = (sum(prices) / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if count else None
        averages.append(
            {
                "district_code": code,
                "base_quarter": quarter,
                "rent_per_sqm": average,
                "deposit_multiplier": Decimal("15.00"),
                "market_count": count,
                "confidence": "높음" if count >= 2 else "낮음" if count == 1 else "없음",
            }
        )
    summary = {
        "base_quarter": quarter,
        "available_quarters": sorted(quarters),
        "input_rows": len(rows),
        "seoul_rows": len(seoul),
        "excluded_aggregate_rows": len(aggregates),
        "excluded_aggregates": [{"name": r["지역__3"], "source_record": r[SOURCE_ROW]} for r in aggregates],
        "market_rows": len(markets),
        "district_rows": len(averages),
        "covered_districts": len(grouped),
        "confidence_counts": dict(Counter(r["confidence"] for r in averages)),
        "mapping_rows": len(mapping),
        "empty_seed_rows": len(blanks),
        "zero_rent_markets": sum(r["rent_per_sqm"] == 0 for r in markets),
        "source_records": {r["지역__3"]: r[SOURCE_ROW] for r in selected},
    }
    return {
        "commercial_market": sorted(markets, key=lambda r: r["market_name"]),
        "district_rent": averages,
    }, summary


def _schemas():
    import pyarrow as pa

    return {
        "commercial_market": pa.schema(
            [
                pa.field("market_name", pa.string(), nullable=False),
                pa.field("base_quarter", pa.string(), nullable=False),
                pa.field("district_code", pa.string(), nullable=False),
                pa.field("rent_per_sqm", pa.decimal128(8, 2), nullable=False),
            ]
        ),
        "district_rent": pa.schema(
            [
                pa.field("district_code", pa.string(), nullable=False),
                pa.field("base_quarter", pa.string(), nullable=False),
                pa.field("rent_per_sqm", pa.decimal128(8, 2)),
                pa.field("deposit_multiplier", pa.decimal128(5, 2), nullable=False),
                pa.field("market_count", pa.int32(), nullable=False),
                pa.field("confidence", pa.string(), nullable=False),
            ]
        ),
    }


def load_rent_tables(conn, report: dict, masters: dict) -> dict:
    """열린 트랜잭션에 두 표만 upsert한다. 마스터 수정·기존 행 삭제는 하지 않는다."""
    import pyarrow.parquet as pq
    from sqlalchemy import Integer, MetaData, Numeric, String, text

    if conn.dialect.name != "postgresql" or conn.dialect.driver != "psycopg" or not conn.in_transaction():
        raise ValueError("열린 PostgreSQL+psycopg 트랜잭션이 필요합니다")
    for name, columns in TABLE_COLUMNS.items():
        fields = [c for c in columns if c != "geo_code"]
        actual = [dict(r) for r in conn.execute(text(f"SELECT {', '.join(fields)} FROM {name}")).mappings()]
        expected = [{c: r[c] for c in fields} for r in masters[name]]
        if sorted(actual, key=lambda r: r[fields[0]]) != sorted(expected, key=lambda r: r[fields[0]]):
            raise ValueError(f"DB 마스터가 검증된 2단계 마스터와 다릅니다: {name}")
    metadata = MetaData()
    metadata.reflect(conn, only=list(COLUMNS))
    schemas = _schemas()
    for name, columns in COLUMNS.items():
        table = metadata.tables[name]
        if list(table.primary_key.columns.keys()) != KEYS[name] or list(table.c.keys()) != columns:
            raise ValueError(f"DB 기본키/칼럼 불일치: {name}")
        for field in schemas[name]:
            column = table.c[field.name]
            correct = (
                isinstance(column.type, Numeric)
                and column.type.precision == field.type.precision
                and column.type.scale == field.type.scale
                if field.name in {"rent_per_sqm", "deposit_multiplier"}
                else isinstance(column.type, Integer)
                if field.name == "market_count"
                else isinstance(column.type, String)
            )
            if not correct or column.nullable != field.nullable:
                raise ValueError(f"DB 타입/NULL 규칙 불일치: {name}.{field.name}")
    conn.execute(text("LOCK TABLE commercial_market, district_rent IN SHARE ROW EXCLUSIVE MODE"))
    if conn.scalar(
        text("SELECT count(*) FROM district_rent WHERE base_quarter > :q"), {"q": report["base_quarter"]}
    ):
        raise ValueError("DB의 최신 자치구 임대료를 과거 분기로 덮어쓸 수 없습니다")
    result = {"status": "completed", "tables": {}, "existing_rows_deleted": 0}
    for name, columns in COLUMNS.items():
        mark = report["tables"][name]["output"]
        path = Path(mark["path"])
        if fingerprint(path) != mark:
            raise ValueError(f"검증 후 산출물이 변경되었습니다: {name}")
        table = pq.read_table(path)
        if not table.schema.equals(schemas[name], check_metadata=False):
            raise ValueError(f"산출물 스키마 불일치: {name}")
        staging = f"ts_rent_{uuid4().hex[:12]}"
        conn.execute(text(f"CREATE TEMP TABLE {staging} (LIKE {name} INCLUDING DEFAULTS) ON COMMIT DROP"))
        with conn.connection.driver_connection.cursor() as cursor:
            with cursor.copy(f"COPY pg_temp.{staging} ({', '.join(columns)}) FROM STDIN") as copy:
                for row in table.to_pylist():
                    copy.write_row(tuple(row[c] for c in columns))
        if fingerprint(path) != mark:
            raise ValueError(f"DB 적재 중 산출물이 변경되었습니다: {name}")
        keys = KEYS[name]
        conn.execute(text(f"CREATE UNIQUE INDEX ON {staging} ({', '.join(keys)})"))
        total = conn.scalar(text(f"SELECT count(*) FROM {staging}"))
        if total != report["tables"][name]["output_rows"]:
            raise ValueError(f"스테이징 행 수 불일치: {name}")
        if name == "commercial_market":
            extra = conn.scalar(
                text(
                    f"SELECT count(*) FROM {name} t LEFT JOIN {staging} s ON "
                    "t.market_name=s.market_name AND t.base_quarter=s.base_quarter "
                    "WHERE t.base_quarter=:q AND s.market_name IS NULL"
                ),
                {"q": report["base_quarter"]},
            )
            if extra:
                raise ValueError("선택 분기에 원천 밖의 상권이 있습니다. 더미/다른 원천과 혼합하지 않습니다")
        fields = [c for c in columns if c not in keys]
        join = " AND ".join(f"t.{k}=s.{k}" for k in keys)
        different = (
            f"({', '.join('t.' + c for c in fields)}) IS DISTINCT FROM "
            f"({', '.join('s.' + c for c in fields)})"
        )
        existing = conn.scalar(text(f"SELECT count(*) FROM {staging} s JOIN {name} t ON {join}"))
        changed = conn.scalar(
            text(f"SELECT count(*) FROM {staging} s JOIN {name} t ON {join} WHERE {different}")
        )
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in fields)
        change_filter = (
            f"({', '.join(name + '.' + c for c in fields)}) IS DISTINCT FROM "
            f"({', '.join('EXCLUDED.' + c for c in fields)})"
        )
        touched = conn.execute(
            text(
                f"INSERT INTO {name} ({', '.join(columns)}) SELECT {', '.join(columns)} FROM {staging} "
                f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates} WHERE {change_filter}"
            )
        ).rowcount
        mismatch = conn.scalar(
            text(
                f"SELECT count(*) FROM {staging} s LEFT JOIN {name} t ON {join} "
                f"WHERE t.{keys[0]} IS NULL OR {different}"
            )
        )
        if mismatch or touched != total - existing + changed:
            raise ValueError(f"DB 적재 후 값/행 수 검증 실패: {name}")
        result["tables"][name] = {
            "inserted": total - existing,
            "updated": changed,
            "unchanged": existing - changed,
            "matched_input_rows": total,
            "total_rows": conn.scalar(text(f"SELECT count(*) FROM {name}")),
        }
        conn.execute(text(f"DROP TABLE {staging}"))
    return result


def process_rent(
    source_report: Path,
    master_report: Path,
    processed_dir: Path,
    mapping_path: Path,
    *,
    base_quarter: str | None = None,
    load_db: bool = False,
) -> dict:
    """단계별 실행과 DB 접속을 분리한다. 입력 검증 실패 시 결과를 공개하지 않는다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    source_report, master_report, mapping_path = (
        p.resolve() for p in (source_report, master_report, mapping_path)
    )
    processed_dir = processed_dir.resolve()
    source = json.loads(source_report.read_text(encoding="utf-8"))
    protected = [Path(source["raw_dir"]).resolve(), mapping_path.parent, SERVER_ROOT / "data/seed"]
    for path in protected:
        if path == processed_dir or path in processed_dir.parents or processed_dir in path.parents:
            raise ValueError("원본/seed와 산출물 디렉터리는 겹칠 수 없습니다")
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    output = processed_dir / "rent" / run_id
    staging = output.with_name(f".{run_id}.partial")
    report_path = processed_dir / "reports" / f"build_district_rent_{run_id}.json"
    report = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "source_run_id": source["run_id"],
        "source_report": str(source_report),
        "master_report": str(master_report),
        "mapping": str(mapping_path),
        "output_root": str(output),
        "report_path": str(report_path),
        "inputs": [fingerprint(source_report)],
        "tables": {},
        "database": {"status": "not_requested"},
        "policies": {
            "unit": "천원/㎡·월",
            "average": "자치구 내 상권별 단순 산술평균; 면적 가중치 없음",
            "rounding": "Decimal ROUND_HALF_UP, 소수 둘째 자리",
            "deposit_multiplier": "15.00, 기존 스키마 가정",
            "confidence": "상권 2개 이상 높음 / 1개 낮음 / 0개 없음",
            "missing": "도봉구 임대료 NULL 유지; passed는 백엔드가 확인불가로 계산",
            "history": "commercial_market 선택 분기 보존; district_rent 최신 분기만 저장; 과거 덮어쓰기 금지",
            "training": "임대료 시점별 피처 사용은 모델 담당 결정; 최신 값을 역사적 관측값으로 간주하지 않음",
        },
    }
    try:
        if source["status"] not in {"completed", "completed_with_issues"}:
            raise ValueError("완료된 1단계 보고서가 필요합니다")
        files = [f for f in source["files"] if f["dataset"] == "d5"]
        if len(files) != 1 or any(f.get("dataset") == "d5" for f in source["failures"]):
            raise ValueError("정상 변환된 d5 임대료 파일 하나가 필요합니다")
        masters, _ = _masters(master_report, source, report)
        entry = files[0]
        if entry["quarantined_rows"] or entry.get("rows_with_decode_issues", 0):
            raise ValueError("d5에 보류/디코딩 오류가 있습니다")
        raw_mark = entry["source"]
        if fingerprint(Path(raw_mark["path"])) != raw_mark:
            raise ValueError("1단계 이후 d5 원본이 변경되었습니다")
        path = Path(entry["output"]).resolve()
        report["inputs"].extend([raw_mark, fingerprint(path), fingerprint(mapping_path)])
        table = pq.read_table(path)
        required = {"지역", "지역__2", "지역__3", SOURCE_ROW, *_quarter_columns(entry["headers"]).values()}
        if required - set(table.column_names):
            raise ValueError("d5 Parquet 필수 칼럼 누락")
        if len(table) != entry["output_rows"] or entry["input_rows"] != entry["output_rows"]:
            raise ValueError("d5 원천 행 수 불일치")
        if any(str(table.schema.field(c).type) != "string" for c in required - {SOURCE_ROW}):
            raise ValueError("d5 원천 문자열 보존 형식이 아닙니다")
        if (table.schema.metadata or {}).get(b"source_sha256", b"").decode() != raw_mark["sha256"]:
            raise ValueError("d5 Parquet 원본 식별자 불일치")
        with mapping_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != SEED_COLUMNS:
                raise ValueError("자치구 매핑 seed 헤더 불일치")
            seed = list(reader)
        rows, counts = build_rent_rows(
            table.to_pylist(), entry["headers"], seed, masters["district"], base_quarter
        )
        report["base_quarter"] = counts["base_quarter"]
        report["counts"] = counts
        report["district_summary"] = [
            {
                **r,
                "rent_per_sqm": str(r["rent_per_sqm"]) if r["rent_per_sqm"] is not None else None,
                "deposit_multiplier": str(r["deposit_multiplier"]),
            }
            for r in rows["district_rent"]
        ]
        staging.mkdir(parents=True, exist_ok=False)
        for name, schema in _schemas().items():
            target = staging / f"{name}.parquet"
            typed = pa.Table.from_pylist(rows[name], schema=schema)
            pq.write_table(typed, target, compression="zstd")
            if not pq.read_table(target).equals(typed):
                raise ValueError(f"Parquet 저장 후 값 검증 실패: {name}")
            report["tables"][name] = {"output_rows": len(typed), "columns": COLUMNS[name]}
        for mark in report["inputs"]:
            if fingerprint(Path(mark["path"])) != mark:
                raise ValueError("처리 중 입력 파일이 변경되었습니다")
        staging.rename(output)
        for name in COLUMNS:
            report["tables"][name]["output"] = fingerprint(output / f"{name}.parquet")
        if load_db:
            from dotenv import load_dotenv
            from sqlalchemy import create_engine
            from sqlalchemy.engine import make_url

            report["database"] = {"status": "failed"}
            load_dotenv(SERVER_ROOT / ".env")
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                raise ValueError("--load-db에는 DATABASE_URL이 필요합니다")
            url = make_url(database_url)
            report["database"]["target"] = {"host": url.host, "port": url.port, "database": url.database}
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            try:
                with engine.begin() as conn:
                    result = load_rent_tables(conn, report, masters)
            finally:
                engine.dispose()
            report["database"].update(result)
        report["status"] = "completed"
    except Exception as exc:
        logger.error("5단계 실패 (%s)", type(exc).__name__)
        report["status"] = "failed"
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc) if isinstance(exc, ValueError) else "처리 오류. 로컬 검증이 필요합니다",
        }
        if staging.exists():
            report["held_artifacts"] = str(staging)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _json(report_path, report)
    return report
