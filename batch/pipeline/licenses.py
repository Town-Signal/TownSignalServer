"""4단계 인허가 변환·공간조인·적재. 기존 jobs를 고치지 않는 독립 모듈 진입점.

설치: python -m pip install "geopandas>=1.0,<2.0" "pyarrow>=18.0,<26.0"
실행: python -m batch.pipeline.licenses --source-report <1단계.json> \
      --master-report <2단계.json> --boundary-report <d6 추가변환.json>
선택: --observed-on YYYY-MM-DD (실제 원천 다운로드일), --load-db, --strict

data/seed/license_to_industry_mapping.csv: coverage 100행 + 종류·업태별 mapping 행.
결과: data/processed/licenses/<run_id>/ 및 reports/load_licenses_<run_id>.json.
모든 기간·영업상태·원문 칼럼을 enriched Parquet에 보존한다. DB용 Parquet는 인허가당
한 행이다. 같은 키의 핵심 값이 같을 때만 중복을 묶고 모든 원본 위치를 기록한다.
미분류/좌표 결측은 NULL, 잘못된 폐업일은 NULL과 오류 플래그로 보존(생존학습 제외).
개업일·식별키 오류/핵심 값이 다른 중복은 보류하고 전체 적재를 중단한다.
주소의 법정동명/법정동코드를 행정동으로 가정하지 않는다. 좌표가 없는 주소는 보존하고
미매칭으로 남긴다. 외부 지오코딩 호출은 하지 않는다. 경계 도형은 보정하지 않는다.
생존모델은 여기서 학습하지 않는다. 후속 작업은 eligible 필터와 관측 종료일을 사용해야
한다. NULL 폐업일+휴업/취소/말소를 영업 중 우측절단으로 해석하지 않는다.
DB는 검증된 동일 마스터만 허용하며 business_license만 트랜잭션으로 upsert한다.
기존 행을 삭제하지 않는다. 원본·기존 코드·seed·DB 스키마를 변경하지 않는다.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from batch.pipeline.indicators import _masters
from batch.pipeline.license_mapping import (
    SOURCES,
    normalize,
    read_mapping,
    source_type,
)
from batch.pipeline.masters import TABLE_COLUMNS
from batch.pipeline.raw_conversion import SOURCE_ROW, fingerprint
from batch.pipeline.raw_sources import SERVER_ROOT

logger = logging.getLogger(__name__)
DB_COLUMNS = ["license_id", "dong_code", "industry_code", "open_date", "close_date", "status"]
SOURCE_CRS = 5174
REQUIRED = [
    "개방자치단체코드",
    "관리번호",
    "인허가일자",
    "폐업일자",
    "영업상태코드",
    "영업상태명",
    "상세영업상태코드",
    "상세영업상태명",
    "지번주소",
    "도로명주소",
    "좌표정보(X)",
    "좌표정보(Y)",
    "최종수정일자",
    "데이터갱신일자",
    SOURCE_ROW,
]


def _json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def parse_date(value: str | None, *, required: bool = False) -> date | None:
    value = normalize(value)
    if not value and not required:
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    if re.fullmatch(r"[0-9]{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    raise ValueError(f"날짜 형식/필수 값 오류: {value!r}")


def stable_id(kind: str, authority: str, management: str) -> tuple[str, str]:
    if not re.fullmatch(r"[0-9]{7}", authority) or not management:
        raise ValueError("개방자치단체코드/관리번호 오류")
    key = json.dumps([kind, authority, management], ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(key.encode("utf-8"), digest_size=20, person=b"TownSignalLicV1").hexdigest(), key


def normalized_status(code: str, detail: str) -> str | None:
    direct = {"01": "영업/정상", "02": "휴업", "03": "폐업", "05": "제외등"}
    if code in direct:
        return direct[code]
    if code == "04":
        return next(
            (
                label
                for word, label in (
                    ("취소", "취소"),
                    ("말소", "말소"),
                    ("만료", "만료"),
                    ("정지", "정지"),
                    ("중지", "중지"),
                )
                if word in detail
            ),
            "취소등",
        )
    return None


def _schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("license_id", pa.string(), nullable=False),
            pa.field("dong_code", pa.string()),
            pa.field("industry_code", pa.string()),
            pa.field("open_date", pa.date32(), nullable=False),
            pa.field("close_date", pa.date32()),
            pa.field("status", pa.string()),
        ]
    )


def _extra_schema():
    import pyarrow as pa

    return pa.schema(
        [
            *[pa.field("ts_" + f.name, f.type) for f in _schema()],
            pa.field("ts_license_type", pa.string()),
            pa.field("ts_spatial_reason", pa.string()),
            pa.field("ts_subtype", pa.string()),
            pa.field("ts_mapping_reason", pa.string()),
            pa.field("ts_flags", pa.list_(pa.string())),
            pa.field("ts_is_canonical", pa.bool_()),
            pa.field("ts_survival_candidate", pa.bool_()),
            pa.field("ts_survival_eligible", pa.bool_()),
            pa.field("ts_observed_on", pa.date32()),
        ]
    )


def load_boundary(path: Path, masters: dict):
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    if not {"ADSTRD_CD", "ADSTRD_NM", "geometry"}.issubset(frame.columns):
        raise ValueError("행정동 경계의 코드/명칭/도형 필드가 없습니다")
    if frame.crs is None or frame.crs.to_epsg() != 5181:
        raise ValueError("확인된 행정동 경계 EPSG:5181과 다릅니다")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any() or not frame.geometry.is_valid.all():
        raise ValueError("행정동 경계에 누락·빈 도형·잘못된 도형이 있습니다")
    if not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("행정동 경계가 면 도형이 아닙니다")
    codes = frame.ADSTRD_CD
    expected = {row["dong_code"]: row["name"] for row in masters["dong"]}
    if codes.duplicated().any() or set(codes) != set(expected):
        raise ValueError("행정동 경계와 마스터 코드가 다르거나 중복입니다")
    if any(row.ADSTRD_NM != expected[row.ADSTRD_CD] for row in frame.itertuples()):
        raise ValueError("행정동 경계와 마스터 명칭이 다릅니다")
    return frame[["ADSTRD_CD", "geometry"]].copy()


def spatial_codes(frame, boundary, districts: dict) -> tuple[list, list]:
    """내부 포함을 우선 사용하고 경계선 포함은 단일 매칭만 허용한다. 최근접 배정 금지."""
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    x = pd.to_numeric(frame["좌표정보(X)"].str.strip(), errors="coerce")
    y = pd.to_numeric(frame["좌표정보(Y)"].str.strip(), errors="coerce")
    valid = np.isfinite(x) & np.isfinite(y) & x.ne(0) & y.ne(0)
    codes = [None] * len(frame)
    reasons = ["missing_or_invalid_coordinate"] * len(frame)
    if not valid.any():
        return codes, reasons
    points = gpd.GeoDataFrame(
        index=frame.index[valid], geometry=gpd.points_from_xy(x[valid], y[valid]), crs=SOURCE_CRS
    ).to_crs(boundary.crs)
    hits = gpd.sjoin(points, boundary, how="left", predicate="within")
    remaining = []
    for index, group in hits.groupby(level=0):
        matches = group.ADSTRD_CD.dropna().unique()
        if len(matches) == 1:
            codes[index], reasons[index] = str(matches[0]), "within"
        elif len(matches) > 1:
            reasons[index] = "multiple_boundaries"
        else:
            remaining.append(index)
    if remaining:
        edges = gpd.sjoin(points.loc[remaining], boundary, how="left", predicate="covered_by")
        for index, group in edges.groupby(level=0):
            matches = group.ADSTRD_CD.dropna().unique()
            if len(matches) == 1:
                codes[index], reasons[index] = str(matches[0]), "single_boundary_edge"
            else:
                reasons[index] = "multiple_boundaries" if len(matches) > 1 else "outside_boundaries"
    # 주소는 좌표의 자치구 대조에만 사용한다. 법정동을 행정동으로 가정하지 않는다.
    district_codes = {name: code for code, name in districts.items()}
    for i, code in enumerate(codes):
        if code is None:
            continue
        found = set()
        for column in ("지번주소", "도로명주소"):
            value = normalize(frame.iloc[i][column])
            match = re.match(r"^(?:서울특별시|서울시|서울)\s+(\S+구)(?:\s|$)", value)
            if match and match[1] in district_codes:
                found.add(district_codes[match[1]])
        if found and found != {code[:5]}:
            codes[i], reasons[i] = None, "address_district_conflict"
    return codes, reasons


def _select_boundary(boundary_report: Path, report: dict) -> Path:
    import pyarrow.parquet as pq

    report["inputs"].append(fingerprint(boundary_report))
    source = json.loads(boundary_report.read_text(encoding="utf-8"))
    matches = []
    for item in source["files"]:
        if item["dataset"] != "d6":
            continue
        path = Path(item["output"]).resolve()
        pf = pq.ParquetFile(path)
        if {"ADSTRD_CD", "ADSTRD_NM"}.issubset(pf.schema_arrow.names):
            if item["status"] != "completed" or pf.metadata.num_rows != item["output_rows"]:
                raise ValueError("행정동 경계 변환이 정상 완료되지 않았습니다")
            for mark in item["source"]:
                if fingerprint(Path(mark["path"])) != mark:
                    raise ValueError("행정동 경계 원본이 변환 후 변경되었습니다")
                report["inputs"].append(mark)
            report["inputs"].append(fingerprint(path))
            matches.append(path)
    if len(matches) != 1:
        raise ValueError("변환 보고서에서 유일한 행정동 경계를 찾을 수 없습니다")
    report["boundary_conversion_run_id"] = source["run_id"]
    return matches[0]


def _transform(files, boundary, masters, coverage, rules, staging, report, batch_size, observed_on):
    import pyarrow as pa
    import pyarrow.parquet as pq

    counts, flags, spatial, by_industry, subtypes, statuses = [Counter() for _ in range(6)]
    districts = {r["district_code"]: r["name"] for r in masters["district"]}
    index_path = staging / "_identity.sqlite"
    index = sqlite3.connect(index_path)
    index.execute(
        "CREATE TABLE identity (id TEXT PRIMARY KEY, identity TEXT, digest BLOB, file TEXT, record INT)"
    )
    db_path = staging / "business_license.parquet"
    enriched_dir = staging / "enriched"
    enriched_dir.mkdir()
    extra_schema = _extra_schema()
    date_min, date_max = None, None
    try:
        with (
            pq.ParquetWriter(db_path, _schema(), compression="zstd") as db_writer,
            (staging / "issues.jsonl").open("x", encoding="utf-8") as issues,
        ):
            for item in files:
                path = Path(item["output"]).resolve()
                kind = source_type(path)
                field = SOURCES[kind][0]
                before = fingerprint(path)
                report["inputs"].append(before)
                source_issue_path = Path(item["issues_file"])
                report["inputs"].append(fingerprint(source_issue_path))
                pf = pq.ParquetFile(path)
                if pf.metadata.num_rows != item["output_rows"] or item.get("quarantined_rows", 0):
                    raise ValueError(f"1단계 행 수 불일치/보류 행: {path.name}")
                if (pf.schema_arrow.metadata or {}).get(b"source_sha256", b"").decode() != item["source"][
                    "sha256"
                ]:
                    raise ValueError(f"1단계 원본 지문 불일치: {path.name}")
                if set(REQUIRED + [field]) - set(pf.schema_arrow.names):
                    raise ValueError(f"필수 인허가 칼럼 누락: {path.name}")
                if any(c.startswith("ts_") for c in pf.schema_arrow.names):
                    raise ValueError("원천에 예약된 ts_ 칼럼이 있습니다")
                if any(str(f.type) != "string" for f in pf.schema_arrow if f.name != SOURCE_ROW):
                    raise ValueError("1단계 원문 문자열 보존 형식과 다릅니다")
                schema = pa.schema(
                    list(pf.schema_arrow) + list(extra_schema), metadata=pf.schema_arrow.metadata
                )
                enriched_path = enriched_dir / path.name
                source_counts = Counter()
                with pq.ParquetWriter(enriched_path, schema, compression="zstd") as writer:
                    for batch in pf.iter_batches(batch_size=batch_size):
                        needed = REQUIRED + [field]
                        table = pa.Table.from_batches([batch])
                        rows = table.select(needed).to_pylist()
                        frame = table.select(
                            ["좌표정보(X)", "좌표정보(Y)", "지번주소", "도로명주소"]
                        ).to_pandas()
                        dongs, spatial_reasons = spatial_codes(frame, boundary, districts)
                        transformed, extra = [], []
                        for i, row in enumerate(rows):
                            counts["input_rows"] += 1
                            source_counts["input_rows"] += 1
                            problems, fatal = [], []
                            subtype = normalize(row[field])
                            rule = rules.get((kind, subtype))
                            industry = rule["industry_code"] or None if rule else None
                            mapping_reason = rule["reason"] if rule else "미등록 업태; 추정하지 않음"
                            if rule is None:
                                problems.append("unknown_subtype_rule")
                            opening, closing, lid, identity = None, None, None, None
                            try:
                                opening = parse_date(row["인허가일자"], required=True)
                            except ValueError:
                                fatal.append("invalid_open_date")
                            try:
                                closing = parse_date(row["폐업일자"])
                            except ValueError:
                                problems.append("invalid_close_date")
                            if opening and closing and closing < opening:
                                closing = None
                                problems.append("close_before_open")
                            if observed_on and (
                                (opening and opening > observed_on) or (closing and closing > observed_on)
                            ):
                                problems.append("date_after_observation")
                            try:
                                lid, identity = stable_id(
                                    kind, normalize(row["개방자치단체코드"]), normalize(row["관리번호"])
                                )
                            except ValueError:
                                fatal.append("invalid_identity")
                            status = normalized_status(
                                normalize(row["영업상태코드"]), normalize(row["상세영업상태명"])
                            )
                            if status is None:
                                problems.append("unknown_status")
                            if status == "폐업" and closing is None:
                                problems.append("closed_without_valid_close_date")
                            if status == "영업/정상" and closing is not None:
                                problems.append("active_with_close_date")
                            spatial_reason = spatial_reasons[i]
                            if dongs[i] is None:
                                problems.append(spatial_reason)
                            db_row = dict(
                                zip(
                                    DB_COLUMNS,
                                    [lid, dongs[i], industry, opening, closing, status],
                                    strict=True,
                                )
                            )
                            canonical = not fatal
                            if canonical:
                                # 주소/전화번호가 달라도 좌표·업태·상태·개폐업일·최종수정은 같아야 한다.
                                core = [
                                    identity,
                                    *list(db_row.values()),
                                    subtype,
                                    *[
                                        normalize(row[c])
                                        for c in (
                                            "좌표정보(X)",
                                            "좌표정보(Y)",
                                            "영업상태코드",
                                            "영업상태명",
                                            "상세영업상태코드",
                                            "상세영업상태명",
                                            "최종수정일자",
                                            "데이터갱신일자",
                                            "인허가일자",
                                            "폐업일자",
                                        )
                                    ],
                                ]
                                digest = hashlib.sha256(
                                    json.dumps(
                                        core, default=str, ensure_ascii=False, separators=(",", ":")
                                    ).encode()
                                ).digest()
                                existing = index.execute(
                                    "SELECT identity,digest,file,record FROM identity WHERE id=?", (lid,)
                                ).fetchone()
                                if existing:
                                    canonical = False
                                    if existing[0] != identity or existing[1] != digest:
                                        fatal.append("conflicting_duplicate_or_hash_collision")
                                    else:
                                        counts["duplicate_rows"] += 1
                                        source_counts["duplicate_rows"] += 1
                                        issues.write(
                                            json.dumps(
                                                {
                                                    "kind": "equivalent_duplicate",
                                                    "license_id": lid,
                                                    "source_file": path.name,
                                                    "record": row[SOURCE_ROW],
                                                    "canonical_file": existing[2],
                                                    "canonical_record": existing[3],
                                                },
                                                ensure_ascii=False,
                                            )
                                            + "\n"
                                        )
                                else:
                                    index.execute(
                                        "INSERT INTO identity VALUES (?,?,?,?,?)",
                                        (lid, identity, digest, path.name, row[SOURCE_ROW]),
                                    )
                            candidate = bool(
                                canonical
                                and dongs[i]
                                and industry
                                and coverage[industry]["coverage"] == "커버됨"
                                and (
                                    (status == "폐업" and closing)
                                    or (status == "영업/정상" and closing is None)
                                )
                                and not any(
                                    p in problems
                                    for p in (
                                        "invalid_close_date",
                                        "close_before_open",
                                        "date_after_observation",
                                    )
                                )
                            )
                            eligible = bool(candidate and (closing is not None or observed_on is not None))
                            if fatal:
                                counts["held_rows"] += 1
                                source_counts["held_rows"] += 1
                            if problems or fatal:
                                issues.write(
                                    json.dumps(
                                        {
                                            "source_file": path.name,
                                            "record": row[SOURCE_ROW],
                                            "license_id": lid,
                                            "flags": problems + fatal,
                                            "raw": row,
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                            if canonical:
                                transformed.append(db_row)
                                counts["output_rows"] += 1
                                source_counts["output_rows"] += 1
                                counts["industry_mapped" if industry else "industry_unclassified"] += 1
                                counts["dong_matched" if dongs[i] else "dong_unmatched"] += 1
                                counts["survival_candidates"] += int(candidate)
                                counts["survival_eligible"] += int(eligible)
                                counts["valid_close_date"] += int(closing is not None)
                                flags.update(problems)
                                spatial[spatial_reason] += 1
                                by_industry[industry or "NULL"] += 1
                                subtypes[(kind, subtype, industry or "NULL")] += 1
                                statuses[status or "NULL"] += 1
                                if opening:
                                    date_min = min(date_min, opening) if date_min else opening
                                    date_max = max(date_max, opening) if date_max else opening
                            extra.append(
                                {
                                    **{"ts_" + k: v for k, v in db_row.items()},
                                    "ts_license_type": kind,
                                    "ts_spatial_reason": spatial_reason,
                                    "ts_subtype": subtype,
                                    "ts_mapping_reason": mapping_reason,
                                    "ts_flags": problems + fatal,
                                    "ts_is_canonical": canonical,
                                    "ts_survival_candidate": candidate,
                                    "ts_survival_eligible": eligible,
                                    "ts_observed_on": observed_on,
                                }
                            )
                        extra_table = pa.Table.from_pylist(extra, schema=extra_schema)
                        full = pa.Table.from_arrays([*table.columns, *extra_table.columns], schema=schema)
                        writer.write_table(full)
                        if transformed:
                            db_writer.write_table(pa.Table.from_pylist(transformed, schema=_schema()))
                        index.commit()
                        logger.info(
                            "인허가 처리: %s %s/%s",
                            path.name,
                            source_counts["input_rows"],
                            item["output_rows"],
                        )
                if fingerprint(path) != before:
                    raise ValueError("처리 중 원천 Parquet가 변경되었습니다")
                report["sources"].append(
                    {
                        "license_type": kind,
                        **dict(source_counts),
                        "decode_nulls_preserved": item.get("decode_nulls_by_column", {}),
                        "enriched": str(enriched_path),
                    }
                )
    finally:
        index.close()
    index_path.unlink()
    report["counts"] = {
        key: counts[key]
        for key in (
            "input_rows",
            "output_rows",
            "duplicate_rows",
            "held_rows",
            "industry_mapped",
            "industry_unclassified",
            "dong_matched",
            "dong_unmatched",
            "survival_candidates",
            "survival_eligible",
            "valid_close_date",
        )
    }
    report["quality_flags"] = dict(sorted(flags.items()))
    report["spatial"] = dict(sorted(spatial.items()))
    report["rows_by_industry"] = dict(sorted(by_industry.items()))
    report["rows_by_status"] = dict(sorted(statuses.items()))
    report["subtype_mapping"] = [
        {"license_type": k, "subtype": s, "industry_code": c, "rows": n}
        for (k, s, c), n in sorted(subtypes.items())
    ]
    report["open_dates"] = {"min": str(date_min), "max": str(date_max)}
    if counts["input_rows"] != counts["output_rows"] + counts["duplicate_rows"] + counts["held_rows"]:
        raise ValueError("원천 행 보존 합계가 맞지 않습니다")


def load_license_table(conn, report, masters):
    """기존 지표/마스터를 변경하지 않는다. 동일한 마스터를 확인하고 인허가만 적재한다."""
    import pyarrow.parquet as pq
    from sqlalchemy import Date, MetaData, String, text

    if conn.dialect.name != "postgresql" or conn.dialect.driver != "psycopg" or not conn.in_transaction():
        raise ValueError("열린 PostgreSQL+psycopg 트랜잭션이 필요합니다")
    for name, columns in TABLE_COLUMNS.items():
        # 화면용 geo_code는 행정동 공간조인과 무관하다.
        fields = [c for c in columns if c != "geo_code"]
        actual = [dict(r) for r in conn.execute(text(f"SELECT {', '.join(fields)} FROM {name}")).mappings()]
        expected = [{c: r[c] for c in fields} for r in masters[name]]
        if sorted(actual, key=lambda r: r[fields[0]]) != sorted(expected, key=lambda r: r[fields[0]]):
            raise ValueError(f"DB 마스터가 검증된 2단계 마스터와 다릅니다: {name}")
    metadata = MetaData()
    metadata.reflect(conn, only=["business_license"])
    table = metadata.tables["business_license"]
    if list(table.primary_key.columns.keys()) != ["license_id"]:
        raise ValueError("business_license 기본키 불일치")
    for field in _schema():
        column = table.c[field.name]
        dtype = Date if field.name.endswith("_date") else String
        if not isinstance(column.type, dtype) or column.nullable != field.nullable:
            raise ValueError(f"DB 타입/NULL 규칙 불일치: {field.name}")
    mark = report["output"]
    path = Path(mark["path"])
    if fingerprint(path) != mark:
        raise ValueError("검증 후 인허가 산출물이 변경되었습니다")
    staging = "ts_license_" + uuid4().hex[:10]
    conn.execute(
        text(f"CREATE TEMP TABLE {staging} (LIKE business_license INCLUDING DEFAULTS) ON COMMIT DROP")
    )
    with conn.connection.driver_connection.cursor() as cursor:
        with cursor.copy(f"COPY pg_temp.{staging} ({', '.join(DB_COLUMNS)}) FROM STDIN") as copy:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000):
                for row in zip(*(batch.column(i).to_pylist() for i in range(len(DB_COLUMNS))), strict=True):
                    copy.write_row(row)
    if fingerprint(path) != mark:
        raise ValueError("DB 적재 중 인허가 산출물이 변경되었습니다")
    conn.execute(text(f"CREATE UNIQUE INDEX ON {staging} (license_id)"))
    total = conn.scalar(text(f"SELECT count(*) FROM {staging}"))
    if total != report["counts"]["output_rows"]:
        raise ValueError("스테이징 행 수 불일치")
    fields = DB_COLUMNS[1:]
    different = (
        f"({', '.join('t.' + c for c in fields)}) IS DISTINCT FROM ({', '.join('s.' + c for c in fields)})"
    )
    join = "t.license_id=s.license_id"
    existing = conn.scalar(text(f"SELECT count(*) FROM {staging} s JOIN business_license t ON {join}"))
    changed = conn.scalar(
        text(f"SELECT count(*) FROM {staging} s JOIN business_license t ON {join} WHERE {different}")
    )
    assignments = ", ".join(f"{c}=EXCLUDED.{c}" for c in fields)
    change_filter = (
        f"({', '.join('business_license.' + c for c in fields)}) IS DISTINCT FROM "
        f"({', '.join('EXCLUDED.' + c for c in fields)})"
    )
    touched = conn.execute(
        text(
            f"INSERT INTO business_license ({', '.join(DB_COLUMNS)}) "
            f"SELECT {', '.join(DB_COLUMNS)} FROM {staging} "
            f"ON CONFLICT (license_id) DO UPDATE SET {assignments} WHERE {change_filter}"
        )
    ).rowcount
    mismatch = conn.scalar(
        text(
            f"SELECT count(*) FROM {staging} s LEFT JOIN business_license t ON {join} "
            f"WHERE t.license_id IS NULL OR {different}"
        )
    )
    if mismatch or touched != total - existing + changed:
        raise ValueError("DB 적재 결과가 검증된 인허가와 다릅니다")
    result = {
        "status": "completed",
        "inserted": total - existing,
        "updated": changed,
        "unchanged": existing - changed,
        "matched_input_rows": total,
        "deleted": 0,
        "total_rows": conn.scalar(text("SELECT count(*) FROM business_license")),
    }
    conn.execute(text(f"DROP TABLE {staging}"))
    return result


def process_licenses(
    source_report: Path,
    master_report: Path,
    boundary_report: Path,
    processed_dir: Path,
    mapping_path: Path,
    *,
    observed_on: date | None = None,
    batch_size: int = 50_000,
    load_db: bool = False,
):
    import pyarrow.parquet as pq

    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다")
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    processed_dir = processed_dir.resolve()
    output = processed_dir / "licenses" / run_id
    staging = output.with_name("." + run_id + ".partial")
    report_path = processed_dir / "reports" / f"load_licenses_{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "report_path": str(report_path),
        "output_root": str(output),
        "inputs": [],
        "sources": [],
        "counts": {},
        "warnings": [],
        "database": {"status": "not_requested"},
        "observed_on": str(observed_on) if observed_on else None,
        "observation_policy": (
            "실제 수집일만 사용; 파일 수정시각/개별 최종수정일을 관측 종료일로 추정하지 않음"
        ),
        "boundary_policy": "제공된 단일 경계에 모든 역사적 점포 위치를 배정; 과거 당시 행정동 복원 아님",
        "crs": {
            "source": "EPSG:5174",
            "target": "EPSG:5181",
            "references": [
                f"https://data.seoul.go.kr/dataList/{oid}/S/1/datasetView.do" for _, oid in SOURCES.values()
            ],
            "reference_checked_on": "2026-10-08",
            "primary_coordinate_fields": ["좌표정보(X)", "좌표정보(Y)"],
            "extra_coordinates": "일반음식점 X좌표/Y좌표는 별도 원점·출처이므로 사용하지 않음",
        },
    }
    try:
        report["inputs"].append(fingerprint(source_report))
        source = json.loads(source_report.read_text(encoding="utf-8"))
        files = [f for f in source["files"] if f["dataset"] == "d4"]
        kinds = [source_type(Path(f["output"])) for f in files]
        if (
            set(kinds) != set(SOURCES)
            or len(kinds) != len(set(kinds))
            or any(f.get("dataset") == "d4" for f in source["failures"])
        ):
            raise ValueError("정상 변환된 서울시 인허가 6종이 각각 하나씩 필요합니다")
        report["source_run_id"] = source["run_id"]
        masters, _ = _masters(master_report.resolve(), source, report)
        report["inputs"].append(fingerprint(mapping_path))
        coverage, rules = read_mapping(mapping_path, masters["industry"])
        report["coverage"] = {
            "counts": dict(Counter(r["coverage"] for r in coverage.values())),
            "industries": list(coverage.values()),
            "scope": "현재 원천에서 생존 관측 가능 여부; 전수 관측/매출 커버리지 판정 아님",
        }
        boundary_path = _select_boundary(boundary_report.resolve(), report)
        boundary = load_boundary(boundary_path, masters)
        report["boundary"] = {
            "path": str(boundary_path),
            "rows": len(boundary),
            "effective_date": None,
            "codes_match_master": True,
        }
        staging.mkdir(parents=True, exist_ok=False)
        _transform(files, boundary, masters, coverage, rules, staging, report, batch_size, observed_on)
        for mark in report["inputs"]:
            if fingerprint(Path(mark["path"])) != mark:
                raise ValueError("처리 중 입력 파일이 변경되었습니다")
        if report["counts"]["held_rows"]:
            raise ValueError("개업일·식별키·핵심 중복 충돌 보류 행이 있어 전체 적재를 중단합니다")
        if (
            pq.ParquetFile(staging / "business_license.parquet").metadata.num_rows
            != report["counts"]["output_rows"]
        ):
            raise ValueError("최종 Parquet 행 수 불일치")
        staging.rename(output)
        report["output"] = fingerprint(output / "business_license.parquet")
        report["issues_file"] = str(output / "issues.jsonl")
        for item in report["sources"]:
            item["enriched"] = fingerprint(output / "enriched" / Path(item["enriched"]).name)
        for key, count in report["quality_flags"].items():
            report["warnings"].append({"kind": key, "count": count})
        if report["counts"]["industry_unclassified"]:
            report["warnings"].append(
                {"kind": "unclassified_industry", "count": report["counts"]["industry_unclassified"]}
            )
        if observed_on is None:
            report["warnings"].append(
                {
                    "kind": "observation_end_unconfirmed",
                    "note": "영업 중 점포는 보존하되 종료일 확인 전 생존학습 eligible=False",
                }
            )
        report["warnings"].append(
            {
                "kind": "boundary_effective_date_unconfirmed",
                "note": "단일 경계 기준 공간 배정이며 역사적 경계 복원은 수행하지 않음",
            }
        )
        if load_db:
            from dotenv import load_dotenv
            from sqlalchemy import create_engine

            load_dotenv(SERVER_ROOT / ".env")
            url = os.environ.get("DATABASE_URL")
            if not url:
                raise ValueError("--load-db에는 DATABASE_URL이 필요합니다")
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            try:
                with engine.begin() as conn:
                    report["database"] = load_license_table(conn, report, masters)
            finally:
                engine.dispose()
        report["status"] = "completed_with_issues" if report["warnings"] else "completed"
    except Exception as exc:
        logger.exception("4단계 실패")
        report["status"] = "failed"
        # DB 드라이버 예외에는 연결 정보/SQL 매개변수가 포함될 수 있으므로 원문을 기록하지 않는다.
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc) if isinstance(exc, ValueError) else "처리 오류. 로컬 실행 출력을 확인하세요",
        }
    report["finished_at"] = datetime.now(UTC).isoformat()
    _json(report_path, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    for arg in ("source-report", "master-report", "boundary-report"):
        parser.add_argument("--" + arg, type=Path, required=True)
    parser.add_argument(
        "--mapping", type=Path, default=SERVER_ROOT / "data/seed/license_to_industry_mapping.csv"
    )
    parser.add_argument("--processed-dir", type=Path, default=SERVER_ROOT / "data/processed")
    parser.add_argument("--observed-on", type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--load-db", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = process_licenses(
        args.source_report,
        args.master_report,
        args.boundary_report,
        args.processed_dir,
        args.mapping,
        observed_on=args.observed_on,
        batch_size=args.batch_size,
        load_db=args.load_db,
    )
    print(f"상태: {report['status']}")
    print(f"집계: {report['counts']}")
    print(f"DB: {report['database']['status']}")
    print(f"보고서: {report['report_path']}")
    return int(report["status"] == "failed" or (args.strict and report["status"] != "completed"))


if __name__ == "__main__":
    raise SystemExit(main())
