"""build_support_program의 Batch 흐름 — 제출 · 지난주 결과 적재 · 주간 두 번의 실행(제공사는 가짜)."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from batch.jobs import build_support_program as job
from batch.jobs.build_support_program import RawNotice
from batch.llm.client import BatchResult
from batch.llm.structure import custom_id

CODES = {"용산구": "11170", "구로구": "11530"}
NOW = datetime(2026, 10, 12, tzinfo=UTC)
AGE_TREE = {"and": [{"field": "age", "op": "<=", "value": 39}]}


def notice(n: int = 1, **kw) -> RawNotice:
    base = {
        "source_url": f"https://example.test/batch/{n}",
        "title": f"공고{n}",
        "agency": "기관",
        "raw_text": f"원문{n}",
        "district_code": None,
        "region": "서울",
        "apply_start": date(2026, 10, 1),
        "apply_end": date(2026, 12, 31),
    }
    return RawNotice(**{**base, **kw})


def answer(**kw) -> dict:
    """LLM이 준 구조화 결과."""
    cond = {"field": "age", "op": "<=", "number_value": 39, "evidence": "만 39세 이하"}
    return {
        "amount_max": 3_000_000,
        "amount_evidence": "",
        "district_name": None,
        "is_exclusive": False,
        "conditions_known": True,
        "conditions": [[cond]],
        **kw,
    }


class Provider:
    """가짜 제공사: 제출을 기록하고, 결과 조회는 미리 넣어 둔 결과를 돌려준다."""

    def __init__(self):
        self.submitted: list[list] = []
        self.schemas: list[dict] = []
        self.results: list[BatchResult] = []
        self.since: list[datetime] = []

    def submit(self, requests, schema):
        self.submitted.append(requests)
        self.schemas.append(schema)
        return f"batch-{len(self.submitted)}"

    def fetch_results(self, since):
        self.since.append(since)
        return self.results

    def answer_last_submission(self, **kw):
        """마지막에 제출한 요청마다 답이 온 것으로 만든다."""
        self.results = [BatchResult(r.custom_id, answer(**kw)) for r in self.submitted[-1]]


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def row(conn, n: int = 1):
    found = (
        conn.execute(
            text("SELECT * FROM support_program WHERE source_url = :u"),
            {"u": f"https://example.test/batch/{n}"},
        )
        .mappings()
        .all()
    )
    assert len(found) <= 1
    return found[0] if found else None


# ── submit_structuring_batch ───────────────────────────────────────────


def test_submit_builds_one_request_per_notice_with_its_tag():
    provider = Provider()
    one, two = notice(1), notice(2, region="전국")

    batch_id = job.submit_structuring_batch([one, two], CODES, provider.submit)

    assert batch_id == "batch-1"
    first, second = provider.submitted[0]
    assert (first.custom_id, second.custom_id) == (custom_id(one), custom_id(two))
    assert (
        "원문1" in first.prompt
        and "[공고 지역 구분] 서울" in first.prompt
        and "용산구, 구로구" in first.prompt
    )
    assert "[공고 지역 구분] 전국" in second.prompt
    assert provider.schemas[0]["properties"]["district_name"]["enum"] == ["용산구", "구로구", None]


def test_submit_nothing_does_not_call_provider():
    provider = Provider()

    assert job.submit_structuring_batch([], CODES, provider.submit) is None

    assert provider.submitted == []


# ── collect_pending_llm_results ────────────────────────────────────────


def collect(conn, notices, results, since=NOW):
    provider = Provider()
    provider.results = results
    return job.collect_pending_llm_results(conn, notices, CODES, since, provider.fetch_results)


def test_collect_saves_structured_result_with_collected_values(conn):
    one = notice(1)

    saved = collect(
        conn, [one], [BatchResult(custom_id(one), answer(district_name="용산구", is_exclusive=True))]
    )

    assert saved == 1
    r = row(conn)
    assert (r["name"], r["agency"], r["raw_text"], r["apply_end"]) == (
        "공고1",
        "기관",
        "원문1",
        date(2026, 12, 31),
    )
    assert (r["amount_max"], r["district_code"], r["is_exclusive"]) == (3_000_000, "11170", True)
    assert r["eligibility"] == AGE_TREE and r["verified_by"] is None and r["extracted_at"] is not None


def test_collect_skips_result_for_notice_that_is_no_longer_collected(conn):
    gone = notice(1)

    assert collect(conn, [notice(2)], [BatchResult(custom_id(gone), answer())]) == 0

    assert row(conn, 1) is None


def test_collect_skips_result_when_raw_text_changed_after_submission(conn):
    old = notice(1, raw_text="제출 당시 원문")
    now = notice(1, raw_text="바뀐 원문")

    assert collect(conn, [now], [BatchResult(custom_id(old), answer())]) == 0

    assert row(conn) is None


@pytest.mark.parametrize("data", [None, [], "글자"])
def test_collect_skips_failed_or_malformed_result(conn, data):
    one = notice(1)

    assert collect(conn, [one], [BatchResult(custom_id(one), data)]) == 0

    assert row(conn) is None


def test_collect_does_not_overwrite_row_that_already_has_same_raw_text(conn):
    one = notice(1)
    collect(conn, [one], [BatchResult(custom_id(one), answer())])
    conn.execute(
        text("UPDATE support_program SET verified_by = '검수자', amount_max = 777 WHERE source_url = :u"),
        {"u": one.source_url},
    )

    saved = collect(conn, [one], [BatchResult(custom_id(one), answer(amount_max=1))])  # 같은 결과가 다시 옴

    r = row(conn)
    assert saved == 0 and (r["amount_max"], r["verified_by"]) == (777, "검수자")


def test_collect_updates_row_whose_raw_text_changed_and_clears_verification(conn):
    old = notice(1, raw_text="옛 원문")
    collect(conn, [old], [BatchResult(custom_id(old), answer())])
    conn.execute(
        text("UPDATE support_program SET verified_by = '검수자' WHERE source_url = :u"), {"u": old.source_url}
    )
    new = notice(1, raw_text="새 원문")

    saved = collect(conn, [new], [BatchResult(custom_id(new), answer(amount_max=9))])

    r = row(conn)
    assert saved == 1 and (r["raw_text"], r["amount_max"], r["verified_by"]) == ("새 원문", 9, None)


def test_collect_with_no_results_saves_nothing(conn):
    assert collect(conn, [notice(1)], []) == 0


# ── run: 주간 실행 두 번 ───────────────────────────────────────────────


def test_first_week_submits_everything_and_saves_nothing(conn):
    provider = Provider()

    saved, batch_id = job.run(conn, [notice(1), notice(2)], NOW, provider.submit, provider.fetch_results)

    assert (saved, batch_id) == (0, "batch-1")
    assert [r.custom_id for r in provider.submitted[0]] == [custom_id(notice(1)), custom_id(notice(2))]
    assert row(conn, 1) is None and row(conn, 2) is None
    assert provider.since == [NOW - timedelta(days=job.COLLECT_DAYS)]


def test_second_week_saves_fresh_results_and_submits_new_and_changed_notices(conn):
    provider = Provider()
    job.run(conn, [notice(1), notice(2), notice(3)], NOW, provider.submit, provider.fetch_results)  # 1주차
    provider.answer_last_submission()
    # 2주차: 1은 그대로, 2는 원문이 바뀜, 3은 목록에서 사라짐, 4는 새 공고
    week2 = [notice(1), notice(2, raw_text="바뀐 원문"), notice(4)]

    saved, batch_id = job.run(conn, week2, NOW + timedelta(days=7), provider.submit, provider.fetch_results)

    assert saved == 1 and batch_id == "batch-2"
    assert row(conn, 1)["amount_max"] == 3_000_000  # 1만 적재됨
    assert row(conn, 2) is None and row(conn, 3) is None  # 옛 원문 · 사라진 공고의 답은 버림
    assert [r.custom_id for r in provider.submitted[1]] == [
        custom_id(week2[1]),
        custom_id(week2[2]),
    ]  # 1은 제외


def test_third_week_leaves_verified_row_alone_and_submits_nothing_new(conn):
    provider = Provider()
    job.run(conn, [notice(1)], NOW, provider.submit, provider.fetch_results)
    provider.answer_last_submission()
    job.run(conn, [notice(1)], NOW + timedelta(days=7), provider.submit, provider.fetch_results)
    conn.execute(
        text("UPDATE support_program SET verified_by = '검수자', amount_max = 777 WHERE source_url = :u"),
        {"u": notice(1).source_url},
    )

    saved, batch_id = job.run(
        conn, [notice(1)], NOW + timedelta(days=14), provider.submit, provider.fetch_results
    )

    r = row(conn, 1)
    assert (saved, batch_id) == (0, None) and (r["amount_max"], r["verified_by"]) == (777, "검수자")
    assert len(provider.submitted) == 1  # 새로 제출한 것이 없다


# ── main ───────────────────────────────────────────────────────────────


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin(self):
        yield self.conn


def test_main_collects_notices_once_and_runs_both_steps(conn, monkeypatch, caplog):
    monkeypatch.setenv("PUBLIC_DATA_API_KEY", "시험용-키")
    provider = Provider()
    fetched = []

    def fetch(api_key):
        fetched.append(api_key)
        return [notice(1), notice(2)]

    with caplog.at_level("INFO", logger=job.logger.name):
        job.main(FakeEngine(conn), fetch, provider.submit, provider.fetch_results)

    assert fetched == ["시험용-키"]  # 수집은 한 번
    assert len(provider.submitted) == 1 and len(provider.since) == 1  # ① ② 모두 실행됨
    assert "수집 2건" in caplog.text and "적재 0건" in caplog.text and "batch-1" in caplog.text


def test_main_without_public_data_key_fails_before_any_work(monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_API_KEY", "")

    def fetch(api_key):
        raise AssertionError("수집하면 안 된다")

    with pytest.raises(RuntimeError, match="PUBLIC_DATA_API_KEY"):
        job.main(None, fetch)


def test_main_is_not_implemented_until_provider_is_connected(conn, monkeypatch):
    """운영 제공사를 연결하기 전에는 run_weekly가 '미구현'으로 기록한다."""
    monkeypatch.setenv("PUBLIC_DATA_API_KEY", "시험용-키")

    with pytest.raises(NotImplementedError):
        job.main(FakeEngine(conn), lambda key: [notice(1)])
