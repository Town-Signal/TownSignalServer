"""자격증 자유 입력 정규화 (전체 명세 7.2, 3장 5번).

1. 앞뒤 공백 · 괄호 표기를 정리한다.
2. 동의어 사전에서 표준명을 찾는다.
3. 없으면 주입받은 매퍼(API의 LLM 폴백)에 "표준 목록 중 하나 또는 없음"을 묻는다.
   common은 LLM을 직접 부르지 않는다. 타임아웃(2초) · 재시도 · 켜고 끄기는 API 쪽 책임이다.
4. 매퍼 실패 · 예외 · 없음 · 표준 목록 밖 값이면 원문을 그대로 쓴다. 제출은 막지 않는다.
5. 입력 순서를 유지하고 중복을 제거한다.

표준명은 support_program.eligibility의 certificates 조건에 쓰는 명칭과 같아야 한다.
배치의 공고 구조화도 이 사전을 쓴다.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# TODO(가정): 임시 사전이다. 실제 표준 목록 · 동의어는 데이터 담당이 확정한다.
# ⑰ GET /api/certificates가 이 목록을 그대로 내린다.
# (표준명, 동의어, 분류)
_CATALOG: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("조리기능사", ("한식조리사", "한식조리기능사"), "조리"),
    ("양식조리기능사", ("양식조리사",), "조리"),
    ("중식조리기능사", ("중식조리사",), "조리"),
    ("일식조리기능사", ("일식조리사",), "조리"),
    ("제과기능사", ("제과사",), "제과제빵"),
    ("제빵기능사", ("제빵사",), "제과제빵"),
    ("바리스타 1급", ("바리스타1급",), "음료"),
    ("바리스타 2급", ("바리스타2급",), "음료"),
    ("위생사", (), "위생"),
    ("미용사(일반)", ("일반미용사", "헤어미용사"), "미용"),
    ("미용사(네일)", ("네일미용사",), "미용"),
    ("미용사(피부)", ("피부미용사",), "미용"),
)

STANDARD_NAMES: tuple[str, ...] = tuple(name for name, _, _ in _CATALOG)

# LLM 폴백 매퍼: (정리된 입력, 표준명 목록) → 표준명 하나 또는 None
Mapper = Callable[[str, tuple[str, ...]], str | None]


@dataclass(frozen=True)
class CertificateEntry:
    name: str
    aliases: tuple[str, ...]
    category: str | None


@dataclass(frozen=True)
class NormalizeResult:
    certificates: tuple[str, ...]  # normalized_certificates. 입력 순서 유지 · 중복 제거
    # 표준명으로 바꾸지 못하고 원문을 쓴 항목(CERTIFICATE_NOT_RECOGNIZED 경고용)
    unrecognized: tuple[str, ...]


_SPACES = re.compile(r"\s+")


def clean(text: str) -> str:
    """앞뒤 · 연속 공백, 전각 괄호, 괄호 안팎 공백을 정리한다. 예: ' 미용사 （ 일반 ） ' → '미용사(일반)'."""
    text = text.replace("（", "(").replace("）", ")")
    text = _SPACES.sub(" ", text.strip())
    text = re.sub(r"\s*\(\s*", "(", text)
    return re.sub(r"\s*\)", ")", text)


def _key(text: str) -> str:
    """비교 키: 공백을 모두 빼고 소문자로. '바리스타2급' == '바리스타 2급'."""
    return _SPACES.sub("", clean(text)).lower()


_LOOKUP: dict[str, str] = {}
for _name, _aliases, _ in _CATALOG:
    for _word in (_name, *_aliases):
        _LOOKUP[_key(_word)] = _name


def lookup(text: str) -> str | None:
    """사전에서 표준명을 찾는다. 없으면 None."""
    return _LOOKUP.get(_key(text))


def catalog() -> tuple[CertificateEntry, ...]:
    """⑰ 표준 자격증 목록 응답용."""
    return tuple(CertificateEntry(name, aliases, category) for name, aliases, category in _CATALOG)


def _ask_mapper(mapper: Mapper, text: str) -> str | None:
    try:
        answer = mapper(text, STANDARD_NAMES)
    except Exception:  # 타임아웃 · 네트워크 오류 등. 제출을 막지 않는다
        return None
    return answer if answer in STANDARD_NAMES else None


def normalize(items: Iterable[str], mapper: Mapper | None = None) -> NormalizeResult:
    result: list[str] = []
    unrecognized: list[str] = []
    for raw in items:
        text = clean(raw)
        if not text:
            continue
        standard = lookup(text)
        if standard is None and mapper is not None:
            standard = _ask_mapper(mapper, text)
        if standard is None:
            unrecognized.append(text)
        value = standard or text
        if value not in result:
            result.append(value)
    return NormalizeResult(certificates=tuple(result), unrecognized=tuple(dict.fromkeys(unrecognized)))
