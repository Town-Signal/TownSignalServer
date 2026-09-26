from common.evaluator import Applicant, Program, applies_to, is_eligible, match

YOUTH = Applicant(age=27, career_years=0, certifications=("조리기능사",))


def program(**kwargs) -> Program:
    base = dict(
        id=1,
        name="테스트 사업",
        scope="중앙",
        amount_max=10_000_000,
        support_type="보조금",
        selection_type="자동",
        verified=True,
    )
    base.update(kwargs)
    return Program(**base)


def test_age_out_of_range_is_not_eligible():
    assert not is_eligible(program(min_age=30, max_age=39), YOUTH)


def test_career_over_limit_is_not_eligible():
    assert not is_eligible(program(career_max_years=0), Applicant(age=27, career_years=3))


def test_required_certificate_must_be_held():
    assert is_eligible(program(cert_required=("조리기능사",)), YOUTH)
    assert not is_eligible(program(cert_required=("제과기능사",)), YOUTH)


def test_district_program_applies_only_to_its_district():
    gwanak = program(scope="자치구", district_code="11620")
    assert applies_to(gwanak, "11620")
    assert not applies_to(gwanak, "11680")


def test_unverified_program_is_excluded():
    assert match([program(verified=False)], YOUTH, "11620") == []
