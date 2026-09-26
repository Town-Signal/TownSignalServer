"""지원사업표(support_program)를 채우는 배치 작업.

실행: python -m batch.jobs.build_support_program
주기: 주 1회 (공고가 수시로 바뀌므로 세 표 중 이것만 매주 돌린다)

수집 → LLM 구조화 → 팀 검수 → 적재 순서이며, 검수를 통과한 행만
verified=true로 표시한다. verified가 거짓인 행은 API 매칭에 쓰이지 않는다.
"""

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("지원사업표 생성 — 아직 구현 전이다")
    raise NotImplementedError("K-Startup 수집과 LLM 구조화 구현 후 연결한다")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
