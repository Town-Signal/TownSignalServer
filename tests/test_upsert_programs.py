"""upsert_programs — 같은 공고 찾기 · verified_by 유지 규칙 · 전부 아니면 전무(명세 4.8)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from batch.jobs.build_support_program import upsert_programs

URL = "https://example.test/upsert/1"
ELIG = {"and": [{"field": "age", "op": "<=", "value": 39}]}


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def program(**kw):
    return {"name": "시험 공고", "amount_max": 5_000_000, "eligibility": ELIG, "source_url": URL, **kw}


def rows(conn, where="source_url = :u", **params):
    params = params or {"u": URL}
    return (
        conn.execute(text(f"SELECT * FROM support_program WHERE {where} ORDER BY program_id"), params)
        .mappings()
        .all()
    )


def verify(conn, by="검수자", where="source_url = :u", **params):
    conn.execute(
        text(f"UPDATE support_program SET verified_by = :by WHERE {where}"),
        {"by": by, **(params or {"u": URL})},
    )


def count(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM support_program")).scalar_one()


def test_insert_new_program(conn):
    before = count(conn)

    assert upsert_programs(conn, [program(agency="기관", raw_text="원문", apply_end="2026-12-31")]) == 1

    (row,) = rows(conn)
    assert count(conn) == before + 1
    assert (row["name"], row["agency"], row["amount_max"], row["raw_text"]) == (
        "시험 공고",
        "기관",
        5_000_000,
        "원문",
    )
    assert row["eligibility"] == ELIG and row["is_exclusive"] is False and row["district_code"] is None
    assert row["extracted_at"] is not None and row["verified_by"] is None


def test_update_keeps_verified_by_when_raw_text_same(conn):
    upsert_programs(conn, [program(raw_text="원문")])
    verify(conn)
    conn.execute(
        text("UPDATE support_program SET extracted_at = '2020-01-01' WHERE source_url = :u"), {"u": URL}
    )
    before = count(conn)

    upsert_programs(conn, [program(raw_text="원문", amount_max=7_000_000, is_exclusive=True)])

    (row,) = rows(conn)
    assert count(conn) == before  # 새 행이 생기지 않는다
    assert (row["amount_max"], row["is_exclusive"]) == (7_000_000, True)
    assert row["verified_by"] == "검수자"
    assert row["extracted_at"].year > 2020  # 갱신 시각이 새로 찍힌다


def test_update_clears_verified_by_when_raw_text_changed(conn):
    upsert_programs(conn, [program(raw_text="원문")])
    verify(conn)

    upsert_programs(conn, [program(raw_text="바뀐 원문")])

    (row,) = rows(conn)
    assert row["raw_text"] == "바뀐 원문" and row["verified_by"] is None


def test_update_clears_verified_by_when_raw_text_becomes_null(conn):
    upsert_programs(conn, [program(raw_text="원문")])
    verify(conn)

    upsert_programs(conn, [program(raw_text=None)])

    assert rows(conn)[0]["verified_by"] is None


def test_input_verified_by_is_ignored(conn):
    upsert_programs(conn, [program(verified_by="아무개", region="서울")])  # 모르는 키도 무시한다

    assert rows(conn)[0]["verified_by"] is None


def test_without_source_url_matches_by_name_and_agency(conn):
    same = {"name": "주소 없는 공고", "agency": "기관", "source_url": None}
    where = "name = :n"
    upsert_programs(conn, [program(**same, raw_text="원문")])
    verify(conn, where=where, n="주소 없는 공고")

    upsert_programs(conn, [program(**same, raw_text="원문", amount_max=1)])  # 같은 이름 · 기관 → 갱신
    upsert_programs(conn, [program(**{**same, "agency": "다른 기관"})])  # 기관이 다르면 새 행
    upsert_programs(conn, [program(**{**same, "agency": None})])  # 기관 없음도 별개
    upsert_programs(conn, [program(**{**same, "agency": None})])  # 기관 없음끼리는 같은 공고

    found = rows(conn, where=where, n="주소 없는 공고")
    assert [(r["agency"], r["amount_max"]) for r in found] == [
        ("기관", 1),
        ("다른 기관", 5_000_000),
        (None, 5_000_000),
    ]
    assert found[0]["verified_by"] == "검수자"


def test_input_without_url_does_not_touch_row_that_has_url(conn):
    """이름 · 기관이 같아도 DB 행에 주소가 있으면 다른 공고다. 기존 행의 주소를 지우지 않는다."""
    upsert_programs(conn, [program(name="동명 공고", agency="기관", raw_text="원문")])
    verify(conn)

    upsert_programs(conn, [program(name="동명 공고", agency="기관", source_url=None, amount_max=99)])

    found = rows(conn, where="name = :n", n="동명 공고")
    assert [(r["source_url"], r["amount_max"], r["verified_by"]) for r in found] == [
        (URL, 5_000_000, "검수자"),  # 기존 행은 주소 · 금액 · 검수가 그대로
        (None, 99, None),  # 주소 없는 입력은 새 행
    ]


def test_input_with_url_does_not_take_over_row_without_url(conn):
    upsert_programs(conn, [program(name="동명 공고", agency="기관", source_url=None)])

    upsert_programs(conn, [program(name="동명 공고", agency="기관", amount_max=99)])

    found = rows(conn, where="name = :n", n="동명 공고")
    assert [(r["source_url"], r["amount_max"]) for r in found] == [(None, 5_000_000), (URL, 99)]


def test_same_name_with_different_urls_are_different_programs(conn):
    upsert_programs(conn, [program(name="동명 공고", agency="기관")])

    upsert_programs(
        conn, [program(name="동명 공고", agency="기관", source_url="https://example.test/upsert/2")]
    )

    found = rows(conn, where="name = :n", n="동명 공고")
    assert [r["source_url"] for r in found] == [URL, "https://example.test/upsert/2"]


def test_same_key_twice_in_one_call_keeps_last(conn):
    before = count(conn)

    assert upsert_programs(conn, [program(amount_max=1), program(amount_max=2)]) == 2

    assert count(conn) == before + 1 and rows(conn)[0]["amount_max"] == 2


def test_duplicate_rows_in_db_updates_only_first(conn):
    insert = (
        "INSERT INTO support_program (name, amount_max, eligibility, source_url) VALUES (:n, 1, '{}', :u)"
    )
    conn.execute(text(insert), {"n": "먼저", "u": URL})
    conn.execute(text(insert), {"n": "나중", "u": URL})
    # 먼저 만든 행을 한 번 고쳐 두 행의 물리적 저장 순서를 뒤집는다. 정렬이 없으면 나중 행이 먼저 잡힌다
    conn.execute(text("UPDATE support_program SET name = '먼저' WHERE name = '먼저'"))

    upsert_programs(conn, [program(name="갱신", amount_max=9)])

    first, second = rows(conn)
    assert (first["name"], first["amount_max"]) == ("갱신", 9)
    assert (second["name"], second["amount_max"]) == ("나중", 1)


def test_empty_list_does_nothing(conn):
    before = count(conn)

    assert upsert_programs(conn, []) == 0

    assert count(conn) == before


def test_district_code_is_saved(conn):
    code = conn.execute(text("SELECT district_code FROM district ORDER BY 1 LIMIT 1")).scalar_one()

    upsert_programs(conn, [program(district_code=code)])

    assert rows(conn)[0]["district_code"] == code


@pytest.mark.parametrize(
    "bad",
    [
        {"amount_max": -1},  # CHECK (amount_max >= 0)
        {"district_code": "99999"},  # district FK
    ],
)
def test_db_constraint_violation_rolls_back_everything(conn, bad):
    before = count(conn)

    with pytest.raises(IntegrityError):
        upsert_programs(conn, [program(), program(source_url="https://example.test/upsert/2", **bad)])

    assert count(conn) == before  # 앞의 정상 공고도 들어가지 않는다


@pytest.mark.parametrize("missing", ["name", "amount_max", "eligibility"])
def test_missing_required_key_rolls_back_everything(conn, missing):
    before = count(conn)
    broken = program(source_url="https://example.test/upsert/2")
    del broken[missing]

    with pytest.raises(KeyError):
        upsert_programs(conn, [program(), broken])

    assert count(conn) == before


@pytest.mark.parametrize("bad", ['{"and": []}', [], None, 5])
def test_eligibility_must_be_a_dict_and_rolls_back_everything(conn, bad):
    before = count(conn)

    with pytest.raises(TypeError):
        upsert_programs(
            conn, [program(), program(source_url="https://example.test/upsert/2", eligibility=bad)]
        )

    assert count(conn) == before  # JSON 글자를 넣어도 이중 인코딩된 채 저장되지 않는다
