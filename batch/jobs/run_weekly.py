"""주간 배치 진입점 — EC2 cron이 주 1회 배치 이미지로 실행한다(전체 명세 4.6 · 12.1).

실행: python -m batch.jobs.run_weekly
      docker compose --profile batch run --rm batch batch.jobs.run_weekly   # 로컬
cron(가정): 매주 월요일 03:00 KST.

단계(각각 독립 실행, 단계마다 성공 · 실패와 행 수를 로그로 남기고 하나라도 실패하면 종료 코드 1)
1. 지원사업 수집 · 구조화 — build_support_program.main()(지원사업 담당이 구현 중, 지금은 미구현 실패로 기록)
2. 생성 후 30일이 지난 recommendation 삭제(rec_item은 CASCADE)
3. support_program · program_verification 백업 — pg_dump 명령과 지울 옛 백업 목록을 조립해 로그로 남긴다
   (실행은 아직 안 함, 배치 이미지에 postgresql-client 추가 후 연결)
학습 · 예측 · 요약은 여기서 실행하지 않는다(팀원 PC에서 기준 분기가 바뀔 때, 판정 38).

TODO(가정): APP_ENV 가드는 두지 않는다. 이 작업은 운영 EC2에서 도는 것이 정상이고, 명세 12.3에서
APP_ENV는 batch에 주입하지 않는 환경변수다.
"""

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, create_engine

from batch import config
from batch.jobs import build_support_program
from batch.pipeline.backup import backups_to_remove, build_pg_dump_command
from batch.pipeline.cleanup import delete_expired_recommendations

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    detail: str


def _step(name: str, action: Callable[[], str]) -> StepResult:
    try:
        detail = action()
    except NotImplementedError as exc:
        return StepResult(name, False, f"미구현 — {exc}")
    except Exception as exc:  # 한 단계가 실패해도 다음 단계는 계속한다
        logger.exception("%s 실패", name)
        return StepResult(name, False, f"{type(exc).__name__}: {exc}")
    return StepResult(name, True, detail)


def _collect_programs() -> str:
    build_support_program.main()
    return "완료"


def _cleanup(conn: Connection, now: datetime | None) -> str:
    return f"{delete_expired_recommendations(conn, now)}건 삭제"


def _backup(now: datetime, backup_dir: str) -> str:
    argv, env = build_pg_dump_command(config.DATABASE_URL, backup_dir, now)
    existing = os.listdir(backup_dir) if os.path.isdir(backup_dir) else []
    stale = backups_to_remove(existing)
    # 비밀번호는 env(PGPASSWORD)에만 있고 argv에는 없다 → 로그에 남겨도 된다
    return f"명령 조립만(실행 안 함): {' '.join(argv)} / 지울 옛 백업: {stale or '없음'}"


def run(conn: Connection, now: datetime | None = None, backup_dir: str | None = None) -> list[StepResult]:
    """세 단계를 차례로 실행하고 결과를 돌려준다. now는 테스트에서 고정한다(없으면 DB · 현재 시각)."""
    backup_now = now or datetime.now(KST)
    return [
        _step("지원사업 수집 · 구조화", _collect_programs),
        _step("30일 지난 추천 정리", lambda: _cleanup(conn, now)),
        _step("지원사업표 백업", lambda: _backup(backup_now, backup_dir or config.BACKUP_DIR)),
    ]


def main() -> int:
    engine = create_engine(config.DATABASE_URL)
    with engine.connect() as conn:
        results = run(conn)
    for result in results:
        logger.info("[%s] %s — %s", "성공" if result.ok else "실패", result.name, result.detail)
    failed = [r for r in results if not r.ok]
    logger.info("주간 배치 종료: 성공 %d · 실패 %d", len(results) - len(failed), len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
