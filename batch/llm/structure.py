"""공고 구조화: 수집한 공고(RawNotice) → upsert_programs가 받는 딕셔너리(명세 4.8 · 6.6 LLM ①).

LLM은 amount_max · 대상 구 · 중복 수혜 불가 · 신청 자격 조건만 뽑는다.
이름 · 기관 · 접수 기간 · 주소 · 원문은 수집한 값을 그대로 쓴다.
모호한 요건은 해석하지 않고 field = "unsupported"에 원문 문구로 남긴다. 평가기는 모르는 필드가 하나라도 있으면
그 사업을 매칭하지 않으므로(common/evaluator) 검수자가 고치기 전까지 매칭에서 빠진다.
LLM 호출은 batch.llm.client에 모여 있어서 테스트에서는 가짜로 바꾼다.
"""

import hashlib
import time
from collections.abc import Callable, Mapping
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from batch.llm.client import LLMUnavailable, generate_json
from common.certificate_normalizer import STANDARD_NAMES

if TYPE_CHECKING:
    from batch.jobs.build_support_program import RawNotice

CALL_SLEEP_SEC = 0.5  # 공고마다 호출한 뒤 쉬는 시간
NUMBER_FIELDS = ("age", "career_years", "capital")
NUMBER_OPS = ("==", "!=", "<", "<=", ">", ">=")
LIST_OPS = ("contains_any", "contains_all")
CATEGORIES = ("외식업", "서비스업", "소매업")
FIELDS = (*NUMBER_FIELDS, "certificates", "industry_category", "industry_code", "unsupported")
LOWER_OPS, UPPER_OPS = (">", ">="), ("<", "<=")
# 대상이 그 구에 있다는 말
REGION_WORDS = (
    "거주",
    "소재",
    "관내",
    "주민",
    "구민",
    "주소지",
    "사업장",
    "창업 예정",
    "창업예정",
    "에서 창업",
)

PROMPT = """너는 정부·지자체 창업 지원사업 공고에서 지원금 정보와 신청 자격을 뽑는 도구다.
공고 원문을 읽고 아래 규칙대로만 JSON을 출력한다. 원문에 없는 것은 추측하지 않는다. 애매하면 해석하지 말고 원문 문구를 그대로 둔다.

[공고 지역 구분] {region}  (전국 = 전국 대상 공고, 서울 = 서울 대상 공고)

## 출력 항목
1. amount_max (정수, 원 단위)
 - 예비창업자의 창업을 돕기 위해 신청자 1건(1개 기업·1인)에게 주는 창업 지원금(보조금·지원금)의 최대 금액만 쓴다. 사업 전체 예산·총사업비·총지원규모는 금액이 아니다.
 - 금액이 범위면 상한. "1천만 원" → 10000000, "2억" → 200000000.
 - 창업 지원금이 아니면 금액이 적혀 있어도 0이다. 융자·보증·이자 지원(이차보전), 경진대회·공모전 상금·포상금, 연구개발비,
   단가표·기준 단가, 이미 운영 중인 기업의 수출·출장·판로·인력 등 사업 활동 비용 지원이 그렇다.
 - 연구개발비는 R&D 과제·기술개발 사업·연구거점·공동연구실에서 과제별로 주는 정부출연금·연구비·과제비다. 주관기관·참여기관·연구책임자·과제 선정·컨소시엄 같은 말이 나오고
   "과제 별 ○억원", "정부지원연구개발비 ○백만원"처럼 적혀 있어도 창업 지원금이 아니므로 0이다. 공고 자체가 연구개발 과제 공모(신규과제·재공모·기술개발)이면 금액은 모두 0이다.
 - 현금성 금액이 원문에 분명하지 않으면(교육·입주·컨설팅·행사 참가 등) 0.
2. district_name, district_evidence
 - 신청 대상이 서울의 특정 한 개 자치구에 거주하거나 그 구에 소재(사업장·본사)한다고, 또는 그 구에서 창업할 예정이라고 원문에 분명히 적힌 경우만 그 구 이름(아래 목록 중 하나).
 - 공고를 낸 기관·센터·학교가 어느 구에 있다는 사실은 대상 지역이 아니다. 예: "서울창업센터 동작", "○○구 청년창업지원센터", "○○대학교 창업보육센터"가 운영해도
   신청 대상이 "서울시 거주"·"서울시 소재"이거나 지역 제한이 없으면 district_name은 null이다.
 - district_evidence: 그 구에 거주·소재해야 한다고 적힌 원문 문구를 구 이름을 포함해 그대로 옮긴다. district_name이 null이면 빈 문자열.
 - 서울 전역, 전국, 여러 구, 애매하면 null. 지역 구분이 전국이면 항상 null.
 - 허용 구 이름: {districts}
3. is_exclusive (참/거짓)
 - 다른 지원사업과의 중복 수혜(중복 지원·중복 참여)가 불가하다고 원문에 명시된 경우만 true. 아니면 false.
4. conditions: 신청 자격 조건 목록. 바깥 배열 = AND(모두 만족), 안쪽 배열 = OR(하나만 만족하면 됨). 대부분 안쪽 배열은 조건 하나다.
 각 조건은 field, op, number_value, text_value, list_value, evidence로 쓴다.
 - field는 다음 중 하나:
   age: 신청자 만 나이. op는 ==, !=, <, <=, >, >= 중 하나, number_value 정수. 예 "만 39세 이하" → <=, 39. "청년"처럼 숫자 없이 표현만 있으면 age가 아니라 unsupported.
   career_years: 동종업계 경력 연수(창업한 지 얼마나 됐는지가 아님). op 숫자 비교, number_value 정수.
   capital: 신청자의 자본금(원). op 숫자 비교, number_value 정수.
   certificates: 요구하는 자격증. op는 contains_any(하나라도) 또는 contains_all(모두), list_value는 아래 표준 자격증 이름 목록 중에서만.
   industry_category: 업종 대분류. op는 ==, text_value는 외식업, 서비스업, 소매업 중 하나. 공고가 그 대분류 전체를 대상으로 할 때만 쓴다.
     예: "음식점업·외식업" → 외식업, "소매업·도소매업·판매업" → 소매업, "서비스업" → 서비스업.
   industry_code: 업종 소분류. op는 ==, text_value는 아래 업종 이름 목록 중 하나. 공고가 소분류 몇 개로 한정할 때 쓴다.
     예: "미용업" → 미용실·네일숍·피부관리실을 각각 industry_code로 쓰고 한 안쪽 배열(OR)에 묶는다. "PC방 또는 노래방" → [[PC방, 노래방]].
     업종이 위 대분류와 아래 목록 어디에도 맞지 않으면(제조업, IT·소프트웨어, 농림어업, 건설, 운송 등) unsupported.
   unsupported: 위에 정확히 맞지 않는 모든 자격 조건(창업 업력·창업 연차, 거주지, 기업 형태·규모, 사업자등록·법인 여부, 특정 분야·기술, 재직·재학 등, 표준 목록에 없는 자격증, 숫자 없는 "청년" 등).
     op는 ==, text_value에 해당 조건 원문 문구를 요약·바꿔 쓰기 없이 그대로 잘라 옮긴다.
 - 이 서비스의 사용자는 아직 창업하지 않은 예비창업자다. 그래서 다음처럼 conditions에 쓰지 않는 조건이 있다.
   · 예비창업자가 대상에 포함된다고 분명한 창업 단계 조건("예비창업자", "예비창업자 포함", "창업 예정자", "창업 준비 중")은 이미 충족한 것으로 보고 쓰지 않는다.
   · 사업장·창업 예정 지역 조건("○○구에서 창업 예정", "○○구 소재 사업장", "서울 소재")은 쓰지 않는다. 특정 한 개 구면 district_name·district_evidence로만 나타낸다.
   · 이미 창업한 사람만 받을 수 있는 조건(사업자등록 필수, 창업 N년 이상·업력 N년 이내, 법인 설립 완료)은 예비창업자가 충족할 수 없으니 unsupported로 쓴다.
   · 거주지 조건("○○구 N개월 이상 거주", "서울시 거주", "○○구 주민")은 unsupported로 쓰고 text_value를 "[거주] "로 시작한다.
 - 신청 제외 대상(제외 조건)은 "이것에 해당하면 안 된다"는 뜻이므로 각각을 별개의 바깥 배열 항목(AND)으로 쓴다. 제외 조건끼리 한 안쪽 배열(OR)에 묶지 않는다.
   field는 unsupported, text_value는 "[제외] "로 시작하고 원문 문구를 그대로 옮긴다.
   신청자는 예비창업자다: 개인이고 아직 창업 전이라 사업자등록·법인·직원·매출·업력·기업 규모·수출 실적·인증·수상이 없고, 성실한 일반인이라 체납·채무불이행·제재 같은 일반 결격사유도 없다고 본다.
   예비창업자에게 해당할 가능성이 없는 제외 대상은 이미 충족한 것으로 보고 쓰지 않는다. 제외 목록이 길어도 해당될 수 있는 것만 쓰고, 해당될 수 있는 것이 없으면 하나도 쓰지 않는다.
   쓰지 않는 예: 휴업·폐업 중인 기업, 대기업·기업 규모 제한, 사업자등록·법인·업력 기반 제외, 국세·지방세 체납, 금융기관 채무불이행·신용불량,
   정부사업 참여제한·제재, 부정수급·허위서류 이력, 이미 지원받은 이력, 아래 업종 이름에 없는 업종(유흥주점, 금융·보험업, 제조업, 사행업 등)의 제외.
   쓰는 예: 신청자 개인의 신분·속성에 따른 제외(공무원·공공기관 임직원, 재직·재학·휴학 중인 사람, 특정 연령)와 아래 업종 이름에 있는 업종의 제외.
   한 문장에 여러 제외가 섞여 있으면("휴·폐업 중이거나 체납 중인 업체") 항목별로 따져서 쓸 것만 쓴다. 전부 쓰지 않는 것이면 그 문장은 버린다.
 - 범위(예: "19~39세", "만 20세 이상 39세 이하")는 하한과 상한을 별개의 바깥 배열 항목 두 개(AND)로 쓴다. 같은 필드의 하한과 상한을 한 안쪽 배열(OR)에 넣지 않는다.
   안쪽 배열(OR)에는 원문에 "또는", "중 하나"처럼 대안이 명시된 조건만 넣는다.
   예시1) 원문 "만 19세~39세" → conditions: [[age >= 19], [age <= 39]]
   예시2) 원문 "경력 2년 이상 또는 조리기능사 보유" → conditions: [[career_years >= 2, certificates contains_any [조리기능사]]]
 - 조건의 evidence에는 근거 원문 문구를 쓴다.
 - 신청 자격 조건이 원문에서 전혀 찾아지지 않거나 판단이 안 되면 conditions는 빈 배열이고 conditions_known은 false.
   "누구나 신청 가능" 등 조건이 없음이 원문에 분명하면 conditions는 빈 배열이고 conditions_known은 true.
   구직자·학생·재직자·특정 분야 종사자처럼 신청 대상이 한정되어 있으면 빈 배열로 두지 말고 unsupported 조건으로 적는다.
   예: "스타트업 취업·이직에 관심 있는 구직자를 대상" → conditions_known은 true, conditions는 [[unsupported "스타트업 취업·이직에 관심 있는 구직자"]].
 - 상세 페이지 머리말의 "대상연령", "창업업력", "대상" 항목은 사이트가 분류한 값이므로 신청 자격으로 쓰지 않는다. 공고 본문(신청대상·모집대상 등)에 적힌 조건만 쓴다.
 - 표준 자격증 이름: {certs}
 - 업종 이름(소분류): {industries}

## 공고 원문
{text}
"""  # noqa: E501  프롬프트는 한 줄에 한 규칙이 읽기 쉽다


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def custom_id(notice: "RawNotice") -> str:
    """Batch 요청의 꼬리표: 어느 공고에 대한 요청인지(주소 지문 16자).

    결과가 돌아오면 이번 주 공고 중 같은 꼬리표의 공고와 짝짓는다. 주소가 적재 키이고 같은 주소 공고의
    수정은 따라가지 않으므로 원문은 꼬리표에 넣지 않는다.
    """
    return _digest(notice.source_url)


def build_prompt(
    notice: "RawNotice", district_names: list[str], industries: Mapping[str, str] | None = None
) -> str:
    """industries는 {업종 소분류 이름: 코드}(industry 표)다. 이름만 프롬프트에 넣는다."""
    return PROMPT.format(
        region=notice.region,
        districts=", ".join(district_names),
        certs=", ".join(STANDARD_NAMES),
        industries=", ".join(industries or {}),
        text=notice.raw_text,
    )


def response_schema(district_names: list[str]) -> dict[str, Any]:
    """LLM 출력 형식(표준 JSON Schema). 제공사에 의존하지 않는다."""
    condition = {
        "type": "object",
        "properties": {
            "field": {"type": "string", "enum": list(FIELDS)},
            "op": {"type": "string", "enum": [*NUMBER_OPS, *LIST_OPS]},
            "number_value": {"type": ["integer", "null"]},
            "text_value": {"type": ["string", "null"]},
            "list_value": {"type": ["array", "null"], "items": {"type": "string"}},
            "evidence": {"type": "string"},
        },
        "required": ["field", "op", "evidence"],
        "additionalProperties": False,
    }
    properties = {
        "amount_max": {"type": "integer"},
        "district_name": {"type": ["string", "null"], "enum": [*district_names, None]},
        "district_evidence": {"type": "string"},
        "is_exclusive": {"type": "boolean"},
        "conditions_known": {"type": "boolean"},
        "conditions": {"type": "array", "items": {"type": "array", "items": condition}},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unsupported(cond: Mapping[str, Any]) -> dict[str, Any]:
    """검증을 통과하지 못한 조건은 원문 문구로 남겨 검수자가 보게 한다."""
    return {
        "field": "unsupported",
        "op": "==",
        "value": cond.get("evidence") or cond.get("text_value") or "(내용 없음)",
    }


def _condition(cond: Mapping[str, Any], industries: Mapping[str, str]) -> dict[str, Any]:
    field, op = cond.get("field"), cond.get("op")
    number, text, items = cond.get("number_value"), cond.get("text_value"), cond.get("list_value")
    if field in NUMBER_FIELDS and op in NUMBER_OPS and _is_int(number):
        return {"field": field, "op": op, "value": number}
    if field == "certificates" and op in LIST_OPS and items and all(i in STANDARD_NAMES for i in items):
        return {"field": field, "op": op, "value": list(items)}
    if field == "industry_category" and op == "==" and text in CATEGORIES:
        return {"field": field, "op": op, "value": text}
    if field == "industry_code" and op == "==" and text in industries:
        return {"field": field, "op": op, "value": industries[text]}
    if field == "unsupported" and text:
        return {"field": "unsupported", "op": "==", "value": text}
    return _unsupported(cond)


def _groups(raw: Any) -> list[list[Mapping[str, Any]]]:
    """LLM이 준 conditions에서 조건이 아닌 것을 걸러 낸다."""
    if not isinstance(raw, list):
        return []
    groups = [[c for c in g if isinstance(c, Mapping)] for g in raw if isinstance(g, list)]
    return [g for g in groups if g]


def _split_always_true(groups: list[list[Mapping[str, Any]]]) -> list[list[Mapping[str, Any]]]:
    """한 OR 묶음이 같은 숫자 필드의 하한 + 상한뿐이고 하한 <= 상한이면 항상 참이라 AND 의도로 보고 나눈다.

    예: age >= 19 OR age <= 39 → age >= 19 AND age <= 39.
    하한 > 상한(age <= 20 OR age >= 60)은 진짜 OR이라 둔다.
    """
    result: list[list[Mapping[str, Any]]] = []
    for group in groups:
        fields = {c.get("field") for c in group}
        if len(group) >= 2 and len(fields) == 1 and fields <= set(NUMBER_FIELDS):
            lower = [
                c["number_value"]
                for c in group
                if c.get("op") in LOWER_OPS and _is_int(c.get("number_value"))
            ]
            upper = [
                c["number_value"]
                for c in group
                if c.get("op") in UPPER_OPS and _is_int(c.get("number_value"))
            ]
            if lower and upper and min(lower) <= max(upper):
                result += [[c] for c in group]
                continue
        result.append(group)
    return result


def build_eligibility(
    raw_conditions: Any, known: bool, industries: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """LLM 조건 → 7.3 조건 트리. 조건이 없을 때 known이면 누구나(빈 and), 아니면 아무도 아님(빈 or).

    industries는 {업종 소분류 이름: 코드}다. 없거나 목록에 없는 업종 이름은 unsupported가 된다.
    """
    groups = _split_always_true(_groups(raw_conditions))
    nodes = []
    for group in groups:
        conds = [_condition(c, industries or {}) for c in group]
        nodes.append(conds[0] if len(conds) == 1 else {"or": conds})
    # 조건 목록이 정확히 빈 리스트일 때만 "조건 없음"이다. 깨진 응답은 아무도 매칭하지 않게 한다
    if not nodes:
        return {"and": []} if known and raw_conditions == [] else {"or": []}
    return {"and": nodes}


def _nearly_in(quote: str, raw_text: str) -> bool:
    """quote가 원문에 거의 그대로 있다. 조사 · 띄어쓰기를 조금 바꿔도 80% 이상 이어서 같으면 인정한다."""
    text = " ".join(raw_text.split())
    match = SequenceMatcher(None, text, quote, autojunk=False).find_longest_match(0, len(text), 0, len(quote))
    return match.size >= 0.8 * len(quote)


def _district_code(
    notice: "RawNotice", result: Mapping[str, Any], district_codes: Mapping[str, str]
) -> str | None:
    """LLM이 고른 구를 근거 문구로 확인한 뒤 코드로 바꾼다. 확인하지 못하면 None(구 지정 없음)이다.

    근거 문구가 ① 원문에 (거의 그대로) 실제로 있고 ② 구 이름을 담고 ③ 거주 · 소재 같은 말을 함께 담아야 한다.
    기관이 있는 구를 대상 지역으로 잘못 고른 경우는 이런 문구가 없어 걸러진다.
    """
    name, evidence = result.get("district_name"), result.get("district_evidence")
    if not isinstance(name, str) or not isinstance(evidence, str):
        return None
    quote = " ".join(evidence.split())
    if name not in quote or not any(word in quote for word in REGION_WORDS):
        return None
    if not _nearly_in(quote, notice.raw_text):
        return None
    return district_codes.get(name)


def to_program(
    notice: "RawNotice",
    result: Mapping[str, Any],
    district_codes: Mapping[str, str],
    industries: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """LLM 결과 + 수집 값 → upsert_programs 입력 한 건."""
    amount = result.get("amount_max")
    if notice.district_code:  # 수작업 공고는 구가 이미 정해져 있다
        district = notice.district_code
    elif notice.region == "전국":
        district = None
    else:
        district = _district_code(notice, result, district_codes)
    return {
        "name": notice.title,
        "agency": notice.agency,
        "source_url": notice.source_url,
        "raw_text": notice.raw_text,
        "apply_start": notice.apply_start,
        "apply_end": notice.apply_end,
        "amount_max": amount if _is_int(amount) and amount >= 0 else 0,
        "district_code": district,
        "is_exclusive": result.get("is_exclusive") is True,
        "eligibility": build_eligibility(
            result.get("conditions"), result.get("conditions_known") is True, industries
        ),
    }


def structure_notices(
    notices: list["RawNotice"],
    district_codes: Mapping[str, str],
    generate: Callable[[str, dict[str, Any]], dict[str, Any]] = generate_json,
    industries: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """공고마다 LLM을 불러 upsert_programs 입력 목록을 만든다. district_codes는 {구 이름: 구 코드}다.

    LLM이 계속 응답하지 않거나 응답을 읽을 수 없는 공고는 건너뛴다. 키 · 요청 · 모델 문제(LLMError)는
    모든 공고에서 되풀이되므로 건너뛰지 않고 그대로 올린다.
    """
    names = list(district_codes)
    schema = response_schema(names)
    programs = []
    for notice in notices:
        try:
            result = generate(build_prompt(notice, names, industries), schema)
            if not isinstance(result, Mapping):
                raise LLMUnavailable("LLM 응답이 JSON 객체가 아니다")
            programs.append(to_program(notice, result, district_codes, industries))
        except LLMUnavailable:
            pass
        time.sleep(CALL_SLEEP_SEC)
    return programs
