"""더미 적재 작업 검사. DB 테스트는 conftest의 db_session(테스트 후 롤백) 안에서 돌린다."""

import pytest
from sqlalchemy import text

from batch import config
from batch.jobs import load_dev_fixtures
from batch.jobs.load_dev_fixtures import ALL_TABLES, DevFixtureError, check_target, load
from tests.test_dev_fixtures import EXPECTED_COUNTS

LOCAL_URL = "postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal"
# 운영 EC2의 compose 안에서도 DB 호스트는 db다 — 호스트 검사만으로는 운영을 못 막는다
COMPOSE_URL = "postgresql+psycopg://townsignal:secret@db:5432/townsignal"
REMOTE_URL = "postgresql+psycopg://townsignal:secret@10.0.0.5:5432/townsignal"

_CHECKSUM = (
    "SELECT md5(coalesce(string_agg(x, '|' ORDER BY x), '')) FROM ("
    "  SELECT (to_jsonb(t) - 'computed_at' - 'generated_at' - 'checked_at' - 'created_at')::text AS x"
    "  FROM {table} t) s"
)


# ── 가드 (DB 없음) ───────────────────────────────────────────


@pytest.mark.parametrize("app_env", [None, "", "prod", "production", "LOCAL"])
@pytest.mark.parametrize("url", [LOCAL_URL, COMPOSE_URL])
def test_guard_requires_explicit_local_app_env(url, app_env):
    with pytest.raises(DevFixtureError, match="APP_ENV"):
        check_target(url, app_env)


def test_guard_rejects_remote_host_even_when_local():
    with pytest.raises(DevFixtureError, match="로컬 DB가 아니라"):
        check_target(REMOTE_URL, "local")


@pytest.mark.parametrize("url", [LOCAL_URL, COMPOSE_URL, LOCAL_URL.replace("localhost", "127.0.0.1")])
def test_guard_accepts_local_targets(url):
    check_target(url, "local")


def test_main_refuses_on_ec2_like_env_without_app_env(monkeypatch, caplog):
    """운영 EC2처럼 DB 호스트가 db이고 APP_ENV가 비어 있으면, 설정 기본값('local')과 관계없이 거부한다."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config, "DATABASE_URL", COMPOSE_URL)
    monkeypatch.setattr(config, "APP_ENV", "local")  # 설정 모듈의 기본값이 local이어도 쓰지 않는다
    assert load_dev_fixtures.main([]) == 1
    assert "APP_ENV" in caplog.text


@pytest.mark.parametrize("app_env", ["prod", ""])
def test_main_refuses_non_local_app_env(monkeypatch, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setattr(config, "DATABASE_URL", COMPOSE_URL)
    assert load_dev_fixtures.main([]) == 1


# ── 적재 (DB, 롤백) ──────────────────────────────────────────


def _checksums(conn) -> dict[str, str]:
    return {name: conn.scalar(text(_CHECKSUM.format(table=name))) for name in ALL_TABLES}


def test_load_twice_gives_same_result(db_session):
    conn = db_session.connection()
    first = load(conn, app_env="local", force=True)
    first_sums = _checksums(conn)
    second = load(conn, app_env="local")  # 이미 더미뿐이라 force 없이도 다시 적재된다
    assert first == second
    assert _checksums(conn) == first_sums
    assert {name: second[name] for name in EXPECTED_COUNTS} == EXPECTED_COUNTS
    assert second["business_license"] == second["recommendation"] == second["rec_item"] == 0
    ids = conn.execute(text("SELECT program_id FROM support_program ORDER BY program_id")).scalars().all()
    assert ids == list(range(1, 12))


def test_load_clears_local_recommendations(db_session):
    conn = db_session.connection()
    load(conn, app_env="local", force=True)
    conn.execute(text("INSERT INTO recommendation (input_condition, calculated_budgets) VALUES ('{}', '{}')"))
    counts = load(conn, app_env="local")
    assert counts["recommendation"] == 0


def test_load_refuses_without_explicit_local(db_session):
    with pytest.raises(DevFixtureError, match="APP_ENV"):
        load(db_session.connection(), app_env=None)


def test_load_refuses_when_non_dummy_rows_exist(db_session):
    conn = db_session.connection()
    load(conn, app_env="local", force=True)
    conn.execute(text("INSERT INTO district (district_code, name) VALUES ('99999', '시험용 실제구')"))
    conn.execute(
        text("INSERT INTO dong (dong_code, name, district_code) VALUES ('99999001', '실제동', '99999')")
    )
    with pytest.raises(DevFixtureError, match="더미가 아닌"):
        load(conn, app_env="local")
    assert conn.scalar(text("SELECT count(*) FROM dong WHERE dong_code = '99999001'")) == 1  # 지우지 않았다

    load(conn, app_env="local", force=True)
    assert conn.scalar(text("SELECT count(*) FROM dong WHERE dong_code = '99999001'")) == 0


def test_loaded_data_spot_checks(db_session):
    conn = db_session.connection()
    load(conn, app_env="local", force=True)
    assert conn.scalar(text("SELECT rent_per_sqm FROM district_rent WHERE district_code = '11320'")) is None
    assert conn.scalar(text("SELECT count(*) FROM support_program WHERE verified_by IS NULL")) == 1
    accuracy = conn.execute(
        text("SELECT field_name, checked_n, accuracy_pct FROM v_extraction_accuracy")
    ).all()
    assert {r.field_name: (r.checked_n, float(r.accuracy_pct)) for r in accuracy} == {
        "age": (3, 100.0), "amount": (3, 66.7), "district": (2, 50.0),
    }
    eligibility = conn.scalar(text("SELECT eligibility FROM support_program WHERE program_id = 3"))
    assert eligibility["or"][1]["value"] == ["조리기능사", "제과기능사"]
