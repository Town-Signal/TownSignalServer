"""upsert_programs — 주소가 적재 키 · 있는 행은 건드리지 않음 · 전부 아니면 전무(명세 4.8)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from batch.jobs.build_support_program import upsert_programs

URL = "https://example.test/upsert/1"
URL2 = "https://example.test/upsert/2"
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


def verify(conn, by="검수자"):
    conn.execute(
        text("UPDATE support_program SET verified_by = :by WHERE source_url = :u"), {"by": by, "u": URL}
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


def test_existing_url_is_left_untouched(conn):
    """같은 주소의 행은 값이 달라도 건드리지 않는다. 사람이 검수한 값이 덮어써지지 않는다."""
    upsert_programs(conn, [program(raw_text="원문")])
    verify(conn)
    conn.execute(text("UPDATE support_program SET amount_max = 777 WHERE source_url = :u"), {"u": URL})
    before = count(conn)

    inserted = upsert_programs(conn, [program(raw_text="바뀐 원문", amount_max=1, is_exclusive=True)])

    (row,) = rows(conn)
    assert inserted == 0 and count(conn) == before
    assert (row["amount_max"], row["raw_text"], row["is_exclusive"]) == (777, "원문", False)
    assert row["verified_by"] == "검수자"


def test_input_verified_by_is_ignored(conn):
    upsert_programs(conn, [program(verified_by="아무개", region="서울")])  # 모르는 키도 무시한다

    assert rows(conn)[0]["verified_by"] is None


def test_same_name_with_different_urls_are_different_programs(conn):
    upsert_programs(conn, [program(name="동명 공고", agency="기관")])

    assert upsert_programs(conn, [program(name="동명 공고", agency="기관", source_url=URL2)]) == 1

    found = rows(conn, where="name = :n", n="동명 공고")
    assert [r["source_url"] for r in found] == [URL, URL2]


def test_same_url_twice_in_one_call_keeps_first(conn):
    before = count(conn)

    assert upsert_programs(conn, [program(amount_max=1), program(amount_max=2)]) == 1

    assert count(conn) == before + 1 and rows(conn)[0]["amount_max"] == 1


def test_only_new_urls_are_counted(conn):
    upsert_programs(conn, [program()])

    assert upsert_programs(conn, [program(), program(source_url=URL2)]) == 1


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
        upsert_programs(conn, [program(), program(source_url=URL2, **bad)])

    assert count(conn) == before  # 앞의 정상 공고도 들어가지 않는다


@pytest.mark.parametrize("missing", ["name", "amount_max", "eligibility", "source_url"])
def test_missing_required_key_rolls_back_everything(conn, missing):
    before = count(conn)
    broken = program(source_url=URL2)
    del broken[missing]

    with pytest.raises(KeyError):
        upsert_programs(conn, [program(), broken])

    assert count(conn) == before


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_source_url_is_rejected_and_rolls_back_everything(conn, empty):
    """주소가 적재 키라서 비어 있으면 넣지 않는다(이름 · 기관으로 대신 찾지 않는다)."""
    before = count(conn)

    with pytest.raises(ValueError):
        upsert_programs(conn, [program(), program(source_url=empty)])

    assert count(conn) == before


@pytest.mark.parametrize("bad", ['{"and": []}', [], None, 5])
def test_eligibility_must_be_a_dict_and_rolls_back_everything(conn, bad):
    before = count(conn)

    with pytest.raises(TypeError):
        upsert_programs(conn, [program(), program(source_url=URL2, eligibility=bad)])

    assert count(conn) == before  # JSON 글자를 넣어도 이중 인코딩된 채 저장되지 않는다
