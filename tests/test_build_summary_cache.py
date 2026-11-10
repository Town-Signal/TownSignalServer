"""요약 캐시 배치 — 사실 표기 · 숫자 검사 · LLM 호출 처리(가짜 응답) · 대상 선정과 적재(더미 DB)."""

from decimal import Decimal

import pytest
from sqlalchemy import text

from batch.jobs import build_summary_cache as bsc
from batch.llm import client
from common.constants import SERVING_MODEL_VERSION

TARGET = {
    "dong_code": "11740690",
    "dong_name": "둔촌1동",
    "district_name": "강동구",
    "industry_code": "CS100001",
    "industry_name": "한식음식점",
    "score_rank": 1,
    "ranked_count": 412,
    "sales_p10": 85_283_032,
    "sales_p50": 109_510_384,
    "sales_p90": 135_384_704,
    "sales_percentile": Decimal("0.9433"),
    "survival_prob_1y": Decimal("0.919"),
    "survival_prob_3y": Decimal("0.791"),
    "growth_rate": None,
    "store_count": 13,
}


def test_facts_formats_values_and_skips_missing():
    f = bsc.facts(TARGET)
    assert f["위치"] == "강동구 둔촌1동"
    assert f["종합점수 순위"] == "서울 412개 동 중 1위"
    assert f["점포당 월 매출(가운데 값)"] == "3,650만 원"  # 분기 ÷ 3 버림
    assert f["점포당 월 매출 예상 범위"] == "2,843만 원 ~ 4,513만 원"
    assert f["매출 수준"] == "서울 같은 업종 동네 중 상위 6%"
    assert f["3년 영업 유지 확률"] == "79%"
    assert f["1년 영업 유지 확률"] == "92%"
    assert f["같은 업종 점포 수(최근 분기)"] == "13곳"
    assert "최근 1년 매출 변화" not in f  # 성장세가 없으면 항목을 뺀다


def test_build_prompt_contains_rules_and_facts():
    prompt = bsc.build_prompt(TARGET)
    assert "새 숫자를 만들거나" in prompt
    assert "- 3년 영업 유지 확률: 79%" in prompt


def test_check_numbers_accepts_copied_numbers_and_rejects_new_ones():
    ok = "둔촌1동 한식은 점포당 월 매출이 3,650만 원 정도로 서울 상위 6%이고, 3년 영업 유지 확률은 79%예요."
    assert bsc.check_numbers(ok, TARGET)
    made_up = "둔촌1동 한식은 월 매출이 약 4,000만 원이에요."
    assert not bsc.check_numbers(made_up, TARGET)


def test_request_summaries_handles_failures():
    replies = iter([
        {"summary": "3년 영업 유지 확률이 79%로 높은 편이에요."},
        client.LLMUnavailable("일시 오류"),
        {"summary": "월 매출이 5,000만 원이에요."},  # 사실에 없는 숫자
        {"summary": "  "},
    ])

    def fake(prompt, schema):
        assert schema is bsc.SUMMARY_SCHEMA
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    out = bsc.request_summaries([TARGET] * 4, generate=fake)
    assert out == ["3년 영업 유지 확률이 79%로 높은 편이에요.", None, None, None]


def test_request_summaries_raises_config_errors():
    def fake(prompt, schema):
        raise client.LLMError("키 없음")

    with pytest.raises(client.LLMError):
        bsc.request_summaries([TARGET], generate=fake)


def test_main_without_mode_prints_prompt_only(monkeypatch, capsys):
    """옵션 없이 돌리면 LLM을 부르지 않는다(비용 없음)."""
    monkeypatch.setattr(bsc, "select_targets", lambda conn, codes, top: [TARGET])
    monkeypatch.setattr(bsc, "create_engine", _FakeEngine)
    monkeypatch.setattr(client, "generate_json", lambda *a: pytest.fail("LLM을 부르면 안 된다"))
    assert bsc.main(["--industries", "CS100001"]) == 0
    assert "3년 영업 유지 확률: 79%" in capsys.readouterr().out


class _FakeEngine:
    def __init__(self, url):
        pass

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── 더미 DB ─────────────────────────────────────────────


def _ranked_industry(conn) -> str:
    return conn.execute(
        text("SELECT industry_code FROM prediction WHERE model_version = :mv AND score_rank IS NOT NULL "
             "GROUP BY industry_code ORDER BY count(*) DESC LIMIT 1"),
        {"mv": SERVING_MODEL_VERSION},
    ).scalar_one()


def test_select_targets_returns_top_ranked(dummy_db):
    code = _ranked_industry(dummy_db)
    targets = bsc.select_targets(dummy_db, [code], top_n=3)
    assert 1 <= len(targets) <= 3
    assert [t["score_rank"] for t in targets] == sorted(t["score_rank"] for t in targets)
    assert all(t["score_rank"] <= 3 and t["industry_code"] == code for t in targets)
    assert targets[0]["dong_name"] and targets[0]["district_name"] and targets[0]["industry_name"]
    assert targets[0]["ranked_count"] >= len(targets)
    bsc.build_prompt(targets[0])  # 실제 행으로도 프롬프트가 만들어진다


def test_upsert_summaries_inserts_then_updates(dummy_db):
    code = _ranked_industry(dummy_db)
    t = bsc.select_targets(dummy_db, [code], top_n=1)[0]
    row = {"dong_code": t["dong_code"], "industry_code": code, "summary_text": "첫 요약", "model_name": "m1"}
    assert bsc.upsert_summaries(dummy_db, [row]) == 1
    assert bsc.upsert_summaries(dummy_db, [{**row, "summary_text": "두 번째 요약", "model_name": "m2"}]) == 1
    got = dummy_db.execute(
        text("SELECT summary_text, model_name FROM summary_cache "
             "WHERE dong_code = :d AND industry_code = :i"),
        {"d": t["dong_code"], "i": code},
    ).one()
    assert tuple(got) == ("두 번째 요약", "m2")
    assert bsc.upsert_summaries(dummy_db, []) == 0
