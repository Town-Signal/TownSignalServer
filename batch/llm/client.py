"""LLM 호출(공고 구조화용). 제공사에 의존하는 코드는 이 파일에만 둔다.

명세 6.6 · 판정 39: 운영 제공사는 Anthropic Claude API(claude-opus-5-5 · Batch API)다.
운영은 Batch로 한다: 이번 주에 submit_batch로 제출하고, 결과는 다음 주 fetch_batch_results로 받는다.
generate_json(바로 응답을 받는 호출)은 개발 중 결과를 곧바로 확인하는 데 쓴다.
키는 환경변수 LLM_API_KEY(.env)에서 읽는다.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from batch import config  # noqa: F401  .env를 읽는다(load_dotenv)
from common.constants import LLM_EFFORT, LLM_MAX_RETRIES, LLM_MAX_TOKENS, LLM_MODEL


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


def _key() -> str:
    key = os.getenv("LLM_API_KEY", "")
    if not key:
        raise LLMError("LLM_API_KEY가 비어 있다")
    return key


def _client() -> Any:
    import anthropic  # 여기서 import한다. 기본 CI(api)는 이 패키지 없이 이 모듈을 import한다(pypdf처럼)

    return anthropic.Anthropic(api_key=_key(), max_retries=LLM_MAX_RETRIES)


@contextmanager
def _translate_errors() -> Iterator[None]:
    """SDK 오류를 LLMError(설정 문제) · LLMUnavailable(일시 오류)로 바꾼다. SDK가 이미 다시 시도한 뒤다."""
    import anthropic

    try:
        yield
    except anthropic.APIConnectionError as exc:  # 접속 실패 · 시간 초과
        raise LLMUnavailable(f"LLM에 접속하지 못했다: {exc}") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            raise LLMUnavailable(f"LLM이 응답하지 않는다: HTTP {exc.status_code}") from exc
        raise LLMError(f"LLM 호출 실패: HTTP {exc.status_code} {str(exc.message)[:200]}") from exc


def _to_any_of(node: Any) -> Any:
    """["타입", "null"] 꼴 타입을 anyOf로 바꾼다. 구조화 출력은 anyOf를 지원한다고 문서에 적혀 있다.

    스키마는 제공사와 무관한 표준 JSON Schema로 받으므로(structure.response_schema) 바꾸는 일은 여기서 한다.
    enum의 null은 null 쪽 가지가 맡는다. 원본 스키마는 바꾸지 않는다.
    """
    if isinstance(node, list):
        return [_to_any_of(v) for v in node]
    if not isinstance(node, dict):
        return node
    node = {k: _to_any_of(v) for k, v in node.items()}
    types = node.get("type")
    if isinstance(types, list) and len(types) == 2 and "null" in types:
        rest = {k: v for k, v in node.items() if k != "type"}
        if "enum" in rest:
            rest["enum"] = [v for v in rest["enum"] if v is not None]
        return {"anyOf": [{"type": next(t for t in types if t != "null"), **rest}, {"type": "null"}]}
    return node


def _params(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """한 건의 요청 본문. 바로 호출(generate_json)과 Batch 제출(submit_batch)이 같이 쓴다."""
    return {
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "output_config": {
            "effort": LLM_EFFORT,
            "format": {"type": "json_schema", "schema": _to_any_of(schema)},
        },
        "messages": [{"role": "user", "content": prompt}],
    }


def _parse(message: Any) -> dict[str, Any]:
    """응답에서 JSON 객체를 꺼낸다. 거절 · 길이 초과 · JSON이 아닌 응답은 LLMUnavailable이다."""
    if message.stop_reason in ("refusal", "max_tokens"):
        raise LLMUnavailable(f"LLM 응답을 쓸 수 없다: {message.stop_reason}")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LLMUnavailable("LLM 응답이 JSON이 아니다") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("LLM 응답이 JSON 객체가 아니다")
    return data


def ensure_ready() -> None:
    """운영 제공사를 쓸 준비가 됐는지 확인한다. 안 됐으면 수집을 시작하기 전에 멈추게 LLMError를 올린다.

    키가 있는지만 본다. 네트워크 호출은 하지 않는다(키가 맞는지는 첫 호출에서 드러난다).
    """
    _key()


def generate_json(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """프롬프트를 보내 schema(표준 JSON Schema)를 따르는 JSON 객체를 바로 받는다(개발 · 검증용).

    일시 오류(429 · 5xx · 접속 실패)는 SDK가 간격을 늘려 다시 시도하고 계속되면 LLMUnavailable을,
    그 밖의 오류와 키가 없을 때는 LLMError를 올린다. 거절 · 길이 초과 · JSON이 아닌 응답은 LLMUnavailable이다.
    """
    with _translate_errors():
        message = _client().messages.create(**_params(prompt, schema))
    return _parse(message)


def submit_batch(requests: list[BatchRequest], schema: dict[str, Any]) -> str:
    """요청들을 Batch로 제출하고 batch id를 돌려준다. 결과는 나중에 fetch_batch_results로 받는다.

    모든 요청이 같은 schema(표준 JSON Schema)를 따른다. 오류는 generate_json과 같다.
    TODO(가정): 한 batch의 한도(10만 건 · 256MB)는 나누지 않는다. 주간 서울 공고 수로는 닿지 않는다.
    """
    items = [{"custom_id": r.custom_id, "params": _params(r.prompt, schema)} for r in requests]
    with _translate_errors():
        return _client().messages.batches.create(requests=items).id


def fetch_batch_results(since: datetime) -> list[BatchResult]:
    """since 이후 끝난 Batch들의 결과를 모두 돌려준다. 아직 끝나지 않은 batch는 건너뛴다.

    요청이 실패 · 취소 · 만료됐거나 응답을 읽을 수 없으면 그 건의 data만 None이다.
    같은 결과를 여러 번 받아도 되도록 "받았다"는 표시는 하지 않는다. 중복 적재는 호출하는 쪽이 막는다.
    since에 시간대가 없으면 UTC로 본다.
    """
    since = since if since.tzinfo else since.replace(tzinfo=UTC)
    client = _client()
    results = []
    with _translate_errors():
        for batch in client.messages.batches.list():
            if batch.processing_status != "ended" or batch.ended_at is None or batch.ended_at < since:
                continue
            for item in client.messages.batches.results(batch.id):
                results.append(BatchResult(item.custom_id, _batch_data(item)))
    return results


def _batch_data(item: Any) -> dict[str, Any] | None:
    if item.result.type != "succeeded":
        return None
    try:
        return _parse(item.result.message)
    except LLMUnavailable:
        return None
