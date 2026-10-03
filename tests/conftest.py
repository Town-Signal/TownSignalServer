"""DB 테스트 공용 픽스처.

DB에는 DATABASE_URL(api.config, .env 또는 환경변수) 하나로만 붙는다. 로컬에서 Homebrew를 쓰든
docker compose를 쓰든 .env만 바꾸면 되고 테스트 코드는 그대로다.

- 로컬에서 DB에 붙지 못하면 DB 테스트는 skip한다(DB 없이도 common 테스트는 돌게).
- CI(환경변수 CI가 있으면)에서는 skip하지 않고 실패시킨다. DB 테스트가 조용히 빠지는 것을 막는다.
- 테스트마다 트랜잭션을 열고 끝나면 롤백한다. 개발 DB의 데이터는 바뀌지 않는다.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from api.db import engine


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        reason = f"DATABASE_URL로 DB에 접속하지 못했다: {exc.orig!r}"
        if os.getenv("CI"):
            pytest.fail(f"CI에서는 DB 테스트를 건너뛰지 않는다. {reason}", pytrace=False)
        pytest.skip(reason)
    return engine


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """테스트 하나 동안 쓰는 세션. 안에서 commit해도 SAVEPOINT까지만 가고, 끝나면 전부 롤백한다."""
    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
