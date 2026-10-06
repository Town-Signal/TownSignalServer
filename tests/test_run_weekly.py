"""batch.jobs.run_weekly — 주간 배치 진입점(명세 4.6 · 12.1)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from batch.jobs import build_summary_cache, build_support_program, run_weekly, train_and_predict

NOW = datetime(2026, 10, 12, 3, 0, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    dummy_db.execute(text("DELETE FROM recommendation"))
    dummy_db.execute(
        text(
            "INSERT INTO recommendation (input_condition, calculated_budgets, created_at) "
            "VALUES ('{}', '{}', :t)"
        ),
        {"t": NOW - timedelta(days=45)},
    )
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


@pytest.fixture
def forbid_training(monkeypatch):
    """학습 · 예측 · 요약은 주간 배치에서 부르면 안 된다."""

    def fail():
        raise AssertionError("run_weekly가 학습 · 요약을 불렀다")

    monkeypatch.setattr(train_and_predict, "main", fail)
    monkeypatch.setattr(build_summary_cache, "main", fail)


def test_run_weekly_continues_after_step_failure(conn, tmp_path, forbid_training):
    results = run_weekly.run(conn, now=NOW, backup_dir=str(tmp_path))
    assert [r.name for r in results] == ["지원사업 수집 · 구조화", "30일 지난 추천 정리", "지원사업표 백업"]
    collect, cleanup, backup = results
    assert not collect.ok and collect.detail.startswith("미구현")  # 지원사업 담당 구현 전
    assert cleanup.ok and cleanup.detail == "1건 삭제"  # 수집이 실패해도 다음 단계는 돈다
    assert backup.ok and "pg_dump" in backup.detail and "support_program_20261012.dump" in backup.detail
    assert conn.execute(text("SELECT count(*) FROM recommendation")).scalar_one() == 0


def test_run_weekly_all_steps_ok(conn, tmp_path, monkeypatch, forbid_training):
    called = []
    monkeypatch.setattr(build_support_program, "main", lambda: called.append("collect"))
    for day in ("20260901", "20260908", "20260915", "20260922", "20260929"):
        (tmp_path / f"support_program_{day}.dump").write_bytes(b"")
    results = run_weekly.run(conn, now=NOW, backup_dir=str(tmp_path))
    assert all(r.ok for r in results) and called == ["collect"]
    assert "support_program_20260901.dump" in results[2].detail  # 최근 4개만 남기고 지울 목록


def test_main_exit_code(monkeypatch):
    monkeypatch.setattr(run_weekly, "run", lambda conn: [run_weekly.StepResult("a", True, "ok")])
    assert run_weekly.main() == 0
    monkeypatch.setattr(run_weekly, "run", lambda conn: [run_weekly.StepResult("a", False, "x")])
    assert run_weekly.main() == 1
