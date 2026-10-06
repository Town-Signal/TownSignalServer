"""자격증 정규화 LLM 폴백 주입 지점 (전체 명세 7.2 · 3장 11번).

요청 경로에서 LLM을 부르는 유일한 예외다. 설정 플래그(CERT_LLM_FALLBACK_ENABLED)로 끌 수 있고,
꺼져 있거나 LLM_API_KEY가 없으면 매퍼를 만들지 않는다 → 사전 매핑만 한다.
매퍼가 예외를 내거나 '없음'을 돌려주면 common.certificate_normalizer가 원문을 그대로 쓴다(제출은 막지 않는다).
"""

import logging

from api import config
from common.certificate_normalizer import Mapper

logger = logging.getLogger(__name__)


def build_mapper() -> Mapper | None:
    if not config.CERT_LLM_FALLBACK_ENABLED or not config.LLM_API_KEY:
        return None
    timeout = config.CERT_LLM_TIMEOUT_SECONDS

    def ask_llm(text: str, standards: tuple[str, ...]) -> str | None:
        # TODO(가정): 실제 Claude 호출(타임아웃 timeout초, 재시도 없음)은 아직 붙이지 않았다.
        # SDK 의존성 · 키 관리가 정해지면 '표준 목록 중 하나 또는 없음'을 묻는 호출로 바꾼다.
        logger.info("자격증 LLM 폴백 미구현 — 원문 사용(timeout=%ss): %s", timeout, text)
        raise NotImplementedError("자격증 LLM 폴백 호출이 아직 없다")

    return ask_llm
