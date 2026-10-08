"""LLM 호출(공고 구조화용) 인터페이스. 제공사에 의존하는 코드는 이 파일에만 둔다.

명세 6.6 · 판정 39: 운영 제공사는 Anthropic Claude API(claude-opus-5-5 · Batch API)다. 아직 연결하지 않았다.
structure.structure_notices는 generate를 인자로 받으므로 호출 함수를 바꿔 끼워 쓸 수 있다.
"""

from typing import Any


class LLMError(Exception):
    """호출 설정 문제(키 없음 · 잘못된 요청 · 권한 · 모델 없음). 다시 해도 소용없으니 배치를 멈춘다."""


class LLMUnavailable(Exception):
    """일시 오류가 계속되거나 응답을 읽을 수 없다. 그 공고만 건너뛴다."""


def generate_json(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """프롬프트를 보내 schema(표준 JSON Schema)를 따르는 JSON 객체를 받는다.

    일시 오류(429 · 5xx · 접속 실패)는 간격을 늘려 다시 시도하고 계속되면 LLMUnavailable을, 그 밖의 오류와
    키가 없을 때는 LLMError를 올린다.
    """
    raise NotImplementedError("운영 제공사(Anthropic Claude) 호출은 아직 구현 전이다(6.6)")
