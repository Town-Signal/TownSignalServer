"""batch.llm.client — Claude 호출 · 오류 구분 · 응답 해석 · Batch 제출 · 결과 수집(키 · 네트워크 없이)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from batch.jobs import build_support_program as job
from batch.jobs.build_support_program import RawNotice
from batch.llm import client
from batch.llm.client import BatchRequest, BatchResult, LLMError, LLMUnavailable
from batch.llm.structure import response_schema

anthropic = pytest.importorskip("anthropic")  # 기본 CI에는 없다. ci-batch가 돌린다
httpx2 = pytest.importorskip("httpx2")

SCHEMA = {"type": "object", "properties": {"a": {"type": ["string", "null"], "enum": ["x", None]}}}
NOW = datetime(2026, 11, 7, tzinfo=UTC)


def message(text: str = '{"a": "x"}', stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


def status_error(code: int):
    response = httpx2.Response(code, request=httpx2.Request("POST", "https://x.test"))
    return anthropic.APIStatusError("실패 내용", response=response, body=None)


class FakeBatches:
    def __init__(self, batches=(), results=None, error=None):
        self.batches, self.by_batch, self.error, self.created = list(batches), results or {}, error, None

    def create(self, requests):
        if self.error:
            raise self.error
        self.created = requests
        return SimpleNamespace(id="batch_1")

    def list(self):
        return iter(self.batches)

    def results(self, batch_id):
        return iter(self.by_batch[batch_id])


class FakeMessages:
    def __init__(self, reply=None, error=None, batches=None):
        self.reply, self.error, self.sent = reply or message(), error, None
        self.batches = batches or FakeBatches()

    def create(self, **kwargs):
        self.sent = kwargs
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        messages = FakeMessages(**kwargs)
        monkeypatch.setattr(client, "_client", lambda: SimpleNamespace(messages=messages))
        return messages

    return install


# ── 준비 · 키 ────────────────────────────────────────────────


def test_ensure_ready_needs_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        client.ensure_ready()
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        client.generate_json("프롬프트", SCHEMA)

    monkeypatch.setenv("LLM_API_KEY", "시험용-키")
    client.ensure_ready()


# ── generate_json ────────────────────────────────────────────


def test_generate_json_returns_object_and_sends_schema(fake):
    messages = fake()

    assert client.generate_json("프롬프트", SCHEMA) == {"a": "x"}

    sent = messages.sent
    assert sent["model"] == "claude-opus-5-5"
    assert sent["messages"] == [{"role": "user", "content": "프롬프트"}]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    prop = sent["output_config"]["format"]["schema"]["properties"]["a"]
    assert prop == {"anyOf": [{"type": "string", "enum": ["x"]}, {"type": "null"}]}


def test_real_response_schema_has_no_type_lists():
    """["타입", "null"] 꼴이 하나도 남지 않는다(anyOf로 바뀐다). 원본 스키마는 바뀌지 않는다."""
    schema = response_schema(["강남구", "마포구"])
    before = json.dumps(schema)

    sent = json.dumps(client._to_any_of(schema))

    assert '"type": ["' not in sent and "anyOf" in sent
    assert json.dumps(schema) == before


@pytest.mark.parametrize("code", [429, 500, 503, 529])
def test_transient_status_is_unavailable(fake, code):
    fake(error=status_error(code))
    with pytest.raises(LLMUnavailable, match=str(code)):
        client.generate_json("프롬프트", SCHEMA)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_request_or_setup_problem_is_llm_error(fake, code):
    fake(error=status_error(code))
    with pytest.raises(LLMError, match=str(code)):
        client.generate_json("프롬프트", SCHEMA)


def test_connection_failure_is_unavailable(fake):
    fake(error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.test")))
    with pytest.raises(LLMUnavailable):
        client.generate_json("프롬프트", SCHEMA)


@pytest.mark.parametrize(
    "reply",
    [
        message(stop_reason="refusal"),
        message(stop_reason="max_tokens"),
        message(text="JSON이 아니다"),
        message(text="[1, 2]"),
        SimpleNamespace(stop_reason="end_turn", content=[]),
    ],
)
def test_unusable_reply_is_unavailable(fake, reply):
    fake(reply=reply)
    with pytest.raises(LLMUnavailable):
        client.generate_json("프롬프트", SCHEMA)


# ── submit_batch ─────────────────────────────────────────────


def test_submit_batch_sends_one_request_per_prompt(fake):
    batches = FakeBatches()
    fake(batches=batches)

    batch_id = client.submit_batch([BatchRequest("k1", "첫째"), BatchRequest("k2", "둘째")], SCHEMA)

    assert batch_id == "batch_1"
    assert [r["custom_id"] for r in batches.created] == ["k1", "k2"]
    assert batches.created[1]["params"]["messages"][0]["content"] == "둘째"
    assert batches.created[0]["params"]["model"] == "claude-opus-5-5"


def test_submit_batch_error_kinds(fake):
    fake(batches=FakeBatches(error=status_error(400)))
    with pytest.raises(LLMError):
        client.submit_batch([BatchRequest("k", "p")], SCHEMA)

    fake(batches=FakeBatches(error=status_error(503)))
    with pytest.raises(LLMUnavailable):
        client.submit_batch([BatchRequest("k", "p")], SCHEMA)


# ── fetch_batch_results ──────────────────────────────────────


def done(custom_id: str, text: str = '{"a": "x"}', stop_reason: str = "end_turn"):
    result = SimpleNamespace(type="succeeded", message=message(text, stop_reason))
    return SimpleNamespace(custom_id=custom_id, result=result)


def failed(custom_id: str, kind: str):
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type=kind))


def batch(batch_id: str, status: str, ended_at):
    return SimpleNamespace(id=batch_id, processing_status=status, ended_at=ended_at)


def test_fetch_reads_only_ended_batches_since(fake):
    batches = FakeBatches(
        batches=[
            batch("new", "ended", NOW - timedelta(days=1)),
            batch("old", "ended", NOW - timedelta(days=30)),
            batch("running", "in_progress", None),
        ],
        results={
            "new": [
                done("ok"),
                done("bad-json", text="JSON이 아니다"),
                done("refused", stop_reason="refusal"),
                failed("err", "errored"),
                failed("exp", "expired"),
            ]
        },
    )
    fake(batches=batches)

    results = client.fetch_batch_results(NOW - timedelta(days=7))

    assert results == [
        BatchResult("ok", {"a": "x"}),
        BatchResult("bad-json", None),
        BatchResult("refused", None),
        BatchResult("err", None),
        BatchResult("exp", None),
    ]


def test_fetch_treats_naive_since_as_utc(fake):
    fake(batches=FakeBatches(batches=[batch("b", "ended", NOW)], results={"b": [done("ok")]}))

    assert [r.custom_id for r in client.fetch_batch_results(datetime(2026, 11, 1))] == ["ok"]


def test_fetch_with_nothing_ended_is_empty(fake):
    fake(batches=FakeBatches())

    assert client.fetch_batch_results(NOW) == []


# ── 주간 흐름 전체(제공사 SDK만 가짜): 제출 → 다음 주 결과 수집 → 검수 대기 행 ────


ANSWER = {
    "amount_max": 3_000_000,
    "district_name": None,
    "is_exclusive": False,
    "conditions_known": True,
    "conditions": [[{"field": "age", "op": "<=", "number_value": 39, "evidence": "만 39세 이하"}]],
}


class EchoBatches(FakeBatches):
    """제출하면 그 요청마다 같은 구조화 답이 온 batch 하나가 끝나 있다."""

    def create(self, requests):
        super().create(requests)
        self.batches = [batch("b1", "ended", NOW + timedelta(days=1))]
        self.by_batch = {"b1": [done(r["custom_id"], json.dumps(ANSWER)) for r in requests]}
        return SimpleNamespace(id="b1")


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def test_two_weekly_runs_with_fake_sdk_leave_one_pending_row(conn, fake):
    batches = EchoBatches()
    fake(batches=batches)
    one = RawNotice(
        source_url="https://example.test/e2e/1",
        title="공고",
        agency="기관",
        raw_text="신청대상: 만 39세 이하",
        district_code=None,
        region="서울",
    )
    real = {"submit": client.submit_batch, "fetch_results": client.fetch_batch_results}

    first = job.run(conn, [one], now=NOW, **real)  # 첫 주: 받을 결과가 없고, 새 공고를 제출한다
    second = job.run(conn, [one], now=NOW + timedelta(days=7), **real)  # 다음 주: 결과를 받아 적재한다

    assert first == (0, "b1")
    assert second == (1, None)  # 적재했고, 이제 DB에 있는 공고라 다시 제출하지 않는다
    sql = text("SELECT amount_max, verified_by, eligibility FROM support_program WHERE source_url = :u")
    saved = conn.execute(sql, {"u": one.source_url}).one()
    assert saved.amount_max == 3_000_000 and saved.verified_by is None  # 검수 전이라 매칭에 안 쓰인다
    assert saved.eligibility == {"and": [{"field": "age", "op": "<=", "value": 39}]}
    assert len(batches.created) == 1 and batches.created[0]["params"]["model"] == "claude-opus-5-5"
