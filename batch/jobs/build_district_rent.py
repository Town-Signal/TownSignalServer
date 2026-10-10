"""데이터 파이프라인 5단계: 상권 임대료와 자치구 평균을 생성한다.

실행: python -m batch.jobs.build_district_rent --source-report <1단계.json> --master-report <2단계.json>
기본: d5 Parquet의 최신 분기. --base-quarter 2026Q2로 분기 지정 가능.
기본은 파일 생성까지이며 --load-db일 때만 DATABASE_URL의 PostgreSQL에 적재한다.
원본·seed·기존 마스터를 수정하지 않는다. Docker를 자동 실행하지 않는다.
결과: data/processed/rent/<run_id>/ 및 reports/build_district_rent_<run_id>.json.
commercial_market은 처리한 분기별 기록을 유지하고 district_rent는 최신 분기만 저장한다.
도봉구는 임대료 NULL, market_count=0, confidence='없음'. passed는 API에서 계산한다.
"""

import argparse
import logging
from pathlib import Path

from batch.pipeline.raw_sources import SERVER_ROOT
from batch.pipeline.rent import process_rent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--master-report", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, default=SERVER_ROOT / "data/seed/서울_상권_자치구매핑.csv")
    parser.add_argument("--processed-dir", type=Path, default=SERVER_ROOT / "data/processed")
    parser.add_argument("--base-quarter", help="YYYYQ1～YYYYQ4. 생략하면 원천의 최신 분기")
    parser.add_argument("--load-db", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = process_rent(
        args.source_report,
        args.master_report,
        args.processed_dir,
        args.mapping,
        base_quarter=args.base_quarter,
        load_db=args.load_db,
    )
    print(f"상태: {report['status']}")
    print(f"기준 분기: {report.get('base_quarter')}")
    print(f"집계: {report.get('counts', {})}")
    print(f"DB: {report['database']['status']}")
    print(f"보고서: {report['report_path']}")
    return int(report["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
