"""common.certificate_normalizer — 사전 매핑 · LLM 폴백 주입 · 순서 유지 · 중복 제거 (명세 7.2)."""

import pytest

from common.certificate_normalizer import STANDARD_NAMES, catalog, clean, lookup, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  조리기능사  ", "조리기능사"),
        ("바리스타   2급", "바리스타 2급"),
        ("미용사 （ 일반 ）", "미용사(일반)"),
        ("미용사 (네일)", "미용사(네일)"),
    ],
)
def test_clean(raw, expected):
    assert clean(raw) == expected


@pytest.mark.parametrize(
    ("raw", "standard"),
    [
        ("한식조리기능사", "조리기능사"),
        ("한식조리사", "조리기능사"),
        ("바리스타2급", "바리스타 2급"),
        ("바리스타 2 급", "바리스타 2급"),
        ("미용사 (일반)", "미용사(일반)"),
        ("헤어미용사", "미용사(일반)"),
    ],
)
def test_lookup_synonyms(raw, standard):
    assert lookup(raw) == standard


def test_normalize_keeps_order_and_removes_duplicates():
    result = normalize(["바리스타2급", "한식조리사", "바리스타 2급", "조리기능사"])
    assert result.certificates == ("바리스타 2급", "조리기능사")
    assert result.unrecognized == ()


def test_unknown_without_mapper_keeps_original():
    result = normalize(["  드론 조종사  ", "조리기능사"])
    assert result.certificates == ("드론 조종사", "조리기능사")
    assert result.unrecognized == ("드론 조종사",)


def test_mapper_is_called_only_for_unknown_items():
    calls = []

    def mapper(text, standards):
        calls.append(text)
        assert standards == STANDARD_NAMES
        return "제빵기능사" if text == "빵 자격증" else None

    result = normalize(["조리기능사", "빵 자격증", "드론 조종사"], mapper)
    assert calls == ["빵 자격증", "드론 조종사"]
    assert result.certificates == ("조리기능사", "제빵기능사", "드론 조종사")
    assert result.unrecognized == ("드론 조종사",)


@pytest.mark.parametrize(
    "mapper",
    [
        lambda text, standards: (_ for _ in ()).throw(TimeoutError("2초 초과")),  # 예외
        lambda text, standards: None,  # 없음
        lambda text, standards: "표준 목록에 없는 이름",  # 목록 밖 값
    ],
    ids=["raises", "none", "outside-list"],
)
def test_mapper_failure_falls_back_to_original(mapper):
    result = normalize(["빵 자격증"], mapper)
    assert result.certificates == ("빵 자격증",)
    assert result.unrecognized == ("빵 자격증",)


def test_blank_items_are_dropped():
    assert normalize(["   ", ""]).certificates == ()


def test_catalog_matches_standard_names():
    entries = catalog()
    assert tuple(e.name for e in entries) == STANDARD_NAMES
    cook = next(e for e in entries if e.name == "조리기능사")
    assert cook.aliases == ("한식조리사", "한식조리기능사") and cook.category == "조리"
    assert all(lookup(alias) == e.name for e in entries for alias in e.aliases)
