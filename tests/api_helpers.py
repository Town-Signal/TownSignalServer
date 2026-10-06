"""API 테스트 공용 확인 함수 — 응답이 8.1.1 봉투인지 보고 필요한 부분을 꺼낸다."""

from datetime import date

FIXED_TODAY = date(2026, 10, 5)  # API 테스트의 '오늘'. 더미 공고 마감일(2026-10-06 ~)이 모두 남아 있는 날

ENVELOPE_KEYS = {"status", "code", "message", "data", "errors", "warnings", "meta"}


def success(response) -> dict:
    """200 SUCCESS 봉투 전체를 돌려준다(warnings · meta를 볼 때)."""
    body = response.json()
    assert response.status_code == 200, body
    assert set(body) == ENVELOPE_KEYS and "detail" not in body
    assert (body["status"], body["code"], body["message"], body["errors"]) == ("SUCCESS", "OK", None, [])
    return body


def failure(response, http_status: int, code: str) -> dict:
    body = response.json()
    assert response.status_code == http_status, body
    assert set(body) == ENVELOPE_KEYS and "detail" not in body
    assert (body["status"], body["code"], body["data"]) == ("FAILURE", code, None)
    assert isinstance(body["message"], str) and body["message"]
    return body


def single_error(response) -> dict:
    """422 VALIDATION_ERROR이고 칸 오류가 하나일 때 그 항목."""
    body = failure(response, 422, "VALIDATION_ERROR")
    assert len(body["errors"]) == 1, body["errors"]
    return body["errors"][0]


def warning_codes(body: dict) -> list[str]:
    return [w["code"] for w in body["warnings"]]
