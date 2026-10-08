"""데이터 파이프라인 2단계: district · dong · industry 마스터를 구축한다.

설치: python -m pip install "pyarrow>=18.0,<26.0"
실행: python -m batch.jobs.build_masters --source-report data/processed/reports/convert_raw_<run_id>.json
지도 코드 포함: 위 명령에 --geo-dir ../TownSignalFE/public/geo 추가
DB에도 적재: 위 명령에 --load-db 추가 (requirements/batch.txt 설치 및 DATABASE_URL 설정 필요)

원천 데이터는 1단계 보고서에 지정된 d1~d3 Parquet만 읽는다. 수작업 seed는 UTF-8 CSV로 읽는다.
결과는 data/processed/masters/<run_id>/와 reports/build_masters_<run_id>.json에 저장한다.
기본 실행은 파일 생성까지이며, --load-db일 때만 3개 마스터를 한 트랜잭션으로 upsert한다.
DB의 기존 행을 삭제하지 않는다. 원본·seed·1단계 산출물도 수정하지 않는다.
지도 GeoJSON은 화면의 geo_code 대응용이며, 인허가 공간조인 경계로 사용하지 않는다.
"""

import argparse
import logging
from pathlib import Path

from batch.pipeline.masters import build_masters
from batch.pipeline.raw_sources import SERVER_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source-report", type=Path, required=True, help="사용할 1단계 실행 보고서")
    parser.add_argument("--processed-dir", type=Path, default=SERVER_ROOT / "data" / "processed")
    parser.add_argument("--seed-dir", type=Path, default=SERVER_ROOT / "data" / "seed")
    parser.add_argument("--geo-dir", type=Path, help="자치구·행정동 지도 GeoJSON이 있는 폴더")
    parser.add_argument("--load-db", action="store_true", help="DATABASE_URL의 DB에 마스터 3종 upsert")
    parser.add_argument("--strict", action="store_true", help="경고가 있는 완료도 종료 코드 1")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = build_masters(
        args.source_report, args.processed_dir, args.seed_dir, args.geo_dir, load_db=args.load_db,
    )
    print(f"상태: {report['status']}")
    for name, count in report.get("counts", {}).items():
        print(f"{name}: {count:,}행")
    print(f"경고: {len(report['warnings'])}종, 오류: {len(report['failures'])}건")
    print(f"DB: {report['database']['status']}")
    print(f"보고서: {report['report_path']}")
    return int(report["status"] == "failed" or (args.strict and report["status"] != "completed"))


if __name__ == "__main__":
    raise SystemExit(main())
