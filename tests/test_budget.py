"""common.budget — 자치구별 매칭 · 중복 수혜 · 가용예산 · 추정 임대비용 · passed (명세 7.4 · 7.5)."""

from datetime import date
from decimal import Decimal

from common.budget import (
    RentCost,
    available_budget,
    district_code_of,
    format_manwon,
    judge,
    match_district,
    rent_cost,
    select_programs,
)
from common.evaluator import Applicant, Program

GWANAK, GANGNAM = "11620", "11680"
YOUTH = Applicant(age=27, certificates=("조리기능사",))
UNDER_39 = {"field": "age", "op": "<=", "value": 39}


def program(
    program_id, amount, *, exclusive=False, district=None, verified="검수자", eligibility=UNDER_39, **kw
):
    return Program(
        program_id=program_id,
        name=kw.pop("name", f"사업{program_id}"),
        amount_max=amount,
        eligibility=eligibility,
        is_exclusive=exclusive,
        district_code=district,
        verified_by=verified,
        **kw,
    )


# ── 7.5 회귀: 관악구 33㎡ ────────────────────────────────────


def test_gwanak_33sqm_regression():
    cost = rent_cost(Decimal("28.5"), 33)
    assert cost == RentCost(monthly_rent=940_500, deposit=14_107_500, estimated_rent_cost=16_929_000)
    assert judge(55_000_000, cost) == (38_071_000, "통과")


def test_rent_cost_accepts_float_and_custom_multiplier():
    assert rent_cost(28.5, 33.0).estimated_rent_cost == 16_929_000
    cost = rent_cost("10", 10, deposit_multiplier=Decimal("10.00"))
    assert (cost.monthly_rent, cost.deposit, cost.estimated_rent_cost) == (100_000, 1_000_000, 1_300_000)


def test_rent_cost_rounds_half_up():
    # 12.345 × 1000 × 1.1 = 13,579.5 → 13,580 (사사오입)
    assert rent_cost(Decimal("12.345"), Decimal("1.1")).monthly_rent == 13_580


def test_missing_rent_is_unknown_not_zero():
    assert rent_cost(None, 33) is None
    assert judge(55_000_000, None) == (None, "확인불가")


def test_negative_margin_is_excluded_and_zero_passes():
    cost = rent_cost(Decimal("28.5"), 33)
    assert judge(16_928_999, cost) == (-1, "제외")
    assert judge(16_929_000, cost) == (0, "통과")


# ── 7.4 매칭 · 중복 수혜 ─────────────────────────────────────


def test_exclusive_max_plus_all_shared():
    big = program(1, 20_000_000, exclusive=True, name="청년창업사관학교")
    small = program(2, 7_000_000, exclusive=True)
    shared_a = program(3, 3_000_000)
    shared_b = program(4, 1_000_000)
    result = select_programs([small, shared_a, big, shared_b])
    assert {p.program_id for p in result.chosen} == {1, 3, 4}
    assert result.support_fund_max == 24_000_000
    assert [e.program.program_id for e in result.excluded] == [2]
    assert result.excluded[0].reason == "더 큰 지원금인 청년창업사관학교(2,000만 원)와 중복 수혜 불가"


def test_exclusive_tie_picks_smaller_program_id():
    result = select_programs([program(7, 5_000_000, exclusive=True), program(3, 5_000_000, exclusive=True)])
    assert [p.program_id for p in result.chosen] == [3]
    assert [e.program.program_id for e in result.excluded] == [7]


def test_no_match_means_budget_is_capital():
    result = match_district([], YOUTH, GWANAK)
    assert (result.chosen, result.excluded, result.support_fund_max) == ((), (), 0)
    assert available_budget(30_000_000, result.support_fund_max) == 30_000_000


def test_districts_are_matched_separately():
    national = program(1, 10_000_000, exclusive=True)
    gwanak_only = program(2, 5_000_000, district=GWANAK)
    programs = [national, gwanak_only]
    assert match_district(programs, YOUTH, GWANAK).support_fund_max == 15_000_000
    assert match_district(programs, YOUTH, GANGNAM).support_fund_max == 10_000_000


def test_unverified_and_ineligible_programs_are_ignored():
    unverified = program(1, 50_000_000, verified=None)
    over_39 = program(2, 9_000_000, eligibility={"field": "age", "op": ">", "value": 39})
    broken = program(3, 9_000_000, eligibility={"field": "income", "op": ">", "value": 0})
    ok = program(4, 1_000_000)
    result = match_district([unverified, over_39, broken, ok], YOUTH, GWANAK)
    assert [p.program_id for p in result.chosen] == [4]


def test_closed_programs_are_still_matched():
    closed = program(1, 2_000_000, apply_end=date(2020, 1, 31))
    assert match_district([closed], YOUTH, GWANAK).support_fund_max == 2_000_000


def test_format_manwon():
    assert format_manwon(20_000_000) == "2,000만 원"
    assert format_manwon(5_000) == "1만 원"  # 만 원 미만은 반올림


def test_district_code_is_first_five_digits():
    assert district_code_of("11620655") == "11620"
