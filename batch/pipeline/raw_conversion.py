"""1단계: 원본 CSV/상권 구획도를 Parquet로 변환한다. DB에는 접속하지 않는다.

CSV 값은 문자열로 보존한다(코드, 날짜, 빈 문자열, 공백 포함). 칼럼 매핑과 수치 변환은
후속 단계에서 한다. 디코딩 불가 셀은 NULL로 표시하고 원래 바이트를 JSONL에 보존한다.
너비가 잘못된 행은 JSONL에 보류한다. 원본은 읽기만 하고 결과는 실행별 새 디렉터리에 쓴다.
pyarrow/geopandas는 함수 안에서 import해 API/CI의 모듈 import에 영향을 주지 않는다.
"""

import base64
import csv
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from batch.pipeline.raw_sources import RawSource, select_sources

logger = logging.getLogger(__name__)
SOURCE_ROW = "__source_record"
INVALID_BYTES = re.compile(r"[\udc80-\udcff]")


def fingerprint(path: Path) -> dict:
    """원본 크기와 SHA256. 파일 수정시각을 인허가 관측 종료일로 해석하지 않는다."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _has_invalid_bytes(value: str) -> bool:
    return INVALID_BYTES.search(value) is not None


def _columns(header: list[str]) -> list[str]:
    """중복 칼럼명을 위치로 구분한다. 원래 헤더는 보고서에 그대로 보존한다."""
    names: list[str] = []
    for index, value in enumerate(header, start=1):
        base = value.lstrip("\ufeff") or f"column_{index}"
        name = base
        suffix = 2
        while name in names or name == SOURCE_ROW:
            name = f"{base}__{suffix}"
            suffix += 1
        names.append(name)
    return names


def convert_csv(source: Path, destination: Path, spec: RawSource, chunk_size: int = 50_000) -> dict:
    """CSV 하나를 행 그룹 단위로 저장한다. 실패한 partial은 완성 파일로 공개하지 않는다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if chunk_size < 1:
        raise ValueError("chunk_size는 1 이상이어야 합니다")
    if destination.exists():
        raise FileExistsError(destination)
    before = fingerprint(source)
    partial = destination.with_suffix(".partial.parquet")
    issues_path = destination.with_suffix(".issues.jsonl")
    counts: Counter = Counter()
    issue_counts: Counter = Counter()
    null_counts: Counter = Counter()
    writer = None
    with source.open(encoding=spec.encoding, errors="surrogateescape", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        headers = [next(reader) for _ in range(spec.header_rows)]
        if not headers[0] or any(_has_invalid_bytes(v) for row in headers for v in row):
            raise ValueError("헤더가 비어 있거나 디코딩할 수 없습니다")
        if any(len(row) != len(headers[0]) for row in headers):
            raise ValueError("다중 헤더의 칼럼 수가 일치하지 않습니다")
        columns = _columns(headers[0])
        schema = pa.schema(
            [pa.field(name, pa.string()) for name in columns] + [pa.field(SOURCE_ROW, pa.int64())],
            metadata={
                b"source_sha256": before["sha256"].encode(),
                b"source_encoding": spec.encoding.encode(),
                b"source_headers": json.dumps(headers, ensure_ascii=False).encode("utf-8"),
            },
        )
        buffer = {name: [] for name in schema.names}
        with issues_path.open("x", encoding="utf-8") as issues:
            try:
                writer = pq.ParquetWriter(partial, schema, compression="zstd")
                for record, values in enumerate(reader, start=1):
                    counts["input_rows"] += 1
                    context = {"record": record, "physical_end_line": reader.line_num}
                    if len(values) != len(columns):
                        issue_counts["row_width"] += 1
                        counts["quarantined_rows"] += 1
                        issues.write(json.dumps({
                            **context, "reason": "row_width", "expected": len(columns),
                            "actual": len(values), "values": values,
                        }, ensure_ascii=True) + "\n")
                        continue
                    invalid = []
                    for name, value in zip(columns, values, strict=True):
                        if _has_invalid_bytes(value):
                            invalid.append({
                                "column": name,
                                "raw_base64": base64.b64encode(
                                    value.encode(spec.encoding, errors="surrogateescape")
                                ).decode("ascii"),
                            })
                            value = None
                            null_counts[name] += 1
                        buffer[name].append(value)
                    if invalid:
                        issue_counts["decode_cells"] += len(invalid)
                        counts["rows_with_decode_issues"] += 1
                        issues.write(json.dumps({
                            **context, "reason": "decode_cells", "cells": invalid,
                        }, ensure_ascii=True) + "\n")
                    buffer[SOURCE_ROW].append(record)
                    counts["output_rows"] += 1
                    if len(buffer[SOURCE_ROW]) >= chunk_size:
                        writer.write_table(pa.Table.from_pydict(buffer, schema=schema))
                        buffer = {name: [] for name in schema.names}
                if buffer[SOURCE_ROW]:
                    writer.write_table(pa.Table.from_pydict(buffer, schema=schema))
            finally:
                if writer is not None:
                    writer.close()
    after = fingerprint(source)
    if before != after:
        raise RuntimeError("처리 중 원본이 변경되었습니다")
    partial.rename(destination)
    return {
        "source": before, "output": str(destination), "encoding": spec.encoding,
        "headers": headers, "columns": schema.names,
        **{key: counts[key] for key in (
            "input_rows", "output_rows", "quarantined_rows", "rows_with_decode_issues",
        )},
        "issue_counts": dict(issue_counts), "decode_nulls_by_column": dict(null_counts),
        "issues_file": str(issues_path),
        "status": "completed_with_issues" if issue_counts else "completed",
    }


def convert_shapefile(source: Path, destination: Path, spec: RawSource) -> dict:
    """상권 구획도의 속성·geometry·원래 CRS를 GeoParquet로 보존한다. 공간조인은 하지 않는다."""
    import geopandas as gpd

    if destination.exists():
        raise FileExistsError(destination)
    for suffix in (".shp", ".shx", ".dbf", ".prj"):
        if not source.with_suffix(suffix).is_file():
            raise FileNotFoundError(source.with_suffix(suffix))
    components = [source.with_suffix(suffix) for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg")]
    before = [fingerprint(path) for path in components if path.exists()]
    frame = gpd.read_file(source, encoding=spec.encoding)
    if frame.crs is None:
        raise ValueError("상권 구획도의 좌표계를 확인할 수 없습니다")
    if SOURCE_ROW in frame.columns:
        raise ValueError(f"예약 칼럼이 이미 있습니다: {SOURCE_ROW}")
    frame[SOURCE_ROW] = range(1, len(frame) + 1)
    nulls = int(frame.geometry.isna().sum())
    invalid = int((frame.geometry.notna() & ~frame.geometry.is_valid).sum())
    partial = destination.with_suffix(".partial.parquet")
    frame.to_parquet(partial, index=False, compression="zstd", schema_version="1.0.0")
    if before != [fingerprint(path) for path in components if path.exists()]:
        raise RuntimeError("처리 중 상권 구획도 원본이 변경되었습니다")
    partial.rename(destination)
    return {
        "source": before, "output": str(destination), "encoding": spec.encoding,
        "columns": list(frame.columns), "input_rows": len(frame), "output_rows": len(frame),
        "quarantined_rows": 0, "crs": frame.crs.to_wkt(),
        "null_geometries": nulls, "invalid_geometries": invalid,
        "status": "completed_with_issues" if nulls or invalid else "completed",
    }


def convert_raw(
    raw_dir: Path, processed_dir: Path, codes: list[str] | None = None, chunk_size: int = 50_000,
) -> dict:
    """원천별로 독립 변환하고 실행별 보고서를 남긴다. 실패한 원천도 보고서에서 확인 가능하다."""
    raw_dir, processed_dir = raw_dir.resolve(), processed_dir.resolve()
    if raw_dir == processed_dir or raw_dir in processed_dir.parents or processed_dir in raw_dir.parents:
        raise ValueError("원본과 산출물 디렉터리는 서로 겹칠 수 없습니다")
    if chunk_size < 1:
        raise ValueError("chunk_size는 1 이상이어야 합니다")
    specs = select_sources(codes)
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    output_root = processed_dir / "sources" / run_id
    report_path = processed_dir / "reports" / f"convert_raw_{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id, "started_at": started.isoformat(), "raw_dir": str(raw_dir),
        "output_root": str(output_root), "report_path": str(report_path), "files": [],
        "failures": [], "ignored_files": [],
    }
    for spec in specs:
        directory = raw_dir / spec.directory
        suffix = ".csv" if spec.kind == "csv" else ".shp"
        paths = sorted(directory.rglob(f"*{suffix}")) if directory.is_dir() else []
        if not paths:
            report["failures"].append({"dataset": spec.code, "error": f"원본 파일 없음: {directory}"})
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                report["ignored_files"].append({"path": str(path), "reason": "상권 참고 이미지"})
        for source in paths:
            destination = output_root / spec.directory / source.relative_to(directory).with_suffix(".parquet")
            destination.parent.mkdir(parents=True, exist_ok=True)
            logger.info("변환 시작: %s", source.name)
            try:
                if spec.kind == "csv":
                    result = convert_csv(source, destination, spec, chunk_size)
                else:
                    result = convert_shapefile(source, destination, spec)
                result["dataset"] = spec.code
                report["files"].append(result)
                logger.info("변환 완료: %s (%s행, %s)", source.name, result["output_rows"], result["status"])
            except Exception as exc:
                logger.exception("변환 실패: %s", source.name)
                report["failures"].append({
                    "dataset": spec.code, "source": str(source),
                    "error_type": type(exc).__name__, "error": str(exc),
                })
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "failed" if report["failures"] else "completed_with_issues"
        if any(item["status"] == "completed_with_issues" for item in report["files"]) else "completed"
    )
    report["input_rows"] = sum(item["input_rows"] for item in report["files"])
    report["output_rows"] = sum(item["output_rows"] for item in report["files"])
    report["quarantined_rows"] = sum(item["quarantined_rows"] for item in report["files"])
    _write_json(report_path, report)
    return report
