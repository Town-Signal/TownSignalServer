"""자치구별 지원사업 매칭 · 가용예산 · 추정 임대비용 · passed (전체 명세 7.4 · 7.5).

가용예산은 하나다: available_budget = capital + support_fund_max.
support_fund_max에는 현금 지원(amount_max > 0)만 더한다. 금액이 0인 공고(공간 · 교육 등)는 예산 · 중복 수혜
계산에서 빼고 자치구별 목록으로만 보여 준다(2026-11-07 팀 결정, split_cash · eligible_in_district).
보수/기대 예산과 지원 유형(보조금 · 융자 · 현물) 구분은 두지 않는다(13장 판정 2 · 3).
모든 금액은 원 단위 정수이고 반올림은 사사오입이다(가정).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from common.constants import (
    DEPOSIT_MULTIPLIER_DEFAULT,
    INITIAL_RENT_MONTHS,
    PASSED_EXCLUDED,
    PASSED_OK,
    PASSED_UNKNOWN,
    RENT_PER_SQM_UNIT_WON,
)
from common.evaluator import Applicant, Program, applies_to, evaluate, is_matchable

Number = int | float | Decimal | str


@dataclass(frozen=True)
class ExcludedProgram:
    program: Program
    reason: str  # "더 큰 지원금인 {사업명}({금액}만 원)와 중복 수혜 불가"


@dataclass(frozen=True)
class DistrictMatch:
    chosen: tuple[Program, ...]  # 합산하는 사업 (비배타 전부 + 배타 중 최대 1개)
    excluded: tuple[ExcludedProgram, ...]  # 중복 수혜로 빠진 배타 사업
    support_fund_max: int


@dataclass(frozen=True)
class RentCost:
    monthly_rent: int  # 원/월
    deposit: int  # 월세 × 보증금 배수
    estimated_rent_cost: int  # 보증금 + 월세 × 3


def _round_won(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_decimal(value: Number) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def format_manwon(amount_won: int) -> str:
    """원 → '2,000만 원' 표기. 만 원 미만은 반올림한다."""
    manwon = _round_won(Decimal(amount_won) / 10000)
    return f"{manwon:,}만 원"


def exclusion_reason(best: Program) -> str:
    return f"더 큰 지원금인 {best.name}({format_manwon(best.amount_max)})와 중복 수혜 불가"


def select_programs(eligible: Iterable[Program]) -> DistrictMatch:
    """중복 수혜 규칙(7.4): 배타 사업 중 금액이 가장 큰 하나 + 비배타 사업 전부.

    금액이 같은 배타 사업은 program_id가 작은 쪽을 고른다(가정).
    """
    eligible = list(eligible)
    exclusive = [p for p in eligible if p.is_exclusive]
    shared = [p for p in eligible if not p.is_exclusive]
    best = max(exclusive, key=lambda p: (p.amount_max, -p.program_id), default=None)
    chosen = shared + ([best] if best else [])
    excluded = tuple(ExcludedProgram(p, exclusion_reason(best)) for p in exclusive if p is not best)
    return DistrictMatch(
        chosen=tuple(chosen),
        excluded=excluded,
        support_fund_max=sum(p.amount_max for p in chosen),
    )


def split_cash(programs: Iterable[Program]) -> tuple[list[Program], list[Program]]:
    """(현금 지원 amount_max > 0, 금액 0 공고). 금액 0 공고는 예산에 더하지 않는다."""
    programs = list(programs)
    return [p for p in programs if p.amount_max > 0], [p for p in programs if p.amount_max <= 0]


def eligible_in_district(
    programs: Iterable[Program], applicant: Applicant, district_code: str
) -> list[Program]:
    """한 자치구에서 받을 수 있는 공고: 검수됨 · 전국이거나 그 구 전용 · 자격 조건 통과(7.4)."""
    applicable = [p for p in programs if is_matchable(p) and applies_to(p, district_code)]
    return [p for p in applicable if evaluate(p.eligibility, applicant)]


def match_district(programs: Iterable[Program], applicant: Applicant, district_code: str) -> DistrictMatch:
    """한 자치구의 매칭 결과. 25개 구마다 따로 부른다(구 전용 사업 때문에 구마다 다르다)."""
    return select_programs(eligible_in_district(programs, applicant, district_code))


def available_budget(capital: int, support_fund_max: int) -> int:
    """매칭 0건이면 support_fund_max = 0이라 가용예산 = 자본금이다."""
    return capital + support_fund_max


def rent_cost(
    rent_per_sqm: Number | None,
    area_sqm: Number,
    deposit_multiplier: Number = DEPOSIT_MULTIPLIER_DEFAULT,
) -> RentCost | None:
    """추정 임대비용(7.5). rent_per_sqm(천원/㎡ · 월)이 없으면 None — 0으로 채우지 않는다.

    monthly_rent        = round(rent_per_sqm × 1,000 × area)
    deposit             = round(monthly_rent × deposit_multiplier)
    estimated_rent_cost = deposit + monthly_rent × 3
    """
    if rent_per_sqm is None:
        return None
    monthly = _round_won(_to_decimal(rent_per_sqm) * RENT_PER_SQM_UNIT_WON * _to_decimal(area_sqm))
    deposit = _round_won(Decimal(monthly) * _to_decimal(deposit_multiplier))
    return RentCost(
        monthly_rent=monthly,
        deposit=deposit,
        estimated_rent_cost=deposit + monthly * INITIAL_RENT_MONTHS,
    )


def judge(available: int, cost: RentCost | None) -> tuple[int | None, str]:
    """(budget_margin, passed). 임대료가 없으면 (None, "확인불가") — 탈락시키지 않는다."""
    if cost is None:
        return None, PASSED_UNKNOWN
    margin = available - cost.estimated_rent_cost
    return margin, PASSED_OK if margin >= 0 else PASSED_EXCLUDED


def district_code_of(dong_code: str) -> str:
    """자치구 코드는 행정동 코드 앞 5자리다."""
    return dong_code[:5]
