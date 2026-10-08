"""2단계 마스터 구축. 원천은 Parquet, 수작업 seed는 CSV, 출력은 DB 스키마와 같은 칼럼이다.

d1의 행정동을 기준으로 d2·d3 코드를 대조한다. 업종은 d1·d2·100개 category seed를 함께 본다.
추정매출에 없는 업종도 마스터에 유지하며 매출 자료 존재 여부를 별도 보고한다.
알 수 없는 코드·실제 명칭 충돌·누락 category는 실패로 기록하고 마스터를 공개하지 않는다.
원천의 연도별 코드 관측 차이는 기록하되 행정동 신설·폐지로 단정하거나 값을 보간하지 않는다.
"""

import csv
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from batch.pipeline.raw_conversion import fingerprint

logger = logging.getLogger(__name__)

# 서비스의 5자리 자치구 코드 참조. 기존 마스터 규격의 코드·명칭을 사용하며,
# 매 실행마다 실제 행정동 prefix와 수작업 임대료 seed의 자치구명을 대조한다.
# data/dev의 더미 행정동·점포·업종은 입력으로 사용하지 않는다.
DISTRICT_NAMES = {
    "11110": "종로구", "11140": "중구", "11170": "용산구", "11200": "성동구",
    "11215": "광진구", "11230": "동대문구", "11260": "중랑구", "11290": "성북구",
    "11305": "강북구", "11320": "도봉구", "11350": "노원구", "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구", "11500": "강서구",
    "11530": "구로구", "11545": "금천구", "11560": "영등포구", "11590": "동작구",
    "11620": "관악구", "11650": "서초구", "11680": "강남구", "11710": "송파구",
    "11740": "강동구",
}

# 같은 코드의 d1·d2 명칭으로 확인한 표기만 허용한다. '?' 전체 치환은 하지 않는다.
DONG_ALIASES = {
    "11110615": ("종로1?2?3?4가동", "종로1·2·3·4가동"),
    "11110630": ("종로5?6가동", "종로5·6가동"),
    "11200615": ("금호2?3가동", "금호2·3가동"),
    "11260575": ("면목3?8동", "면목3·8동"),
    "11350625": ("중계2?3동", "중계2·3동"),
    "11350665": ("상계3?4동", "상계3·4동"),
    "11350695": ("상계6?7동", "상계6·7동"),
}
CATEGORIES = {"외식업", "서비스업", "소매업"}
DONG_COLUMNS = ["기준_년분기_코드", "행정동_코드", "행정동_코드_명"]
INDUSTRY_COLUMNS = ["서비스_업종_코드", "서비스_업종_코드_명"]
TABLE_COLUMNS = {
    "district": ["district_code", "name", "geo_code"],
    "dong": ["dong_code", "name", "district_code", "geo_code"],
    "industry": ["industry_code", "name", "category"],
}


def _json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _text(value: str | None, pattern: str | None = None, *, label: str, max_length: int = 40) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > max_length:
        raise ValueError(f"비어 있거나 유효하지 않은 {label}: {value!r}")
    if pattern and re.fullmatch(pattern, value, flags=re.ASCII) is None:
        raise ValueError(f"형식이 다른 {label}: {value!r}")
    return value


def _read_sources(source_report: Path, report: dict) -> dict:
    """필요한 칼럼만 읽되 모든 행의 코드·명칭·분기를 검사한다."""
    import pyarrow.parquet as pq

    source = json.loads(source_report.read_text(encoding="utf-8"))
    report["source_run_id"] = source["run_id"]
    report["source_conversion_status"] = source["status"]
    datasets = {}
    seen_paths = set()
    for code in ("d1", "d2", "d3"):
        files = [item for item in source["files"] if item["dataset"] == code]
        if not files or any(item.get("dataset") == code for item in source.get("failures", [])):
            raise ValueError(f"1단계에서 완전히 변환되지 않은 원천: {code}")
        names, industries, periods = defaultdict(set), defaultdict(set), {}
        for item in files:
            path = Path(item["output"]).resolve()
            if path.suffix != ".parquet" or path in seen_paths:
                raise ValueError(f"Parquet가 아니거나 중복으로 지정된 입력: {path}")
            seen_paths.add(path)
            mark = fingerprint(path)
            report["inputs"].append(mark)
            pf = pq.ParquetFile(path)
            if pf.metadata.num_rows != item["output_rows"] or item.get("quarantined_rows", 0):
                raise ValueError(f"1단계 행 수가 다르거나 보류한 행이 있음: {path.name}")
            metadata = pf.schema_arrow.metadata or {}
            if metadata.get(b"source_sha256", b"").decode() != item["source"]["sha256"]:
                raise ValueError(f"1단계 원본 식별자가 다름: {path.name}")
            columns = DONG_COLUMNS + (INDUSTRY_COLUMNS if code != "d3" else [])
            if set(columns) - set(pf.schema_arrow.names):
                raise ValueError(f"필수 칼럼 없음: {path.name}")
            if any(str(pf.schema_arrow.field(c).type) != "string" for c in columns):
                raise ValueError(f"1단계 문자열 보존 형식이 아님: {path.name}")
            for batch in pf.iter_batches(batch_size=50_000, columns=columns):
                values = [batch.column(i).to_pylist() for i in range(len(columns))]
                for row in zip(*values, strict=True):
                    quarter = _text(row[0], r"[0-9]{4}[1-4]", label="분기")
                    dong = _text(row[1], r"[0-9]{8}", label="행정동 코드")
                    name = _text(row[2], label="행정동명", max_length=30)
                    names[dong].add(name)
                    period = periods.setdefault(quarter, {"rows": 0, "dongs": set(), "industries": set()})
                    period["rows"] += 1
                    period["dongs"].add(dong)
                    if code != "d3":
                        industry = _text(row[3], r"CS[0-9]{6}", label="업종 코드")
                        industry_name = _text(row[4], label="업종명")
                        industries[industry].add(industry_name)
                        period["industries"].add(industry)
            logger.info("마스터 입력 확인: %s (%s행)", path.name, pf.metadata.num_rows)
        if not names:
            raise ValueError(f"마스터 입력 원천에 행정동이 없습니다: {code}")
        datasets[code] = {"names": names, "industries": industries, "periods": periods}
    return datasets


def _categories(seed_dir: Path, report: dict) -> dict:
    path = seed_dir / "industry_categories.csv"
    report["inputs"].append(fingerprint(path))
    categories = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"industry_code", "industry_name", "category"}.issubset(reader.fieldnames or []):
            raise ValueError("industry_categories.csv에 필수 칼럼이 없습니다")
        for row in reader:
            code = _text(row["industry_code"], r"CS[0-9]{6}", label="seed 업종 코드")
            name = _text(row["industry_name"], label="seed 업종명")
            category = row["category"]
            if code in categories or category not in CATEGORIES:
                raise ValueError(f"중복 seed 코드 또는 잘못된 category: {code}")
            categories[code] = {"industry_code": code, "name": name, "category": category}
    if len(categories) != 100:
        raise ValueError(f"업종 seed는 100개의 고유 코드가 필요합니다: {len(categories)}개")
    rent_seed = seed_dir / "서울_상권_자치구매핑.csv"
    if rent_seed.is_file():
        report["inputs"].append(fingerprint(rent_seed))
        with rent_seed.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if "자치구" not in (reader.fieldnames or []):
                raise ValueError("자치구 매핑 seed에 자치구 칼럼이 없습니다")
            seed_names = {row["자치구"] for row in reader}
        if seed_names != set(DISTRICT_NAMES.values()):
            raise ValueError("자치구 참조 명칭과 임대료 매핑 seed의 25개 자치구가 다릅니다")
    return categories


def _master_rows(datasets: dict, categories: dict, report: dict) -> dict:
    sales_names = datasets["d1"]["names"]
    districts = sorted({code[:5] for code in sales_names})
    if set(districts) - DISTRICT_NAMES.keys():
        raise ValueError("서울 자치구 참조에 없는 행정동 prefix가 있습니다")
    dongs = []
    for code, values in sorted(sales_names.items()):
        if len(values) != 1:
            raise ValueError(
                f"매출 원천의 동일 코드에 서로 다른 행정동명이 있습니다: {code}, {sorted(values)}"
            )
        name = next(iter(values))
        dongs.append({"dong_code": code, "name": name, "district_code": code[:5], "geo_code": None})
    corrections = []
    for dataset in ("d2", "d3"):
        names = datasets[dataset]["names"]
        if names.keys() != sales_names.keys():
            raise ValueError(
                f"{dataset} 행정동 코드가 d1과 다릅니다. "
                f"추가={sorted(names.keys() - sales_names.keys())}, "
                f"누락={sorted(sales_names.keys() - names.keys())}"
            )
        for code, values in sorted(names.items()):
            canonical = next(iter(sales_names[code]))
            for observed in sorted(values):
                if observed == canonical:
                    continue
                if dataset == "d3" and DONG_ALIASES.get(code) == (observed, canonical):
                    if datasets["d2"]["names"][code] != {canonical}:
                        raise ValueError(f"동명 보정에 필요한 d1·d2 대조가 실패했습니다: {code}")
                    corrections.append({
                        "dataset": dataset, "dong_code": code, "original": observed,
                        "canonical": canonical, "basis": "동일 코드의 d1·d2 명칭 일치 + 명시된 별칭",
                    })
                else:
                    raise ValueError(
                        f"확인되지 않은 행정동명 충돌: {dataset}, {code}, {observed}, {canonical}"
                    )
    report["name_corrections"] = corrections
    for dataset in ("d1", "d2"):
        for code, values in datasets[dataset]["industries"].items():
            if code not in categories or values != {categories[code]["name"]}:
                raise ValueError(
                    f"{dataset} 업종 코드·명칭과 category seed가 다릅니다: {code}, {sorted(values)}"
                )
    sales_codes = set(datasets["d1"]["industries"])
    store_codes = set(datasets["d2"]["industries"])
    report["industry_coverage"] = {
        "master_count": len(categories), "sales_count": len(sales_codes), "store_count": len(store_codes),
        "without_sales": [categories[code] for code in sorted(categories.keys() - sales_codes)],
        "without_store": [categories[code] for code in sorted(categories.keys() - store_codes)],
        "note": "마스터 등록은 지표·예측값의 존재를 보장하지 않음",
    }
    if report["industry_coverage"]["without_sales"]:
        report["warnings"].append({
            "kind": "industries_without_sales", "count": len(categories.keys() - sales_codes),
            "message": "매출 자료가 없는 업종도 마스터에 유지. 매출·매출 성장세를 임의 생성하지 않음",
        })
    if report["industry_coverage"]["without_store"]:
        report["warnings"].append({
            "kind": "industries_without_store", "count": len(categories.keys() - store_codes),
        })
    all_dongs = set(sales_names)
    report["observations_by_quarter"] = {}
    for dataset, values in datasets.items():
        quarters = []
        previous = set()
        for quarter, period in sorted(values["periods"].items()):
            quarters.append({
                "quarter": quarter, "rows": period["rows"], "dong_count": len(period["dongs"]),
                "industry_count": len(period["industries"]) if dataset != "d3" else None,
                "missing_dong_codes": sorted(all_dongs - period["dongs"]),
                "added_since_previous_observation": sorted(period["dongs"] - previous) if previous else [],
                "absent_since_previous_observation": sorted(previous - period["dongs"]) if previous else [],
            })
            previous = period["dongs"]
        report["observations_by_quarter"][dataset] = quarters
    report["observation_note"] = (
        "관측 코드 차이이며 행정동 신설·폐지 판정이 아님. 2024년 공간 기준 변경을 자동 결합하지 않음"
    )
    return {
        "district": [{"district_code": c, "name": DISTRICT_NAMES[c], "geo_code": None} for c in districts],
        "dong": dongs,
        "industry": [categories[code] for code in sorted(categories)],
    }


def _geo_codes(rows: dict, geo_dir: Path | None, report: dict) -> None:
    if geo_dir is None:
        report["geo_mapping"] = {"status": "not_requested", "district_matched": 0, "dong_matched": 0}
        report["warnings"].append({
            "kind": "geo_not_requested", "message": "지도 코드 미부여. --geo-dir로 지정 가능",
        })
        return
    sources = {}
    for kind, filename, width in (
        ("district", "seoul_municipalities_geo_simple.json", 5),
        ("dong", "seoul_submunicipalities_geo_simple.json", 7),
    ):
        path = geo_dir / filename
        report["inputs"].append(fingerprint(path))
        content = json.loads(path.read_text(encoding="utf-8"))
        if content.get("type") != "FeatureCollection":
            raise ValueError(f"지도 형식이 FeatureCollection이 아님: {filename}")
        features = {}
        for feature in content["features"]:
            properties = feature["properties"]
            code = _text(properties["code"], rf"[0-9]{{{width}}}", label="지도 코드")
            name = _text(properties["name"], label="지도 명칭")
            if code in features:
                raise ValueError(f"중복 지도 코드: {code}")
            features[code] = name
        sources[kind] = features
    district_by_name = defaultdict(list)
    for code, name in sources["district"].items():
        district_by_name[name].append(code)
    for district in rows["district"]:
        codes = district_by_name[district["name"]]
        if len(codes) != 1:
            raise ValueError(f"자치구 지도의 명칭 매칭이 모호하거나 없음: {district['name']}")
        district["geo_code"] = codes[0]
    dong_by_name = defaultdict(list)
    for code, name in sources["dong"].items():
        district_name = sources["district"].get(code[:5])
        if district_name is None:
            raise ValueError(f"지도 자치구를 찾을 수 없는 행정동 지도 코드: {code}")
        dong_by_name[district_name, name].append(code)
    missing = []
    for dong in rows["dong"]:
        key = (DISTRICT_NAMES[dong["district_code"]], dong["name"])
        codes = dong_by_name[key]
        if len(codes) > 1:
            raise ValueError(f"행정동 지도의 명칭 매칭이 모호함: {key}")
        dong["geo_code"] = codes[0] if codes else None
        if not codes:
            missing.append({"dong_code": dong["dong_code"], "name": dong["name"], "district": key[0]})
    used = [d["geo_code"] for d in rows["dong"] if d["geo_code"] is not None]
    if len(set(used)) != len(used):
        raise ValueError("서로 다른 서비스 행정동이 동일한 지도 코드에 연결됐습니다")
    report["geo_mapping"] = {
        "status": "completed_with_issues" if missing else "completed",
        "district_matched": len(rows["district"]), "dong_matched": len(used), "missing_dongs": missing,
        "unused_geo_dong_codes": sorted(sources["dong"].keys() - set(used)),
        "method": "자치구명 + 행정동명 정확히 일치. 서비스 코드와 지도 코드의 숫자를 잘라 연결하지 않음",
    }
    if missing:
        report["warnings"].append({"kind": "dongs_without_geo", "count": len(missing), "dongs": missing})


def load_master_rows(conn, rows: dict, *, include_geo_codes: bool = True) -> dict:
    """호출자가 트랜잭션을 연다. 삭제 없이 PK 기준으로 3개 마스터를 갱신한다."""
    from sqlalchemy import MetaData
    from sqlalchemy.dialects.postgresql import insert

    if conn.dialect.name != "postgresql":
        raise ValueError("마스터 DB 적재는 PostgreSQL만 지원합니다")
    metadata = MetaData()
    metadata.reflect(conn, only=list(TABLE_COLUMNS))
    counts = {}
    for name, columns in TABLE_COLUMNS.items():
        table = metadata.tables[name]
        if not set(columns).issubset(table.c.keys()):
            raise ValueError(f"마스터 DB 스키마가 다릅니다: {name}")
        if name != "industry" and not include_geo_codes:
            # 지도 입력을 생략했으면 이미 있는 geo_code를 NULL로 덮지 않는다.
            values = [{k: v for k, v in row.items() if k != "geo_code"} for row in rows[name]]
        else:
            values = rows[name]
        groups = defaultdict(list)
        for row in values:
            groups[tuple(row)].append(row)
        key = columns[0]
        for fields, group in groups.items():
            statement = insert(table).values(group)
            statement = statement.on_conflict_do_update(
                index_elements=[table.c[key]],
                set_={field: statement.excluded[field] for field in fields if field != key},
            )
            conn.execute(statement)
        counts[name] = len(values)
    return counts


def build_masters(
    source_report: Path, processed_dir: Path, seed_dir: Path, geo_dir: Path | None = None,
    *, load_db: bool = False,
) -> dict:
    """입력 검증 후 마스터 3종을 함께 공개한다. 각 실행은 새로운 디렉터리를 사용한다."""
    source_report = source_report.resolve()
    processed_dir, seed_dir = processed_dir.resolve(), seed_dir.resolve()
    source = json.loads(source_report.read_text(encoding="utf-8"))
    raw_dir = Path(source["raw_dir"]).resolve()
    if raw_dir == processed_dir or raw_dir in processed_dir.parents or processed_dir in raw_dir.parents:
        raise ValueError("원본과 산출물 디렉터리는 서로 겹칠 수 없습니다")
    if raw_dir == seed_dir or raw_dir in seed_dir.parents:
        raise ValueError("수작업 seed는 원본 데이터 폴더 밖에 있어야 합니다")
    if seed_dir == processed_dir or seed_dir in processed_dir.parents or processed_dir in seed_dir.parents:
        raise ValueError("수작업 seed와 산출물 디렉터리는 서로 겹칠 수 없습니다")
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    output_root = processed_dir / "masters" / run_id
    partial = output_root.with_name(f".{run_id}.partial")
    report_path = processed_dir / "reports" / f"build_masters_{run_id}.json"
    report = {
        "run_id": run_id, "started_at": started.isoformat(), "source_report": str(source_report),
        "output_root": str(output_root), "report_path": str(report_path),
        "inputs": [], "warnings": [], "failures": [], "files": {},
        "database": {"status": "not_requested"},
    }
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        report["inputs"].append(fingerprint(source_report))
        datasets = _read_sources(source_report, report)
        rows = _master_rows(datasets, _categories(seed_dir, report), report)
        _geo_codes(rows, geo_dir.resolve() if geo_dir else None, report)
        for before in report["inputs"]:
            if fingerprint(Path(before["path"])) != before:
                raise RuntimeError(f"구축 중 입력 파일이 변경되었습니다: {before['path']}")
        partial.mkdir(parents=True, exist_ok=False)
        for name, columns in TABLE_COLUMNS.items():
            schema = pa.schema([pa.field(c, pa.string(), nullable=c == "geo_code") for c in columns])
            pq.write_table(pa.Table.from_pylist(rows[name], schema=schema), partial / f"{name}.parquet",
                           compression="zstd")
        partial.rename(output_root)
        report["counts"] = {name: len(values) for name, values in rows.items()}
        report["category_counts"] = dict(sorted(Counter(row["category"] for row in rows["industry"]).items()))
        report["files"] = {name: fingerprint(output_root / f"{name}.parquet") for name in rows}
        if load_db:
            from dotenv import load_dotenv
            from sqlalchemy import create_engine

            report["database"]["status"] = "failed"
            load_dotenv()
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                raise ValueError("--load-db에는 명시적으로 설정한 DATABASE_URL이 필요합니다")
            engine = create_engine(database_url)
            try:
                with engine.begin() as conn:
                    counts = load_master_rows(conn, rows, include_geo_codes=geo_dir is not None)
            finally:
                engine.dispose()
            report["database"] = {"status": "completed", "upserted_rows": counts, "existing_rows_deleted": 0}
        report["status"] = "completed_with_issues" if report["warnings"] else "completed"
    except Exception as exc:
        logger.exception("마스터 구축 실패")
        report["failures"].append({"error_type": type(exc).__name__, "error": str(exc)})
        report["status"] = "failed"
    report["finished_at"] = datetime.now(UTC).isoformat()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _json(report_path, report)
    return report
