"""행정동 × 업종 요약 생성 — 담당: AI.

실행: python -m batch.jobs.build_summary_cache --industries CS100001,CS100010 [--top 30]
      [--generate | --write]
      옵션이 없으면 프롬프트만 출력한다(LLM을 부르지 않으니 비용이 없다). --generate는 요약을 받아 출력만,
      --write는 받은 요약을 summary_cache에 넣는다.
주기: 기준 분기가 바뀔 때(prediction 갱신 후), 팀원 PC에서(판정 38 · 39). 명세 6.6 LLM ② · 7.9 · 판정 9 · 10.

입력 표: prediction · dong · district · industry · store_quarterly
출력 표: summary_cache (dong_code, industry_code)

대상: 시연 업종의 종합점수 상위 N개 동부터(남은 일 17). 업종 전체(약 2만 건)는 Batch로 바꿀 때 넓힌다.
규칙: 숫자는 LLM이 만들지 않는다(6.6 공통 규칙). 서버가 계산해 사람이 읽는 표기로 바꾼 '사실'만
프롬프트에 넣고, 요약에 사실에 없는 숫자가 들어가면 그 요약은 버린다(check_numbers).
사용자별 예산 여유는 담지 않는다.
요청 경로(API)는 이 표를 읽기만 하고 LLM을 부르지 않는다.
"""

import argparse
import logging
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import Connection, create_engine, text

from batch import config
from batch.llm import client
from batch.pipeline.db import transaction
from common.budget import format_manwon
from common.constants import LLM_MODEL, SERVING_MODEL_VERSION

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 30
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string", "description": "한국어 1 ~ 2문장 요약"}},
    "required": ["summary"],
    "additionalProperties": False,
}

INSTRUCTION = """당신은 서울에서 창업을 준비하는 청년에게 동네 상권을 짧게 설명하는 도우미예요.
아래 '사실'만 가지고 이 동네에서 이 업종을 열 때의 특징을 한국어 1 ~ 2문장(120자 안팎)으로 요약해 주세요.

규칙
- 숫자는 '사실'에 적힌 표기를 그대로 옮겨요. 새 숫자를 만들거나 계산하거나 반올림하지 않아요.
- '사실'에 없는 내용(임대료, 유동인구, 경쟁 업체 이름, 미래 전망 등)은 쓰지 않아요.
- 추천하거나 투자를 권하지 않고, '반드시' '확실히' 같은 단정도 쓰지 않아요.
- 해요체로, 장점과 주의할 점이 있으면 둘 다 담아요.
"""

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

_TARGETS_SQL = text(
    """
    SELECT p.dong_code, d.name AS dong_name, ds.name AS district_name,
           p.industry_code, i.name AS industry_name,
           p.score_rank, p.total_score, p.sales_p10, p.sales_p50, p.sales_p90, p.sales_percentile,
           p.survival_prob_1y, p.survival_prob_3y, p.growth_rate,
           ranked.n AS ranked_count, sq.store_count, sq.year_quarter AS store_quarter
    FROM prediction p
    JOIN dong d ON d.dong_code = p.dong_code
    JOIN district ds ON ds.district_code = d.district_code
    JOIN industry i ON i.industry_code = p.industry_code
    JOIN (SELECT industry_code, count(*) AS n FROM prediction
          WHERE model_version = :mv AND score_rank IS NOT NULL GROUP BY industry_code) ranked
      ON ranked.industry_code = p.industry_code
    LEFT JOIN LATERAL (
        SELECT s.store_count, s.year_quarter FROM store_quarterly s
        WHERE s.dong_code = p.dong_code AND s.industry_code = p.industry_code
        ORDER BY s.year_quarter DESC LIMIT 1
    ) sq ON true
    WHERE p.model_version = :mv AND p.industry_code = ANY(:codes)
      AND p.score_rank IS NOT NULL AND p.score_rank <= :top
    ORDER BY p.industry_code, p.score_rank
    """
)

_UPSERT_SQL = text(
    """
    INSERT INTO summary_cache (dong_code, industry_code, summary_text, model_name, generated_at)
    VALUES (:dong_code, :industry_code, :summary_text, :model_name, now())
    ON CONFLICT (dong_code, industry_code) DO UPDATE
       SET summary_text = EXCLUDED.summary_text, model_name = EXCLUDED.model_name,
           generated_at = EXCLUDED.generated_at
    """
)


def select_targets(
    conn: Connection, industry_codes: Sequence[str], top_n: int = DEFAULT_TOP_N
) -> list[dict[str, Any]]:
    """업종별 종합점수(score_rank) 상위 top_n개 동. 순위가 없는(예측 불가) 조합은 뺀다.

    출력: 조합마다 prediction 값 · 동 · 구 · 업종 이름 · 그 업종의 순위 매긴 동 수 · 최근 분기 점포 수.
    """
    rows = conn.execute(
        _TARGETS_SQL, {"mv": SERVING_MODEL_VERSION, "codes": list(industry_codes), "top": top_n}
    ).mappings()
    return [dict(r) for r in rows]


def _percent(value: Any) -> str:
    return f"{round(float(value) * 100)}%"


def facts(target: dict[str, Any]) -> dict[str, str]:
    """프롬프트에 넣을 사실. 값이 없는 항목은 뺀다. 월 매출은 분기 ÷ 3 버림(판정 31)."""
    out = {
        "위치": f"{target['district_name']} {target['dong_name']}",
        "업종": target["industry_name"],
        "종합점수 순위": f"서울 {target['ranked_count']}개 동 중 {target['score_rank']}위",
    }
    if target.get("sales_p50") is not None:
        out["점포당 월 매출(가운데 값)"] = format_manwon(int(target["sales_p50"]) // 3)
    if target.get("sales_p10") is not None and target.get("sales_p90") is not None:
        out["점포당 월 매출 예상 범위"] = (
            f"{format_manwon(int(target['sales_p10']) // 3)} ~ {format_manwon(int(target['sales_p90']) // 3)}"
        )
    if target.get("sales_percentile") is not None:
        top = max(1, round((1 - float(target["sales_percentile"])) * 100))
        out["매출 수준"] = f"서울 같은 업종 동네 중 상위 {top}%"
    if target.get("survival_prob_3y") is not None:
        out["3년 영업 유지 확률"] = _percent(target["survival_prob_3y"])
    if target.get("survival_prob_1y") is not None:
        out["1년 영업 유지 확률"] = _percent(target["survival_prob_1y"])
    if target.get("growth_rate") is not None:
        out["최근 1년 매출 변화"] = f"{float(target['growth_rate']) * 100:+.1f}%"
    if target.get("store_count") is not None:
        out["같은 업종 점포 수(최근 분기)"] = f"{target['store_count']}곳"
    return out


def build_prompt(target: dict[str, Any]) -> str:
    """공통 지시문 + 이 조합의 사실 목록."""
    lines = "\n".join(f"- {k}: {v}" for k, v in facts(target).items())
    return f"{INSTRUCTION}\n사실\n{lines}\n"


def _numbers(s: str) -> set[str]:
    return {m.replace(",", "") for m in _NUMBER.findall(s)}


def check_numbers(summary: str, target: dict[str, Any]) -> bool:
    """요약 속 숫자가 모두 사실(항목 이름 포함)에 있으면 True. 하나라도 새 숫자면 False(그 요약은 버린다)."""
    allowed: set[str] = set()
    for k, v in facts(target).items():
        allowed |= _numbers(k) | _numbers(v)
    return _numbers(summary) <= allowed


def request_summaries(
    targets: Sequence[dict[str, Any]],
    generate: Callable[[str, dict[str, Any]], dict[str, Any]] = client.generate_json,
) -> list[str | None]:
    """조합마다 요약을 바로 받는다(대상이 수십 개라 Batch 대신 바로 호출). 6.6.

    받지 못했거나(LLMUnavailable) 사실에 없는 숫자가 들어간 요약은 None. 설정 문제(LLMError)는 그대로 올린다.
    """
    out: list[str | None] = []
    for t in targets:
        label = f"{t['dong_code']} {t['industry_code']}"
        try:
            summary = str(generate(build_prompt(t), SUMMARY_SCHEMA).get("summary", "")).strip()
        except client.LLMUnavailable as exc:
            logger.warning("요약을 받지 못함 %s: %s", label, exc)
            out.append(None)
            continue
        if not summary or not check_numbers(summary, t):
            logger.warning("요약을 버림(비었거나 사실에 없는 숫자) %s: %s", label, summary)
            out.append(None)
            continue
        out.append(summary)
    return out


def upsert_summaries(conn: Connection, rows: Sequence[dict[str, Any]]) -> int:
    """summary_cache에 넣거나 갱신한다(dong_code, industry_code, summary_text, model_name).

    출력: 넣거나 갱신한 행 수.
    """
    if not rows:
        return 0
    with transaction(conn):
        conn.execute(_UPSERT_SQL, list(rows))
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    """select_targets → build_prompt → (--generate · --write) request_summaries
    → (--write) upsert_summaries."""
    parser = argparse.ArgumentParser(description="시연 업종 상위 동의 요약문을 만든다.")
    parser.add_argument("--industries", required=True, help="업종 코드, 쉼표로 구분(예: CS100001,CS100010)")
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP_N, help=f"업종별 상위 몇 개 동(기본 {DEFAULT_TOP_N})"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true", help="요약을 받아 출력만 한다(적재 안 함)")
    mode.add_argument("--write", action="store_true", help="요약을 받아 summary_cache에 넣는다")
    args = parser.parse_args(argv)
    codes = [c.strip() for c in args.industries.split(",") if c.strip()]

    with create_engine(config.DATABASE_URL).connect() as conn:
        targets = select_targets(conn, codes, args.top)
        logger.info("대상 %d개 조합(업종 %s, 상위 %d)", len(targets), ", ".join(codes), args.top)
        if not targets:
            logger.error("대상이 없다. 업종 코드와 prediction(%s)을 확인할 것", SERVING_MODEL_VERSION)
            return 1
        if not (args.generate or args.write):
            print(build_prompt(targets[0]))
            logger.info("프롬프트만 출력했다(첫 조합). --generate 또는 --write로 요약을 받는다")
            return 0

        try:
            client.ensure_ready()
            summaries = request_summaries(targets)
        except client.LLMError as exc:
            logger.error("%s", exc)
            return 1
        rows = [
            {"dong_code": t["dong_code"], "industry_code": t["industry_code"],
             "summary_text": s, "model_name": LLM_MODEL}
            for t, s in zip(targets, summaries, strict=True) if s
        ]
        for t, s in zip(targets, summaries, strict=True):
            place = f"{t['district_name']} {t['dong_name']} · {t['industry_name']}"
            print(f"{t['score_rank']:>3}위 {place}: {s or '(실패)'}")
        logger.info("요약 %d / %d개", len(rows), len(targets))
        if args.write:
            n = upsert_summaries(conn, rows)
            conn.commit()
            logger.info("summary_cache에 %d행 넣음(model_name %s)", n, LLM_MODEL)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
