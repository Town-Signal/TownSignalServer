"""batch.pipeline.backup — 지원사업표 pg_dump 명령 조립 · 최근 4개 보관(명세 4.6 · 12.1)."""

from datetime import datetime

from batch.pipeline.backup import backups_to_remove, build_pg_dump_command


def test_pg_dump_command():
    argv, env = build_pg_dump_command(
        "postgresql+psycopg://townsignal:s3cret@db:5432/townsignal", "/backups", datetime(2026, 10, 12, 3, 0)
    )
    assert argv[0] == "pg_dump" and "--format=custom" in argv
    assert [a for a in argv if a.startswith("--table=")] == [
        "--table=support_program",
        "--table=program_verification",
    ]
    assert {"--host=db", "--port=5432", "--username=townsignal", "--dbname=townsignal"} <= set(argv)
    assert argv[-1] == "--file=/backups/support_program_20261012.dump"
    assert not any("s3cret" in a for a in argv)  # 비밀번호는 argv(프로세스 목록)에 없다
    assert env == {"PGPASSWORD": "s3cret"}


def test_pg_dump_command_without_password():
    argv, env = build_pg_dump_command(
        "postgresql+psycopg://townsignal@localhost/townsignal", "b", datetime(2026, 1, 5)
    )
    assert "--port=5432" in argv and env == {}


def test_backups_to_remove_keeps_latest_4():
    names = [f"support_program_2026{m:02d}01.dump" for m in range(1, 7)] + ["memo.txt", "other.dump"]
    assert backups_to_remove(names) == ["support_program_20260101.dump", "support_program_20260201.dump"]
    assert backups_to_remove(names[:3]) == []
