"""load_support_seed — source_url 기준 넣기 · 갱신, verified_by 반영, 전부 아니면 전무."""

import json

import pytest
from sqlalchemy import text

from batch.jobs.load_support_seed import (
    LoadResult,
    SeedLoadError,
    confirm_target,
    load_programs,
    read_programs,
    seed_files,
)

URL = "https://example.test/seed/1"
URL2 = "https://example.test/seed/2"
ELIG = {"and": [{"field": "age", "op": "<=", "value": 39}]}
LOCAL = "postgresql+psycopg://u:p@localhost:5432/townsignal"
REMOTE = "postgresql+psycopg://u:p@10.0.0.5:5432/townsignal"


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def program(**kw):
    return {"name": "시드 공고", "amount_max": 3_000_000, "eligibility": ELIG, "source_url": URL, **kw}


def seed(tmp_path, programs, name="initial_support_programs.json"):
    path = tmp_path / name
    path.write_text(json.dumps(programs, ensure_ascii=False), encoding="utf-8")
    return read_programs([path])


def rows(conn, url=URL):
    sql = text("SELECT * FROM support_program WHERE source_url = :u ORDER BY program_id")
    return conn.execute(sql, {"u": url}).mappings().all()


def count(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM support_program")).scalar_one()


def test_insert_new_programs(conn, tmp_path):
    before = count(conn)

    first = program(agency="기관", apply_end="2026-12-31", verified_by="홍길동")

    result = load_programs(conn, seed(tmp_path, [first, program(source_url=URL2)]))

    assert result == LoadResult(inserted=2, updated=0, verified=1)
    assert count(conn) == before + 2
    (row,) = rows(conn)
    assert (row["name"], row["agency"], row["amount_max"], row["verified_by"]) == (
        "시드 공고",
        "기관",
        3_000_000,
        "홍길동",
    )
    assert row["eligibility"] == ELIG and str(row["apply_end"]) == "2026-12-31"
    assert row["extracted_at"] is not None
    assert rows(conn, URL2)[0]["verified_by"] is None


def test_same_file_twice_makes_no_duplicates(conn, tmp_path):
    items = seed(tmp_path, [program(), program(source_url=URL2)])
    load_programs(conn, items)
    after_first = count(conn)

    result = load_programs(conn, items)

    assert result == LoadResult(inserted=0, updated=2, verified=0)
    assert count(conn) == after_first and len(rows(conn)) == 1


def test_changed_verified_by_updates_row_and_keeps_extracted_at(conn, tmp_path):
    load_programs(conn, seed(tmp_path, [program()]))
    (first,) = rows(conn)

    result = load_programs(conn, seed(tmp_path, [program(verified_by=" 홍길동 ", amount_max=0)]))

    (row,) = rows(conn)
    assert result == LoadResult(inserted=0, updated=1, verified=1)
    assert (row["verified_by"], row["amount_max"]) == ("홍길동", 0)
    assert row["program_id"] == first["program_id"] and row["extracted_at"] == first["extracted_at"]


def test_empty_verified_by_in_seed_clears_db_value(conn, tmp_path):
    """seed가 정본이라 DB에서 직접 채운 verified_by도 seed 값(빈 값이면 NULL)으로 바뀐다."""
    load_programs(conn, seed(tmp_path, [program()]))
    conn.execute(text("UPDATE support_program SET verified_by = '직접' WHERE source_url = :u"), {"u": URL})

    load_programs(conn, seed(tmp_path, [program(verified_by="")]))

    assert rows(conn)[0]["verified_by"] is None


@pytest.mark.parametrize("bad", ['{"and": []}', [], None])
def test_bad_eligibility_rolls_back_everything(conn, tmp_path, bad):
    before = count(conn)
    items = seed(tmp_path, [program(), program(source_url=URL2, eligibility=bad)])

    with pytest.raises(SeedLoadError, match=r"initial_support_programs\.json 2번째 공고.*seed/2"):
        load_programs(conn, items)

    assert count(conn) == before  # 앞의 정상 공고도 들어가지 않는다


def test_db_error_rolls_back_everything(conn, tmp_path):
    before = count(conn)
    items = seed(tmp_path, [program(), program(source_url=URL2, district_code="99999")])

    with pytest.raises(SeedLoadError, match="2번째 공고.*IntegrityError"):
        load_programs(conn, items)

    assert count(conn) == before


def test_unknown_keys_are_ignored(conn, tmp_path):
    result = load_programs(conn, seed(tmp_path, [program(amount_evidence="공고문 2쪽", foo=1)]))

    assert result.inserted == 1 and rows(conn)[0]["name"] == "시드 공고"


def test_nul_bytes_in_text_are_removed(conn, tmp_path):
    """PDF에서 뽑은 raw_text의 NUL은 PostgreSQL text에 넣을 수 없어 지우고 적재한다."""
    load_programs(conn, seed(tmp_path, [program(raw_text="공고\x00 원문\x00")]))

    assert rows(conn)[0]["raw_text"] == "공고 원문"


def test_seed_files_order(tmp_path):
    for name in ["support_programs_seoul_manual.json", "initial_support_programs.json", "other.json"]:
        (tmp_path / name).write_text("[]", encoding="utf-8")

    assert [p.name for p in seed_files(tmp_path)] == [
        "initial_support_programs.json",
        "support_programs_seoul_manual.json",
    ]


def test_top_level_must_be_a_list(tmp_path):
    path = tmp_path / "initial_support_programs.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(SeedLoadError, match="리스트"):
        read_programs([path])


def test_confirm_target():
    def never(_):
        raise AssertionError("묻지 않아야 한다")

    assert confirm_target(LOCAL, yes=False, ask=never)
    assert confirm_target(REMOTE, yes=True, ask=never)
    assert confirm_target(REMOTE, yes=False, ask=lambda _: "yes")
    assert not confirm_target(REMOTE, yes=False, ask=lambda _: "no")
    assert not confirm_target("postgresql+psycopg://u:p@db:5432/townsignal", yes=False, ask=lambda _: "")