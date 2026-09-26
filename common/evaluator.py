"""지원사업 자격 판정.

프레임워크와 DB에 의존하지 않는 순수 함수만 둔다.
배치(지원사업표 검증)와 API(요청 시 매칭)가 같은 함수를 부른다.
"""

from dataclasses import dataclass, field

from common.constants import SELECTION_AUTO, SUPPORT_GRANT


@dataclass(frozen=True)
class Applicant:
    age: int
    career_years: int = 0
    certifications: tuple[str, ...] = ()


@dataclass(frozen=True)
class Program:
    id: int
    name: str
    scope: str  # 중앙 | 서울시 | 자치구
    amount_max: int
    support_type: str  # 보조금 | 융자 | 현물
    selection_type: str  # 자동 | 경쟁
    district_code: str | None = None  # 자치구 사업일 때만
    min_age: int | None = None
    max_age: int | None = None
    career_max_years: int | None = None
    cert_required: tuple[str, ...] = field(default_factory=tuple)
    duplicate_allowed: bool = True
    verified: bool = False


def is_eligible(program: Program, applicant: Applicant) -> bool:
    """인적 조건이 자격요건을 충족하는지 판정한다."""
    if program.min_age is not None and applicant.age < program.min_age:
        return False
    if program.max_age is not None and applicant.age > program.max_age:
        return False
    if program.career_max_years is not None and applicant.career_years > program.career_max_years:
        return False
    if program.cert_required:
        held = set(applicant.certifications)
        if not set(program.cert_required).issubset(held):
            return False
    return True


def applies_to(program: Program, district_code: str) -> bool:
    """해당 자치구에서 받을 수 있는 사업인지 판정한다."""
    if program.district_code is None:
        return True
    return program.district_code == district_code


def match(programs, applicant: Applicant, district_code: str) -> list[Program]:
    """검수를 통과한 사업 중 자격과 지역이 모두 맞는 것만 돌려준다.

    verified가 거짓인 사업은 금액이 검증되지 않아 예산에 반영하지 않는다.
    """
    return [
        p
        for p in programs
        if p.verified and is_eligible(p, applicant) and applies_to(p, district_code)
    ]


def is_guaranteed(program: Program) -> bool:
    """선정 절차 없이 받는 보조금인지 판정한다. 보수 예산에 들어갈 조건이다."""
    return program.selection_type == SELECTION_AUTO and program.support_type == SUPPORT_GRANT
