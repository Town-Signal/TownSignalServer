"""행정동 × 업종 요약 생성 — 담당: AI. 지금은 골격(인터페이스)만 있다.

실행: python -m batch.jobs.build_summary_cache  (팀원 PC에서, prediction 갱신 후 — 판정 38 · 39)
주기: 기준 분기가 바뀔 때. 1순위 릴리스에서는 만들지 않는다(6.6). 명세 6.6 LLM ② · 7.9 · 판정 9 · 10.

입력 표: prediction · store_quarterly · sales_quarterly · population_quarterly · district_rent
출력 표: summary_cache (dong_code, industry_code)

규칙: 숫자는 LLM이 만들지 않고 서버가 계산한 값만 넣는다(6.6 공통 규칙). 사용자별 예산 여유는 담지 않는다.
요청 경로(API)는 이 표를 읽기만 하고 LLM을 부르지 않는다.
"""

import logging
from typing import Any

from sqlalchemy import Connection

logger = logging.getLogger(__name__)


def select_targets(conn: Connection) -> list[dict[str, Any]]:
    """요약 대상 조합: data_status 정상 · 최근 4개 분기 평균 점포 5개 이상(6.6, 약 2만 건 가정)."""
    raise NotImplementedError("요약 대상 선정은 AI 담당이 구현한다(6.6)")


def build_prompt(target: dict[str, Any]) -> str:
    """⑪ ⑫ 지표(업종 분포 · 매출 추이 · 임대료 · 예측 구간 · 성장세 · 유동인구)를 변수로 넣은 프롬프트."""
    raise NotImplementedError("프롬프트 작성은 AI 담당이 구현한다(6.6)")


def request_summaries(prompts: list[str]) -> list[str]:
    """LLM Batch API로 한~두 문장 요약을 받는다(공통 지시문 캐싱). 6.6."""
    raise NotImplementedError("요약 생성 호출은 AI 담당이 구현한다(6.6)")


def upsert_summaries(conn: Connection, rows: list[dict[str, Any]]) -> int:
    """summary_cache에 넣거나 갱신한다(dong_code, industry_code, summary_text, model_name).

    출력: 넣거나 갱신한 행 수.
    """
    raise NotImplementedError("요약 적재는 AI 담당이 구현한다(6.6)")


def main() -> None:
    """select_targets → build_prompt → request_summaries → upsert_summaries."""
    raise NotImplementedError("요약 생성 · 적재는 아직 구현 전이다(6.6)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
