"""로컬 DB에 더미 데이터를 적재한다 — 실제 데이터 아님, 로컬 개발 · 테스트 전용.

실행: python -m batch.jobs.load_dev_fixtures [--force]

- 환경변수 APP_ENV가 **명시적으로** "local"이고 DB 호스트가 로컬
  (localhost · 127.0.0.1 · ::1 · db)일 때만 돈다.
  APP_ENV 기본값에 기대지 않는다. 운영 EC2도 compose 안에서는 DB 호스트가 "db"라 호스트만으로는 못 막는다.
- 한 트랜잭션에서 15개 표를 비우고(TRUNCATE … RESTART IDENTITY) 다시 넣는다. 몇 번을 돌려도 결과가 같고,
  중간에 실패하면 롤백되어 이전 상태가 남는다.
- 더미 표식이 없는 행(dong_code 6번째 자리가 9가 아닌 행정동, 이름이 '[더미]'로 시작하지 않는 지원사업)이
  있으면 지우지 않고 거부한다. --force로만 무시한다.
운영 적재 작업(build_support_program 등)과 코드를 섞지 않는다.
"""

import argparse
import logging
import os
import sys

from sqlalchemy import Connection, MetaData, create_engine, func, select, text
from sqlalchemy.engine import URL, make_url

from batch import config
from batch.dev.fixtures import Fixtures, build

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}

# 비우는 표(전부). recommendation · rec_item(로컬에서 API로 만든 추천 기록)과 business_license도 비운다
ALL_TABLES = (
    "rec_item", "recommendation", "summary_cache", "prediction", "program_verification", "support_program",
    "business_license", "population_quarterly", "store_quarterly", "sales_quarterly", "district_rent",
    "commercial_market", "dong", "industry", "district",
)
# 넣는 순서(FK 순서)
INSERT_ORDER = (
    "district", "dong", "industry", "commercial_market", "district_rent", "support_program",
    "program_verification", "sales_quarterly", "store_quarterly", "population_quarterly", "prediction",
    "summary_cache",
)


class DevFixtureError(RuntimeError):
    """로컬 더미 적재를 거부한 이유."""


def check_target(url: str | URL, app_env: str | None) -> None:
    """로컬 DB가 아니면 거부한다. app_env는 환경변수 값 그대로(없으면 None) 넘긴다."""
    if app_env != "local":
        raise DevFixtureError(
            f"APP_ENV가 'local'로 설정돼 있지 않아 거부했다(현재: {app_env!r}). "
            "로컬에서만 .env에 APP_ENV=local을 두고 실행한다."
        )
    host = make_url(url).host
    if host not in LOCAL_HOSTS:
        raise DevFixtureError(f"로컬 DB가 아니라 거부했다(host={host!r}).")


def _non_dummy_rows(conn: Connection, tables: dict) -> dict[str, int]:
    dong, program = tables["dong"], tables["support_program"]
    found = {
        "dong": conn.scalar(
            select(func.count()).select_from(dong).where(func.substr(dong.c.dong_code, 6, 1) != "9")
        ),
        "support_program": conn.scalar(
            select(func.count()).select_from(program).where(~program.c.name.startswith("[더미"))
        ),
    }
    return {name: n for name, n in found.items() if n}


def load(
    conn: Connection, *, app_env: str | None, force: bool = False, fixtures: Fixtures | None = None
) -> dict:
    """더미를 다시 적재하고 표별 행 수를 돌려준다. 트랜잭션은 호출하는 쪽이 연다."""
    check_target(conn.engine.url, app_env)
    metadata = MetaData()
    metadata.reflect(conn, only=ALL_TABLES)
    tables = metadata.tables

    found = _non_dummy_rows(conn, tables)
    if found and not force:
        raise DevFixtureError(f"더미가 아닌 데이터가 있어 지우지 않았다: {found}. 지워도 되면 --force.")

    conn.execute(text(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE"))

    fx = fixtures or build()
    program_ids: dict[str, int] = {}
    for name in INSERT_ORDER:
        rows = fx.tables[name]
        if name == "support_program":  # 한 행씩 넣어 program_id를 순서대로 1부터 받는다
            program = tables[name]
            for row in rows:
                values = {k: v for k, v in row.items() if not k.startswith("_")}
                program_ids[row["_key"]] = conn.scalar(
                    program.insert().values(values).returning(program.c.program_id)
                )
            continue
        if name == "program_verification":
            rows = [
                {
                    **{k: v for k, v in r.items() if k != "_program_key"},
                    "program_id": program_ids[r["_program_key"]],
                }
                for r in rows
            ]
        if rows:
            conn.execute(tables[name].insert(), rows)

    return {name: conn.scalar(select(func.count()).select_from(tables[name])) for name in ALL_TABLES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="로컬 DB에 더미 데이터를 다시 적재한다(실제 데이터 아님).")
    parser.add_argument("--force", action="store_true", help="더미가 아닌 행이 있어도 비우고 적재한다")
    args = parser.parse_args(argv)

    app_env = os.environ.get("APP_ENV")  # 기본값을 쓰지 않는다 — 비어 있으면 거부
    try:
        check_target(config.DATABASE_URL, app_env)
        engine = create_engine(config.DATABASE_URL)
        with engine.begin() as conn:
            counts = load(conn, app_env=app_env, force=args.force)
    except DevFixtureError as exc:
        logger.error("%s", exc)
        return 1
    for name in INSERT_ORDER + ("business_license", "recommendation", "rec_item"):
        logger.info("%-22s %6d", name, counts[name])
    logger.info("더미 데이터 적재 완료(실제 데이터 아님)")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
