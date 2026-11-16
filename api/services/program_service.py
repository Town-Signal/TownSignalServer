"""③ 지원사업 공고 상세 · ① 지원사업 일정 (전체 명세 8.2)."""

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from api.errors import AppError
from api.repositories import program_repository as repo
from api.schemas.common import ListData
from api.schemas.program import ProgramDetail, UpcomingProgram

UPCOMING_MAX = 50  # limit을 생략하면 최대 50건(판정 28, 가정)
NATIONAL = "전국"
STATUS_OPEN, STATUS_CLOSED = "모집 중", "마감"


def _common_fields(program, district_name: str | None) -> dict[str, Any]:
    return {
        "program_id": program.program_id,
        "name": program.name,
        "agency": program.agency,
        "amount_max": program.amount_max,
        "district_code": program.district_code,
        "district_name": district_name or NATIONAL,
        "apply_start": program.apply_start,
        "apply_end": program.apply_end,
        "source_url": program.source_url,
    }


def detail(session: Session, program_id: int) -> ProgramDetail:
    row = repo.get_verified_program(session, program_id)
    if row is None:  # 없거나 검수 전(verified_by NULL)이면 404(8.2 ③)
        raise AppError("PROGRAM_NOT_FOUND", 404, "공고를 찾을 수 없어요.")
    program = row.SupportProgram
    return ProgramDetail(
        **_common_fields(program, row.district_name),
        always_open=program.apply_end is None,
        is_exclusive=program.is_exclusive,
        eligibility=program.eligibility,
        raw_text=program.raw_text,
    )


def upcoming(session: Session, today: date, limit: int | None) -> ListData[UpcomingProgram]:
    """검수 · 현금 지원(amount_max > 0) · 마감일 있는 공고. 마감 공고도 넣고 status로 구분한다(2026-11-07).

    TODO(가정): 마감일 당일까지 "모집 중"(apply_end ≥ 오늘). total은 limit으로 자르기 전 개수.
    """
    rows, total = repo.list_upcoming(session, today, limit or UPCOMING_MAX)
    items = [
        UpcomingProgram(
            **_common_fields(row.SupportProgram, row.district_name),
            status=STATUS_OPEN if row.SupportProgram.apply_end >= today else STATUS_CLOSED,
        )
        for row in rows
    ]
    return ListData(items=items, total=total)
