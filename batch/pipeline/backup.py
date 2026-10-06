"""지원사업표 백업 명령 조립 (전체 명세 4.6 run_weekly ④ · 12.1 '백업 · 로그').

support_program · program_verification만 주 1회 pg_dump하고 최근 4개를 남긴다. 나머지 표는 원천과
배치로 다시 만들 수 있다. 여기서는 명령과 지울 파일 목록을 만들기만 하고 실행하지 않는다.
TODO(가정): 배치 이미지(Dockerfile.batch)에 postgresql-client가 없어 실제 실행 전에 추가해야 한다.
"""

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

BACKUP_TABLES = ("support_program", "program_verification")
KEEP_BACKUPS = 4
_BACKUP_FILE = re.compile(r"^support_program_\d{8}\.dump$")


def backup_file_name(now: datetime) -> str:
    return f"support_program_{now:%Y%m%d}.dump"


def build_pg_dump_command(
    database_url: str, out_dir: str | Path, now: datetime
) -> tuple[list[str], dict[str, str]]:
    """(argv, env). SQLAlchemy URL(postgresql+psycopg://…)을 libpq 인자로 풀고,
    비밀번호는 프로세스 목록에 보이지 않게 argv가 아니라 env(PGPASSWORD)로 넘긴다."""
    url = make_url(database_url)
    argv = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        f"--host={url.host or 'localhost'}",
        f"--port={url.port or 5432}",
        f"--username={url.username}",
        f"--dbname={url.database}",
        *(f"--table={name}" for name in BACKUP_TABLES),
        f"--file={Path(out_dir) / backup_file_name(now)}",
    ]
    env = {"PGPASSWORD": url.password} if url.password else {}
    return argv, env


def backups_to_remove(file_names: Iterable[str], keep: int = KEEP_BACKUPS) -> list[str]:
    """백업 파일 중 최근 keep개를 남기고 지울 파일 이름(날짜가 파일 이름에 있어 이름순 = 날짜순)."""
    backups = sorted((name for name in file_names if _BACKUP_FILE.match(name)), reverse=True)
    return sorted(backups[keep:])
