"""지원사업 자격 판정 (전체 명세 7.3).

support_program.eligibility(JSONB 조건 트리)를 재귀로 평가한다.
프레임워크와 DB에 의존하지 않는 순수 함수만 둔다. 배치(공고 검증)와 API(요청 시 매칭)가 같은 함수를 부른다.

    노드  := 그룹 | 조건
    그룹  := { "and": [노드, ...] } | { "or": [노드, ...] }
    조건  := { "field": 필드명, "op": 연산자, "value": 값 }

알 수 없는 필드 · 연산자, 타입 불일치, 모양이 잘못된 노드가 하나라도 있으면 그 사업은
**매칭하지 않는다**(보수적 판정). or 안에 있어도 마찬가지이며 경고 로그를 남긴다.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Applicant:
    """입력 조건. certificates는 정규화(certificate_normalizer) 후 값이다."""

    age: int
    career_years: int = 0
    capital: int = 0
    certificates: tuple[str, ...] = ()
    industry_code: str | None = None
    industry_category: str | None = None  # industry.category (외식업 · 서비스업 · 소매업)


@dataclass(frozen=True)
class Program:
    """support_program 한 행에서 판정에 필요한 값만."""

    program_id: int
    name: str
    amount_max: int
    eligibility: Mapping[str, Any]
    is_exclusive: bool = False  # 중복 수혜 불가
    district_code: str | None = None  # None = 전국 사업
    verified_by: str | None = None  # None이면 검수 전이라 매칭에 쓰지 않는다
    apply_end: date | None = None  # 매칭에는 쓰지 않고 화면 표시용으로만 내린다


class _Unmatchable(Exception):
    """트리를 해석할 수 없음 → 사업 전체를 매칭하지 않는다."""


# 필드 → 값의 종류 (가정, 7.3)
_NUMBER_FIELDS = {"age", "career_years", "capital"}
_LIST_FIELDS = {"certificates"}
_STRING_FIELDS = {"industry_code", "industry_category"}

_NUMBER_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _condition(node: Mapping[str, Any], applicant: Applicant) -> bool:
    field, op, value = node.get("field"), node.get("op"), node.get("value")

    if field in _NUMBER_FIELDS:
        if op not in _NUMBER_OPS or not _is_int(value):
            raise _Unmatchable(f"수치 조건 해석 불가: {node}")
        return _NUMBER_OPS[op](getattr(applicant, field), value)

    if field in _LIST_FIELDS:
        if not _is_str_list(value):
            raise _Unmatchable(f"목록 조건 값이 문자열 목록이 아님: {node}")
        held = set(applicant.certificates)
        if op == "contains_any":
            return any(v in held for v in value)
        if op == "contains_all":
            return all(v in held for v in value)
        raise _Unmatchable(f"목록 연산자 해석 불가: {node}")

    if field in _STRING_FIELDS:
        actual = getattr(applicant, field)
        if op == "==" and isinstance(value, str):
            return actual == value
        if op == "in" and _is_str_list(value):
            return actual in value
        raise _Unmatchable(f"문자열 조건 해석 불가: {node}")

    raise _Unmatchable(f"알 수 없는 필드: {node}")


def _node(node: Any, applicant: Applicant) -> bool:
    if not isinstance(node, Mapping):
        raise _Unmatchable(f"노드가 객체가 아님: {node!r}")
    if set(node) == {"and"} or set(node) == {"or"}:
        (kind, children), = node.items()
        if not isinstance(children, list):
            raise _Unmatchable(f"{kind}의 값이 목록이 아님: {node}")
        # 결과가 정해져도 끝까지 해석한다. 뒤쪽에 잘못된 노드가 있으면 사업 전체를 매칭하지 않기 위해서다
        results = [_node(child, applicant) for child in children]
        return all(results) if kind == "and" else any(results)  # 빈 and 참, 빈 or 거짓
    if set(node) == {"field", "op", "value"}:
        return _condition(node, applicant)
    raise _Unmatchable(f"노드 모양이 잘못됨: {node}")


def evaluate(eligibility: Any, applicant: Applicant) -> bool:
    """조건 트리가 참이면 True. 해석할 수 없는 트리는 False(매칭하지 않음)."""
    try:
        return _node(eligibility, applicant)
    except _Unmatchable as exc:
        logger.warning("eligibility 해석 불가 — 매칭하지 않음: %s", exc)
        return False


def applies_to(program: Program, district_code: str) -> bool:
    """해당 자치구에서 받을 수 있는 사업인지. district_code가 None이면 전국 사업이다."""
    return program.district_code is None or program.district_code == district_code


def is_matchable(program: Program) -> bool:
    """검수를 통과한 공고만 매칭한다(verified_by IS NOT NULL, 4.8). 마감 공고도 포함한다."""
    return program.verified_by is not None
