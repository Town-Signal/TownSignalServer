"""common.evaluator — eligibility 조건 트리 재귀 평가 (명세 7.3)."""

import pytest

from common.evaluator import Applicant, Program, applies_to, evaluate, is_matchable

# 7.3 예시 입력: 나이 27 · 경력 0년 · 조리기능사 보유
YOUTH = Applicant(
    age=27,
    career_years=0,
    capital=30_000_000,
    certificates=("조리기능사",),
    industry_code="CS100001",
    industry_category="외식업",
)

SPEC_EXAMPLE = {
    "and": [
        {"field": "age", "op": "<=", "value": 39},
        {
            "or": [
                {"field": "career_years", "op": ">=", "value": 2},
                {"field": "certificates", "op": "contains_any", "value": ["조리기능사"]},
            ]
        },
    ]
}


def cond(field, op, value):
    return {"field": field, "op": op, "value": value}


def program(**kwargs) -> Program:
    base = dict(program_id=1, name="시험 사업", amount_max=10_000_000, eligibility={"and": []})
    return Program(**{**base, **kwargs})


def test_spec_example_is_true():
    assert evaluate(SPEC_EXAMPLE, YOUTH)


def test_spec_example_false_without_certificate_or_career():
    assert not evaluate(SPEC_EXAMPLE, Applicant(age=27))
    assert evaluate(SPEC_EXAMPLE, Applicant(age=27, career_years=2))
    assert not evaluate(SPEC_EXAMPLE, Applicant(age=40, career_years=5))


def test_empty_and_is_true_empty_or_is_false():
    assert evaluate({"and": []}, YOUTH)
    assert not evaluate({"or": []}, YOUTH)


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (cond("age", "==", 27), True),
        (cond("age", "!=", 27), False),
        (cond("age", "<", 27), False),
        (cond("age", "<=", 27), True),
        (cond("age", ">", 26), True),
        (cond("age", ">=", 28), False),
        (cond("capital", ">=", 30_000_000), True),
        (cond("career_years", "<=", 0), True),
        (cond("certificates", "contains_any", ["제과기능사", "조리기능사"]), True),
        (cond("certificates", "contains_all", ["제과기능사", "조리기능사"]), False),
        (cond("certificates", "contains_all", ["조리기능사"]), True),
        (cond("industry_code", "==", "CS100001"), True),
        (cond("industry_code", "in", ["CS100002", "CS100003"]), False),
        (cond("industry_category", "in", ["외식업", "소매업"]), True),
    ],
)
def test_operators(node, expected):
    assert evaluate(node, YOUTH) is expected


@pytest.mark.parametrize(
    "bad",
    [
        cond("income", ">=", 0),  # 알 수 없는 필드
        cond("age", "between", [20, 39]),  # 알 수 없는 연산자
        cond("age", "<=", "39"),  # 타입 불일치
        cond("age", "<=", True),  # bool은 정수로 보지 않는다
        cond("certificates", "contains_any", "조리기능사"),  # 목록이 아님
        cond("certificates", "==", ["조리기능사"]),  # 목록 필드에 문자열 연산자
        cond("industry_code", "in", "CS100001"),  # in 값이 목록이 아님
        cond("industry_code", "<", "CS100001"),  # 문자열에 수치 연산자
        cond("residence_district_code", "!=", "11320"),  # 거주 구는 == · in만
        cond("residence_district_code", "==", 11320),  # 타입 불일치
        {"and": [cond("age", "<=", 39)], "or": []},  # 그룹 키가 둘
        {"field": "age", "op": "<="},  # value 없음
        {"and": "age<=39"},  # 자식이 목록이 아님
        ["age", "<=", 39],  # 노드가 객체가 아님
    ],
)
def test_unparseable_tree_does_not_match(bad, caplog):
    assert evaluate(bad, YOUTH) is False
    assert "매칭하지 않음" in caplog.text


def test_unparseable_node_inside_or_still_rejects_whole_program():
    """or의 다른 가지가 참이어도, 해석할 수 없는 노드가 있으면 사업 전체를 매칭하지 않는다(보수적 판정)."""
    tree = {"or": [cond("age", "<=", 39), cond("income", ">=", 0)]}
    assert evaluate(tree, YOUTH) is False


def test_district_program_applies_only_to_its_district():
    gwanak = program(district_code="11620")
    assert applies_to(gwanak, "11620")
    assert not applies_to(gwanak, "11680")
    assert applies_to(program(district_code=None), "11680")  # 전국 사업


def test_only_verified_programs_are_matchable():
    assert is_matchable(program(verified_by="검수자"))
    assert not is_matchable(program(verified_by=None))


# ── 거주 구(residence_district_code, 2026-11-17) ───────────────

DOBONG_RESIDENT = Applicant(age=27, residence_district_code="11320")


@pytest.mark.parametrize(
    ("node", "applicant", "expected"),
    [
        (cond("residence_district_code", "==", "11320"), DOBONG_RESIDENT, True),
        (cond("residence_district_code", "==", "11290"), DOBONG_RESIDENT, False),
        (cond("residence_district_code", "in", ["11290", "11320"]), DOBONG_RESIDENT, True),
        (cond("residence_district_code", "in", ["11290", "11305"]), DOBONG_RESIDENT, False),
        # 거주 구를 고르지 않으면 조건은 거짓이다
        (cond("residence_district_code", "==", "11320"), Applicant(age=27), False),
        (cond("residence_district_code", "in", ["11320"]), Applicant(age=27), False),
    ],
)
def test_residence_district(node, applicant, expected):
    assert evaluate(node, applicant) is expected


def test_residence_not_given_is_false_not_unparseable(caplog):
    """미입력은 해석 불가가 아니다 — 경고 없이 거짓이고, or의 다른 가지와 다른 사업 판정은 그대로다."""
    no_residence = Applicant(age=27)
    residence = cond("residence_district_code", "==", "11320")
    assert evaluate({"and": [cond("age", "<=", 39), residence]}, no_residence) is False
    assert evaluate({"or": [residence, cond("age", "<=", 39)]}, no_residence) is True
    assert evaluate({"and": [cond("age", "<=", 39)]}, no_residence) is True
    assert "매칭하지 않음" not in caplog.text
