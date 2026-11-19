"""인허가 전처리(batch.pipeline.licenses)의 business_license 추가 칼럼 규칙."""

from decimal import Decimal

import pytest

from batch.pipeline.licenses import CORE_COLUMNS, DB_COLUMNS, EXTRA_COLUMNS, _schema, area_sqm, extra_values


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("29.70", Decimal("29.70")),
        (" 133.560000000 ", Decimal("133.56")),
        ("15", Decimal("15.00")),
        (".00", None),
        ("0E-9", None),
        ("0", None),
        ("-3", None),
        ("", None),
        (None, None),
        ("abc", None),
        ("NaN", None),
    ],
)
def test_area_sqm(raw, expected):
    assert area_sqm(raw) == expected


def test_area_falls_back_in_order():
    assert extra_values({"소재지면적": "", "시설면적": "0E-9", "시설총규모": "28.9"})[1] == Decimal("28.90")
    assert extra_values({"소재지면적": "10", "시설면적": "20", "시설총규모": "30"})[1] == Decimal("10.00")
    assert extra_values({"시설면적": "126.02"})[1] == Decimal("126.02")  # 노래방 · PC방
    assert extra_values({"소재지면적": ".00", "시설총규모": "0"})[1] is None


def test_name_address_multi_use():
    name, _, address, multi_use = extra_values(
        {"사업장명": " 어부바 ", "도로명주소": "", "지번주소": "서울특별시 성동구 송정동 73-921",
         "다중이용업소여부": "Y"}
    )
    assert (name, address, multi_use) == ("어부바", "서울특별시 성동구 송정동 73-921", True)
    assert extra_values({"도로명주소": "서울 도로명", "지번주소": "서울 지번"})[2] == "서울 도로명"
    assert extra_values({"다중이용업소여부": "N"})[3] is False
    assert extra_values({"다중이용업소여부": ""})[3] is None
    assert extra_values({})[3] is None  # 칼럼이 없는 업종(노래방 · PC방 · 체력단련장)
    assert extra_values({"사업장명": "  "})[0] is None


def test_name_and_address_keep_source_text():
    row = {"사업장명": "첼로Ⅱ노래연습장", "도로명주소": "서울 대명길 22  (명륜2가)"}
    name, _, address, _ = extra_values(row)
    assert (name, address) == ("첼로Ⅱ노래연습장", "서울 대명길 22  (명륜2가)")


def test_db_columns_line_up():
    assert DB_COLUMNS == CORE_COLUMNS + EXTRA_COLUMNS
    assert len(extra_values({})) == len(EXTRA_COLUMNS)


def test_parquet_schema_matches_db_columns():
    pytest.importorskip("pyarrow")  # 기본 CI에는 없다. ci-batch가 돌린다
    assert _schema().names == DB_COLUMNS
