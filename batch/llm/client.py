"""LLM 호출(공고 구조화용) 인터페이스. 제공사에 의존하는 코드는 이 파일에만 둔다.

명세 6.6 · 판정 39: 운영 제공사는 Anthropic Claude API(claude-opus-5-5 · Batch API)다. 아직 연결하지 않았다.
운영은 Batch로 한다: 이번 주에 submit_batch로 제출하고, 결과는 다음 주 fetch_batch_results로 받는다.
generate_json(바로 응답을 받는 호출)은 개발 중 결과를 곧바로 확인하는 데 쓴다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class LLMError(Exception):
    """호출 설정 문제(키 없음 · 잘못된 요청 · 권한 · 모델 없음). 다시 해도 소용없으니 배치를 멈춘다."""


class LLMUnavailable(Exception):
    """일시 오류가 계속되거나 응답을 읽을 수 없다. 그 공고만 건너뛴다."""


@dataclass(frozen=True)
class BatchRequest:
    """Batch로 보낼 요청 한 건. custom_id는 결과가 돌아왔을 때 어느 공고의 답인지 맞추는 꼬리표다."""

    custom_id: str
    prompt: str


@dataclass(frozen=True)
class BatchResult:
    """Batch 결과 한 건. data가 None이면 그 요청만 실패한 것이다."""

    custom_id: str
    data: dict[str, Any] | None


def generate_json(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """프롬프트를 보내 schema(표준 JSON Schema)를 따르는 JSON 객체를 바로 받는다(개발 · 검증용).

    일시 오류(429 · 5xx · 접속 실패)는 간격을 늘려 다시 시도하고 계속되면 LLMUnavailable을, 그 밖의 오류와
    키가 없을 때는 LLMError를 올린다.
    """
    raise NotImplementedError("운영 제공사(Anthropic Claude) 호출은 아직 구현 전이다(6.6)")


def submit_batch(requests: list[BatchRequest], schema: dict[str, Any]) -> str:
    """요청들을 Batch로 제출하고 batch id를 돌려준다. 결과는 나중에 fetch_batch_results로 받는다.

    모든 요청이 같은 schema(표준 JSON Schema)를 따른다.
    """
    raise NotImplementedError("운영 제공사(Anthropic Claude) Batch 제출은 아직 구현 전이다(6.6)")


def fetch_batch_results(since: datetime) -> list[BatchResult]:
    """since 이후 끝난 Batch들의 결과를 모두 돌려준다. 아직 끝나지 않은 batch는 건너뛴다.

    같은 결과를 여러 번 받아도 되도록 "받았다"는 표시는 하지 않는다. 중복 적재는 호출하는 쪽이 막는다.
    """
    raise NotImplementedError("운영 제공사(Anthropic Claude) Batch 결과 조회는 아직 구현 전이다(6.6)")
