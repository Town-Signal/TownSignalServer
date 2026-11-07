"""지원사업 seed 파일(data/seed/*.json)을 support_program에 적재한다 — 담당: 백엔드(지원사업).

실행: python -m batch.jobs.load_support_seed [--file PATH] [--yes]
주기: seed 파일이 바뀔 때 · 팀원 PC.

- 읽는 파일: data/seed/initial_support_programs.json, 그다음 support_programs_*.json(이름순).
  --file을 주면 그 파일 하나만 읽는다. 파일은 공고 딕셔너리의 JSON 리스트다.
- 적재 규칙은 아래 load_programs. 주간 수집(build_support_program.upsert_programs)과 달리 seed를 정본으로 보고
  같은 source_url의 행을 seed 값으로 덮어쓴다. 값 검증 · 변환은 같은 함수(program_values)를 쓴다.
- APP_ENV와 상관없이 돈다. DATABASE_URL 호스트가 로컬(localhost · 127.0.0.1 · ::1)이 아니면
  확인을 받는다(--yes로 건너뜀).
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL, make_url

from batch import config
from batch.jobs.build_support_program import program_values
from batch.pipeline.db import transaction
from common.constants import BENEFIT_CASH, BENEFIT_TYPES

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"
INITIAL_FILE = "initial_support_programs.json"
EXTRA_PATTERN = "support_programs_*.json"  # 수작업 공고 파일(예: support_programs_seoul_manual.json)
# compose의 "db"는 운영 EC2에서도 같은 이름이라 로컬로 보지 않는다
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

Item = tuple[str, int, dict[str, Any]]  # (파일 이름, 파일 안 번호(1부터), 공고)

COLUMNS = (
    "name", "agency", "amount_max", "district_code", "is_exclusive", "apply_start", "apply_end",
    "eligibility", "raw_text", "verified_by", "benefit_types",
)  # seed 값으로 쓰는 칼럼(source_url은 키, extracted_at은 새로 넣을 때만)


_CASTS = {"eligibility": "jsonb", "benefit_types": "text[]"}


def _param(column: str) -> str:
    return f"CAST(:{column} AS {_CASTS[column]})" if column in _CASTS else f":{column}"


UPDATE_SQL = text(
    "UPDATE support_program SET "
    + ", ".join(f"{c} = {_param(c)}" for c in COLUMNS)
    + " WHERE source_url = :source_url"
)
INSERT_SQL = text(
    f"INSERT INTO support_program ({', '.join(COLUMNS)}, source_url, extracted_at) "
    f"VALUES ({', '.join(_param(c) for c in COLUMNS)}, :source_url, now())"
)


class SeedLoadError(RuntimeError):
    """seed 적재를 멈춘 이유. 이미 한 일은 모두 되돌아간 뒤다."""


@dataclass(frozen=True)
class LoadResult:
    inserted: int  # 새로 넣은 공고 수
    updated: int  # 같은 source_url 행이 있어 seed 값으로 덮어쓴 공고 수(값이 같아도 센다)
    verified: int  # 적재한 공고 중 verified_by가 있는 수


def seed_files(seed_dir: Path = SEED_DIR) -> list[Path]:
    """initial 파일 다음에 수작업 파일들(이름순). 같은 주소가 겹치면 뒤 파일 값이 남는다."""
    initial = seed_dir / INITIAL_FILE
    extras = sorted(seed_dir.glob(EXTRA_PATTERN))
    return ([initial] if initial.exists() else []) + extras


def read_programs(paths: list[Path]) -> list[Item]:
    items: list[Item] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SeedLoadError(f"{path.name}: 최상위가 리스트가 아니다({type(data).__name__})")
        items += [(path.name, i, p) for i, p in enumerate(data, start=1)]
    return items


def _strip_nul(p: dict[str, Any]) -> dict[str, Any]:
    """글자 값의 NUL(0x00)을 지운다. PostgreSQL text에는 NUL을 넣을 수 없다.

    TODO(가정): PDF 첨부에서 뽑은 raw_text에 NUL이 섞여 있다(initial seed 9건).
    의미 없는 글자라 지우고 적재한다.
    """
    return {k: v.replace("\x00", "") if isinstance(v, str) else v for k, v in p.items()}


def _verified_by(p: dict[str, Any]) -> str | None:
    value = p.get("verified_by")
    return value.strip() or None if isinstance(value, str) else None


def _benefit_types(p: dict[str, Any]) -> list[str]:
    """seed의 benefit_types(지원 형태). 없으면 빈 배열, 같은 값은 한 번만(순서 유지).

    TODO(가정): amount_max > 0인데 비어 있으면 현금 지원으로 보고 ['현금']을 넣는다
    (initial seed는 이 키가 없다).
    허용 값(common.constants.BENEFIT_TYPES) 밖이면 ValueError — 적재 전체를 되돌린다.
    """
    value = p.get("benefit_types")
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TypeError(f"benefit_types는 글자 리스트여야 한다: {value!r}")
    unknown = [v for v in value if v not in BENEFIT_TYPES]
    if unknown:
        raise ValueError(f"benefit_types에 허용되지 않은 값: {unknown}")
    types = list(dict.fromkeys(value))
    if not types and p["amount_max"] > 0:
        types = [BENEFIT_CASH]
    return types


def load_programs(conn: Connection, items: list[Item]) -> LoadResult:
    """seed 공고를 source_url 기준으로 넣거나 갱신한다. 전부 한 트랜잭션이다.

    TODO(가정): seed 파일이 사람이 고친 정본이라 같은 source_url 행은 seed 값으로 덮어쓴다(같은 주소 행이
    여럿이면 모두). 검수(verified_by)도 seed에 적는 것을 전제로 그대로 반영한다(비어 있으면 NULL) —
    DB에서 직접 채운 verified_by는 다음 적재 때 seed 값으로 바뀐다.
    benefit_types는 _benefit_types 규칙으로 넣는다.
    TODO(가정): support_program에 없는 키(benefit_summary · announce_period · review_notes ·
    amount_evidence 등)는 무시한다. extracted_at은 새로 넣을 때만 now().
    글자 값의 NUL(0x00)은 지우고 넣는다(_strip_nul).
    하나라도 실패하면 이 호출에서 한 일을 모두 되돌리고 몇 번째 공고 · 이유를 담은 SeedLoadError를 올린다.
    """
    inserted = updated = verified = 0
    with transaction(conn):
        for file_name, no, p in items:
            try:
                if not isinstance(p, dict):
                    raise TypeError(f"공고는 딕셔너리여야 한다: {type(p).__name__}")
                p = _strip_nul(p)
                values = {
                    **program_values(p),
                    "verified_by": _verified_by(p),
                    "benefit_types": _benefit_types(p),
                }
                matched = conn.execute(UPDATE_SQL, values).rowcount
                if not matched:
                    conn.execute(INSERT_SQL, values)
            except Exception as exc:
                url = p.get("source_url") if isinstance(p, dict) else None
                reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                raise SeedLoadError(
                    f"{file_name} {no}번째 공고(source_url={url!r}) 적재 실패 — "
                    f"{type(exc).__name__}: {reason}"
                ) from exc
            if matched:
                updated += 1
            else:
                inserted += 1
            verified += values["verified_by"] is not None
    return LoadResult(inserted, updated, verified)


def confirm_target(url: str | URL, yes: bool, ask: Callable[[str], str] = input) -> bool:
    """로컬 DB면 그냥 진행. 아니면 --yes가 있거나 'yes'를 입력해야 진행한다."""
    host = make_url(url).host
    if host in LOCAL_HOSTS or yes:
        return True
    try:
        answer = ask(f"운영 DB에 적재합니다(host={host!r}). 계속하려면 yes를 입력하세요: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


def main(argv: list[str] | None = None, ask: Callable[[str], str] = input) -> int:
    parser = argparse.ArgumentParser(description="지원사업 seed 파일을 support_program에 적재한다.")
    parser.add_argument("--file", type=Path, help="이 파일 하나만 적재한다(기본: data/seed의 seed 파일 전부)")
    parser.add_argument("--yes", action="store_true", help="로컬이 아닌 DB여도 묻지 않고 적재한다")
    args = parser.parse_args(argv)

    if not confirm_target(config.DATABASE_URL, args.yes, ask):
        logger.error("적재를 취소했다.")
        return 1
    paths = [args.file] if args.file else seed_files()
    if not paths:
        logger.error("적재할 seed 파일이 없다: %s", SEED_DIR)
        return 1
    try:
        items = read_programs(paths)
        with create_engine(config.DATABASE_URL).connect() as conn:
            result = load_programs(conn, items)
    except (SeedLoadError, OSError, json.JSONDecodeError) as exc:
        logger.error("%s", exc)
        return 1
    logger.info("파일: %s", ", ".join(p.name for p in paths))
    logger.info(
        "새로 넣음 %d · 갱신 %d · 검수됨(verified_by 있음) %d",
        result.inserted,
        result.updated,
        result.verified,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())