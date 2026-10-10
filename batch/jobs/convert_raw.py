"""데이터 파이프라인 1단계. TownSignalServer에서 실행하며 DB를 사용하지 않는다.

설치: python -m pip install "pandas>=2.2,<3.0" "pyarrow>=18.0,<26.0" "geopandas>=1.0,<2.0"
전체: python -m batch.jobs.convert_raw
선택: python -m batch.jobs.convert_raw --datasets d1 d2 d3
오류를 종료 코드에 반영: python -m batch.jobs.convert_raw --strict

원본: data/raw/d1_sales_est ~ d6_map (읽기 전용).
결과: data/processed/sources/<run_id>/, reports/convert_raw_<run_id>.json.
원천 CSV의 모든 칼럼·기간·영업상태를 보존한다. d5 다중 헤더와 d6 CRS도 보존한다.
디코딩 문제는 셀을 NULL로 표시하고 원본 바이트를 issues.jsonl에 남긴다.
strict 옵션은 경고가 있는 완료 결과도 종료 코드 1로 반환한다(결과/보고서는 보존).
"""

import argparse
import logging
from pathlib import Path

from batch.pipeline.raw_conversion import convert_raw
from batch.pipeline.raw_sources import SERVER_ROOT, SOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--datasets", nargs="+", choices=[source.code for source in SOURCES])
    parser.add_argument("--raw-dir", type=Path, default=SERVER_ROOT / "data" / "raw")
    parser.add_argument("--processed-dir", type=Path, default=SERVER_ROOT / "data" / "processed")
    parser.add_argument("--chunk-size", type=int, default=50_000)
    parser.add_argument("--strict", action="store_true", help="경고가 있는 결과도 종료 코드 1")
    args = parser.parse_args(argv)
    if args.chunk_size < 1:
        parser.error("--chunk-size는 1 이상이어야 합니다")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = convert_raw(args.raw_dir, args.processed_dir, args.datasets, args.chunk_size)
    print(f"상태: {report['status']}")
    print(f"변환 파일: {len(report['files'])}, 실패 파일/원천: {len(report['failures'])}")
    print(f"입력 {report['input_rows']:,}행 → 출력 {report['output_rows']:,}행")
    print(f"행 전체 보류: {report['quarantined_rows']:,}행")
    print(f"산출물: {report['output_root']}")
    print(f"보고서: {report['report_path']}")
    return int(report["status"] == "failed" or (args.strict and report["status"] != "completed"))


if __name__ == "__main__":
    raise SystemExit(main())
