"""batch.pipeline.cleanup — 생성 후 30일이 지난 recommendation 정리(명세 4.6 run_weekly ③)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from batch.pipeline.cleanup import delete_expired_recommendations

NOW = datetime(2026, 10, 6, 3, 0, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.fixture
def conn(dummy_db):
    """더미가 적재된 연결의 테스트 전용 SAVEPOINT. 끝나면 되돌린다."""
    savepoint = dummy_db.begin_nested()
    dummy_db.execute(text("DELETE FROM recommendation"))
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def add_rec(conn, created_at, label: str):
    return conn.execute(
        text(
            "INSERT INTO recommendation (input_condition, calculated_budgets, created_at) "
            "VALUES (CAST(:c AS jsonb), '{}', :t) RETURNING rec_id"
        ),
        {"c": f'{{"label": "{label}"}}', "t": created_at},
    ).scalar_one()


def labels(conn) -> set[str]:
    return set(conn.execute(text("SELECT input_condition->>'label' FROM recommendation")).scalars())


def test_expired_boundaries(conn):
    add_rec(conn, NOW - timedelta(days=29), "29일")
    add_rec(conn, NOW - timedelta(days=30), "정확히 30일")
    add_rec(conn, NOW - timedelta(days=30, seconds=1), "30일 + 1초")
    add_rec(conn, NOW - timedelta(days=31), "31일")
    assert delete_expired_recommendations(conn, now=NOW) == 2
    assert labels(conn) == {"29일", "정확히 30일"}  # 30일 '초과'만 지운다(가정)


def test_cascade_deletes_rec_items(conn):
    old = add_rec(conn, NOW - timedelta(days=40), "old")
    new = add_rec(conn, NOW - timedelta(days=1), "new")
    for rec_id in (old, new):
        conn.execute(
            text("INSERT INTO rec_item (rec_id, rank_no, dong_code) VALUES (:r, 1, '11620901')"),
            {"r": rec_id},
        )
    delete_expired_recommendations(conn, now=NOW)
    remaining = set(conn.execute(text("SELECT rec_id FROM rec_item")).scalars())
    assert remaining == {new}


def test_default_now_uses_db_clock(conn):
    db_now = conn.execute(text("SELECT now()")).scalar_one()
    add_rec(conn, db_now - timedelta(days=40), "old")
    add_rec(conn, db_now - timedelta(days=1), "recent")
    assert delete_expired_recommendations(conn) == 1
    assert labels(conn) == {"recent"}
