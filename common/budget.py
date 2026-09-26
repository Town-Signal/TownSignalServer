"""가용예산 계산 (설계서 7.3 · 7.4절).

보수 = 자본금 + 선정 절차 없이 받는 보조금
기대 = 보수 + 경쟁 선정 보조금까지 모두 선정된 경우

융자와 현물은 어느 쪽에도 더하지 않고 따로 표시한다.
"""

from dataclasses import dataclass

from common.constants import RESERVE_MONTHS, SUPPORT_GRANT, SUPPORT_IN_KIND, SUPPORT_LOAN
from common.evaluator import Applicant, Program, is_guaranteed, match


@dataclass(frozen=True)
class Budget:
    district_code: str
    own_capital: int
    conservative: int
    expected: int
    grants_guaranteed: int
    grants_competitive: int
    loans: int
    in_kind: int
    programs: tuple[Program, ...]


def _sum_amount(programs) -> int:
    """중복 수혜가 불가한 사업끼리는 금액이 가장 큰 하나만 합산한다."""
    total = sum(p.amount_max for p in programs if p.duplicate_allowed)
    exclusive = [p.amount_max for p in programs if not p.duplicate_allowed]
    if exclusive:
        total += max(exclusive)
    return total


def available_budget(
    own_capital: int, programs, applicant: Applicant, district_code: str
) -> Budget:
    matched = match(programs, applicant, district_code)

    grants_guaranteed = _sum_amount([p for p in matched if is_guaranteed(p)])
    grants_competitive = _sum_amount(
        [p for p in matched if p.support_type == SUPPORT_GRANT and not is_guaranteed(p)]
    )
    loans = _sum_amount([p for p in matched if p.support_type == SUPPORT_LOAN])
    in_kind = _sum_amount([p for p in matched if p.support_type == SUPPORT_IN_KIND])

    conservative = own_capital + grants_guaranteed
    return Budget(
        district_code=district_code,
        own_capital=own_capital,
        conservative=conservative,
        expected=conservative + grants_competitive,
        grants_guaranteed=grants_guaranteed,
        grants_competitive=grants_competitive,
        loans=loans,
        in_kind=in_kind,
        programs=tuple(matched),
    )


def initial_cost(
    deposit: int, monthly_rent: int, fit_out: int = 0, reserve_months: int = RESERVE_MONTHS
) -> int:
    """행정동에서 창업할 때 드는 추정 금액.

    구성 항목과 예비 개월수는 아직 가정값이다 (설계서 13장 6번).
    """
    return deposit + monthly_rent * reserve_months + fit_out


def room(budget_amount: int, cost: int) -> int:
    """여유 = 가용예산 - 초기비용. 음수면 추천에서 제외한다."""
    return budget_amount - cost


def district_code_of(dong_code: str) -> str:
    """자치구 코드는 행정동 코드 앞 5자리다."""
    return dong_code[:5]
