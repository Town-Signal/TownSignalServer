"""batch.llm.structure — 프롬프트 · 조건 트리 번역 · 검증 · 보정 · 건너뛰기. LLM은 가짜로 바꾼다."""

from datetime import date

import pytest
from sqlalchemy import text

from batch.jobs.build_support_program import RawNotice, upsert_programs
from batch.llm import structure
from batch.llm.client import LLMError, LLMUnavailable
from common.certificate_normalizer import STANDARD_NAMES

CODES = {"용산구": "11170", "구로구": "11530"}


def notice(**kw) -> RawNotice:
    base = {
        "source_url": "https://example.test/llm/1",
        "title": "시험 공고",
        "agency": "기관",
        "raw_text": "원문 전체",
        "district_code": None,
        "region": "서울",
        "apply_start": date(2026, 1, 2),
        "apply_end": date(2026, 12, 31),
    }
    return RawNotice(**{**base, **kw})


def cond(field, op, **kw):
    return {"field": field, "op": op, "evidence": "근거", **kw}


def result(**kw):
    return {
        "amount_max": 0,
        "amount_evidence": "",
        "district_name": None,
        "district_evidence": "",
        "is_exclusive": False,
        "conditions_known": True,
        "conditions": [],
        **kw,
    }


def program(res, **notice_kw):
    return structure.to_program(notice(**notice_kw), res, CODES)


def age(op, value):
    return cond("age", op, number_value=value)


def node(field, op, value):
    return {"field": field, "op": op, "value": value}


# ── 수집 값 + 구조화 값 합치기 ──────────────────────────────────────────


def test_program_keeps_collected_values_and_adds_structured_ones():
    p = program(result(amount_max=100_000_000, is_exclusive=True, conditions=[[age("<=", 39)]]))

    assert p == {
        "name": "시험 공고",
        "agency": "기관",
        "source_url": "https://example.test/llm/1",
        "raw_text": "원문 전체",
        "apply_start": date(2026, 1, 2),
        "apply_end": date(2026, 12, 31),
        "amount_max": 100_000_000,
        "district_code": None,
        "is_exclusive": True,
        "eligibility": {"and": [node("age", "<=", 39)]},
    }


@pytest.mark.parametrize("bad", [-1, "5000", None, True, 1.5])
def test_invalid_amount_becomes_zero(bad):
    assert program(result(amount_max=bad))["amount_max"] == 0


@pytest.mark.parametrize("value", ["true", 1, None])
def test_is_exclusive_is_true_only_for_boolean_true(value):
    assert program(result(is_exclusive=value))["is_exclusive"] is False


# ── 구 판별 ────────────────────────────────────────────────────────────


# 원문에 실제로 있는 근거 문구
GROUND = "신청대상: 용산구에서 6개월 이상 거주 중이며 용산구에 사업자등록을 한 청년"


def district(name, evidence, raw_text=f"공고 안내\n{GROUND}\n제출서류", **notice_kw):
    res = result(district_name=name, district_evidence=evidence)
    return program(res, raw_text=raw_text, **notice_kw)["district_code"]


def test_district_is_converted_to_code_when_evidence_is_in_the_text():
    assert district("용산구", "용산구에서 6개월 이상 거주 중이며") == "11170"


def test_evidence_with_changed_particle_is_accepted():
    """원문은 "용산구에서"인데 LLM이 "용산구에"로 옮겼다. 거의 그대로면 인정한다."""
    assert district("용산구", "용산구에 6개월 이상 거주 중이며 용산구에 사업자등록을 한 청년") == "11170"


def test_evidence_matches_even_if_whitespace_differs():
    assert district("용산구", "용산구에서   6개월 이상\n거주 중이며") == "11170"


def test_district_without_evidence_is_none():
    assert district("용산구", "") is None
    assert district("용산구", None) is None


def test_evidence_that_is_not_in_the_text_is_rejected():
    assert district("용산구", "용산구에 거주하는 자만 신청 가능") is None


def test_evidence_without_the_district_name_is_rejected():
    """서울시 거주 · 서울시 소재 같은 문구는 구 전용이 아니다(운영 기관이 있는 구를 고른 경우)."""
    raw = "용산구 청년창업지원센터 안내\n신청대상: 19~39세 서울시 거주 청년 창업자"
    assert district("용산구", "19~39세 서울시 거주 청년 창업자", raw) is None


def test_evidence_that_only_names_the_operating_institution_is_rejected():
    raw = "구로구 청년창업지원센터가 입주기업을 모집합니다\n신청대상: 창업 7년 미만 스타트업"
    assert district("구로구", "구로구 청년창업지원센터가 입주기업을 모집합니다", raw) is None


@pytest.mark.parametrize("word", ["거주", "소재", "관내", "주민", "구민", "주소지", "사업장"])
def test_each_region_word_is_accepted(word):
    quote = f"용산구 {word} 청년"

    assert district("용산구", quote, f"신청대상 {quote} 안내") == "11170"


def test_unknown_or_missing_district_name_is_none():
    assert district("없는구", "없는구에 거주하는 청년", "신청대상: 없는구에 거주하는 청년") is None
    assert district(None, GROUND) is None


def test_nationwide_notice_ignores_district_name():
    assert district("용산구", "용산구에서 6개월 이상 거주 중이며", region="전국") is None


def test_notice_with_known_district_keeps_it():
    assert district("구로구", "", district_code="11170") == "11170"


# ── 조건 트리 ──────────────────────────────────────────────────────────


def test_no_conditions_known_means_anyone_and_unknown_means_no_one():
    assert structure.build_eligibility([], True) == {"and": []}
    assert structure.build_eligibility([], False) == {"or": []}


@pytest.mark.parametrize("raw", [None, "글자", {"a": 1}, [[]], [["문자열"]], [None]])
def test_malformed_conditions_become_no_one(raw):
    assert structure.build_eligibility(raw, True) == {"or": []}


def test_range_in_separate_groups_is_kept_as_and():
    tree = structure.build_eligibility([[age(">=", 19)], [age("<=", 39)]], True)

    assert tree == {"and": [node("age", ">=", 19), node("age", "<=", 39)]}


def test_always_true_or_of_lower_and_upper_bound_is_split_into_and():
    tree = structure.build_eligibility([[age(">=", 19), age("<=", 39)]], True)

    assert tree == {"and": [node("age", ">=", 19), node("age", "<=", 39)]}


def test_real_or_of_lower_and_upper_bound_is_kept():
    tree = structure.build_eligibility([[age("<=", 20), age(">=", 60)]], True)

    assert tree == {"and": [{"or": [node("age", "<=", 20), node("age", ">=", 60)]}]}


def test_or_of_different_fields_is_kept():
    groups = [
        [
            cond("career_years", ">=", number_value=2),
            cond("certificates", "contains_any", list_value=["위생사"]),
        ]
    ]

    assert structure.build_eligibility(groups, True) == {
        "and": [{"or": [node("career_years", ">=", 2), node("certificates", "contains_any", ["위생사"])]}]
    }


def test_unsupported_keeps_original_text():
    groups = [
        [cond("unsupported", "==", text_value="[제외] 사행업종")],
        [cond("unsupported", "==", text_value="서울 소재")],
    ]

    assert structure.build_eligibility(groups, True) == {
        "and": [node("unsupported", "==", "[제외] 사행업종"), node("unsupported", "==", "서울 소재")]
    }


def test_certificate_must_be_standard_names_or_becomes_unsupported():
    ok = [[cond("certificates", "contains_all", list_value=list(STANDARD_NAMES[:2]))]]
    bad = [[cond("certificates", "contains_any", list_value=["없는자격증"], evidence="없는자격증 보유자")]]

    assert structure.build_eligibility(ok, True) == {
        "and": [node("certificates", "contains_all", list(STANDARD_NAMES[:2]))]
    }
    assert structure.build_eligibility(bad, True) == {"and": [node("unsupported", "==", "없는자격증 보유자")]}


def test_industry_category_must_be_one_of_three():
    ok = [[cond("industry_category", "==", text_value="외식업")]]
    bad = [[cond("industry_category", "==", text_value="제조업", evidence="제조업체")]]

    assert structure.build_eligibility(ok, True) == {"and": [node("industry_category", "==", "외식업")]}
    assert structure.build_eligibility(bad, True) == {"and": [node("unsupported", "==", "제조업체")]}


@pytest.mark.parametrize(
    "bad",
    [
        cond("age", "contains_any", number_value=3, evidence="나이 이상한 연산자"),  # 필드와 연산자가 안 맞음
        cond("age", "<=", evidence="나이 숫자 없음"),  # 값이 없음
        cond("age", "<=", number_value="39", evidence="나이 글자 값"),  # 값 타입이 틀림
        cond("age", "<=", number_value=True, evidence="나이 불린 값"),
        cond("income", "<", number_value=1, evidence="모르는 필드"),
        cond("industry_code", "==", text_value="CS100001", evidence="쓰지 않는 필드"),
    ],
)
def test_condition_that_fails_validation_becomes_unsupported_with_its_evidence(bad):
    tree = structure.build_eligibility([[bad]], True)

    assert tree == {"and": [node("unsupported", "==", bad["evidence"])]}


# ── 프롬프트 · 스키마 ──────────────────────────────────────────────────


def test_prompt_contains_region_text_districts_and_standard_certificates():
    prompt = structure.build_prompt(notice(raw_text="공고 본문 XYZ", region="전국"), ["용산구", "구로구"])

    assert "[공고 지역 구분] 전국" in prompt and "공고 본문 XYZ" in prompt
    assert "용산구, 구로구" in prompt and STANDARD_NAMES[0] in prompt


def test_prompt_template_has_no_unfilled_placeholder():
    prompt = structure.build_prompt(notice(raw_text="{region} 같은 글자가 본문에 있어도 된다"), ["용산구"])

    assert "{districts}" not in prompt and "{certs}" not in prompt and "{text}" not in prompt


def test_schema_allows_district_names_and_null_and_requires_every_key():
    schema = structure.response_schema(["용산구", "구로구"])

    assert schema["properties"]["district_name"]["enum"] == ["용산구", "구로구", None]
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


# ── 공고 여러 건 ───────────────────────────────────────────────────────


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(structure.time, "sleep", sleeps.append)
    return sleeps


def test_structure_notices_calls_llm_per_notice_and_keeps_order(no_sleep):
    seen = []

    def generate(prompt, schema):
        seen.append(schema["properties"]["district_name"]["enum"])
        return result(amount_max=len(seen))

    out = structure.structure_notices([notice(title="가"), notice(title="나")], CODES, generate)

    assert [(p["name"], p["amount_max"]) for p in out] == [("가", 1), ("나", 2)]
    assert seen[0] == ["용산구", "구로구", None] and no_sleep == [structure.CALL_SLEEP_SEC] * 2


def test_structure_notices_skips_notice_when_llm_is_unavailable(no_sleep):
    def generate(prompt, schema):
        if "건너뜀" in prompt:
            raise LLMUnavailable("응답 없음")
        return result()

    out = structure.structure_notices(
        [notice(title="가"), notice(raw_text="건너뜀"), notice(title="다")], CODES, generate
    )

    assert [p["name"] for p in out] == ["가", "다"]  # 응답 없는 공고만 빠진다


def test_structure_notices_stops_on_configuration_error(no_sleep):
    def generate(prompt, schema):
        raise LLMError("HTTP 404")

    with pytest.raises(LLMError):
        structure.structure_notices([notice()], CODES, generate)


@pytest.mark.parametrize("bad", [[], "글자", None, 5])
def test_structure_notices_skips_response_that_is_not_an_object(no_sleep, bad):
    assert structure.structure_notices([notice()], CODES, lambda p, s: bad) == []


@pytest.mark.parametrize("bad", [["용산구"], {"a": 1}, 5])
def test_non_string_district_name_is_none(bad):
    assert program(result(district_name=bad, district_evidence="용산구 거주"))["district_code"] is None


def test_structure_notices_of_nothing_is_empty(no_sleep):
    assert structure.structure_notices([], CODES, lambda p, s: result()) == []


# ── upsert_programs와 이어지는지 ───────────────────────────────────────


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def test_structured_programs_can_be_upserted_and_stay_unverified(conn, no_sleep):
    res = result(
        amount_max=5_000_000,
        district_name="구로구",
        district_evidence="구로구 거주 청년",
        conditions=[[age(">=", 19), age("<=", 39)], [cond("unsupported", "==", text_value="서울시 거주")]],
    )
    programs = structure.structure_notices(
        [notice(raw_text="신청대상 구로구 거주 청년 안내")], {**CODES, "종로구": "11110"}, lambda p, s: res
    )
    before = conn.execute(text("SELECT count(*) FROM support_program")).scalar_one()

    assert upsert_programs(conn, programs) == 1

    row = (
        conn.execute(
            text("SELECT * FROM support_program WHERE source_url = :u"), {"u": programs[0]["source_url"]}
        )
        .mappings()
        .one()
    )
    assert conn.execute(text("SELECT count(*) FROM support_program")).scalar_one() == before + 1
    assert (row["amount_max"], row["district_code"], row["verified_by"]) == (5_000_000, "11530", None)
    assert row["eligibility"] == {
        "and": [node("age", ">=", 19), node("age", "<=", 39), node("unsupported", "==", "서울시 거주")]
    }
