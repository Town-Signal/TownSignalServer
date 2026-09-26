from common.budget import available_budget, district_code_of, initial_cost, room
from common.evaluator import Applicant, Program

YOUTH = Applicant(age=27, career_years=0, certifications=("조리기능사",))

COMPETITIVE = Program(
    id=1,
    name="청년창업사관학교",
    scope="중앙",
    amount_max=20_000_000,
    support_type="보조금",
    selection_type="경쟁",
    min_age=19,
    max_age=39,
    verified=True,
)

GUARANTEED = Program(
    id=2,
    name="자치구 지원금",
    scope="자치구",
    amount_max=5_000_000,
    support_type="보조금",
    selection_type="자동",
    district_code="11620",
    verified=True,
)

LOAN = Program(
    id=3,
    name="창업 융자",
    scope="중앙",
    amount_max=50_000_000,
    support_type="융자",
    selection_type="자동",
    verified=True,
)


def test_competitive_grant_counts_only_in_expected():
    budget = available_budget(30_000_000, [COMPETITIVE, GUARANTEED], YOUTH, "11620")
    assert budget.conservative == 35_000_000
    assert budget.expected == 55_000_000


def test_loan_is_not_added_to_either_budget():
    budget = available_budget(30_000_000, [LOAN], YOUTH, "11620")
    assert budget.conservative == 30_000_000
    assert budget.expected == 30_000_000
    assert budget.loans == 50_000_000


def test_district_program_excluded_in_other_district():
    budget = available_budget(30_000_000, [GUARANTEED], YOUTH, "11680")
    assert budget.conservative == 30_000_000


def test_room_uses_initial_cost():
    cost = initial_cost(deposit=30_000_000, monthly_rent=1_000_000, fit_out=4_000_000)
    assert cost == 40_000_000
    assert room(55_000_000, cost) == 15_000_000


def test_district_code_is_first_five_digits():
    assert district_code_of("1162069500") == "11620"
