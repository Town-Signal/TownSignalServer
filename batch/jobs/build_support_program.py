"""지원사업표(support_program)를 채우는 배치 작업 — 담당: 백엔드(지원사업). 지금은 골격(인터페이스)만 있다.

실행: python -m batch.jobs.build_support_program  (EC2 cron에서는 run_weekly가 부른다)
주기: 주 1회. 명세 4.8 · 6.6 LLM ① · 12.1.

흐름(4.6 run_weekly ① ②)
1. 지난주에 제출한 LLM Batch 결과를 받아 검수 대기 행으로 적재한다(collect_pending_llm_results).
2. 이번 주 새 공고를 모아(fetch_kstartup_notices · 구 전용은 수작업)
   LLM 구조화를 요청한다(submit_structuring_batch).
3. 구조화 결과는 upsert_programs로 support_program에 넣는다. verified_by는 비워 두고 사람이 원문과 대조해
   검수하면 채운다. verified_by IS NOT NULL인 행만 API 매칭에 쓴다(판정 24).

규칙: 공고 원문을 raw_text에 반드시 보관한다. 금액은 원 단위 정수. 모호한 요건은 해석하지 않고 판단보류
(verified_by NULL)로 둔다. eligibility는 7.3 조건 트리 문법이며 자격증 이름은 common.certificate_normalizer의
표준명을 쓴다. 이 모듈은 배치 전용 의존성(xgboost 등)을 import하지 않는다.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawNotice:
    """수집한 공고 원문 한 건."""

    source_url: str | None
    title: str
    agency: str | None
    raw_text: str  # 원문 전체(검수 · 정확도 측정의 전제, 4.8)
    district_code: str | None  # 구 전용이면 자치구 코드, 전국이면 None
    apply_start: date | None = None
    apply_end: date | None = None


def fetch_kstartup_notices(api_key: str) -> list[RawNotice]:
    """K-Startup 오픈API(PUBLIC_DATA_API_KEY)로 전국 공고를 모은다.

    입력: 공공데이터 인증키. 출력: RawNotice 목록. DB에 쓰지 않는다.
    """
    raise NotImplementedError("K-Startup 수집은 지원사업 담당이 구현한다(4.8)")


def submit_structuring_batch(notices: list[RawNotice]) -> str:
    """공고 원문 → 구조화(JSON 스키마 강제) LLM Batch 요청을 제출하고 batch id를 돌려준다(6.6 LLM ①).

    출력 필드: name · agency · amount_max(원) · district_code · is_exclusive · apply_start · apply_end ·
    eligibility(7.3 트리) · source_url. 결과는 다음 주 collect_pending_llm_results가 받는다.
    """
    raise NotImplementedError("LLM 구조화 요청은 지원사업 담당이 구현한다(4.8 · 6.6)")


def collect_pending_llm_results(conn: Connection) -> int:
    """지난주에 제출한 Batch 결과를 받아 upsert_programs로 검수 대기 행(verified_by NULL)으로 적재한다.

    출력: 적재한 행 수. 적재 표: support_program.
    """
    raise NotImplementedError("LLM Batch 결과 수집은 지원사업 담당이 구현한다(4.6 ①)")


def upsert_programs(conn: Connection, programs: list[dict[str, Any]]) -> int:
    """구조화된 공고를 support_program에 넣거나 갱신한다.

    적재 키(4.8, 가정): source_url이 같으면 같은 공고, source_url이 없으면 name + agency.
    갱신해도 이미 검수된 행의 verified_by는 원문이 바뀌지 않았으면 유지한다(가정 — 담당이 확정).
    출력: 넣거나 갱신한 행 수.
    """
    raise NotImplementedError("지원사업 적재는 지원사업 담당이 구현한다(4.8)")


def main() -> None:
    """run_weekly ① · ②: 지난주 결과 수집 → 이번 주 공고 수집 · 구조화 요청."""
    raise NotImplementedError("지원사업 수집 · 구조화 · 적재는 아직 구현 전이다(4.8)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
