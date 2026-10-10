"""데이터 파이프라인 3단계: 매출·점포·인구를 검증하고 DB 지표 형태로 저장한다.

설치: python -m pip install "pyarrow>=18.0,<26.0"
실행: python -m batch.jobs.load_indicators --source-report <1단계.json> --master-report <2단계.json>
DB에도 적재: 위 명령에 --load-db 추가. SQLAlchemy·psycopg·python-dotenv 및 DATABASE_URL 필요.

원천 CSV를 재파싱하지 않는다. 수작업 마스터와 원천 Parquet도 수정하지 않는다.
결과: data/processed/indicators/<run_id>/ 및 reports/load_indicators_<run_id>.json
store_count는 프랜차이즈를 포함한 유사_업종_점포_수다. 원천의 합계 관계를 매 행 검증한다.
알 수 없는 코드·필수 결측·형식/범위 오류·중복 키는 보류 기록하고 DB 적재를 중단한다.
기본은 파일 생성까지. --load-db이면 마스터와 지표를 한 트랜잭션으로 upsert하며 삭제하지 않는다.
현재 마스터 밖의 행정동/업종이 이미 있는 DB는 적재를 거부한다. 개발용 더미와 혼합하지 않는다.
인허가는 4단계, 성장세와 모델 학습은 후속 단계에서 수행한다.
"""

import argparse
import logging
from pathlib import Path

from batch.pipeline.indicators import process_indicators
from batch.pipeline.raw_sources import SERVER_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--master-report", type=Path, required=True)
    parser.add_argument("--processed-dir", type=Path, default=SERVER_ROOT / "data" / "processed")
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--load-db", action="store_true")
    parser.add_argument("--strict", action="store_true", help="경고가 있는 완료도 종료 코드 1")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size는 1 이상이어야 합니다")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = process_indicators(
        args.source_report,
        args.master_report,
        args.processed_dir,
        batch_size=args.batch_size,
        load_db=args.load_db,
    )
    print(f"상태: {report['status']}")
    for name, result in report["tables"].items():
        print(
            f"{name}: 입력 {result['input_rows']:,} → 출력 {result['output_rows']:,}, "
            f"보류 {result['held_rows']:,}"
        )
    print(f"DB: {report['database']['status']}")
    print(f"보고서: {report['report_path']}")
    return int(report["status"] == "failed" or (args.strict and report["status"] != "completed"))


if __name__ == "__main__":
    raise SystemExit(main())
