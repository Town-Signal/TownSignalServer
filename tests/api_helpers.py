"""API 테스트 공용 확인 함수 — 응답이 8.1.1 봉투인지 보고 필요한 부분을 꺼낸다."""

from datetime import date

from sqlalchemy import text

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


def expected_store_trend(db, dong_code: str, industry_code: str = "CS100001") -> dict | None:
    """store_quarterly에서 7.12 점포 수 증감을 따로 계산한다(서버 코드를 쓰지 않는 기대값).

    더미 분기는 20233 ~ 20252라 2024Q1 이후 6개 → 3분기씩 비교, 신뢰도 '낮음'.
    """
    counts = dict(
        db.execute(
            text(
                "SELECT year_quarter, store_count FROM store_quarterly "
                "WHERE dong_code = :d AND industry_code = :i"
            ),
            {"d": dong_code, "i": industry_code},
        ).all()
    )
    if not counts:
        return None
    prior = [counts.get(q) for q in ("20241", "20242", "20243")]
    recent = [counts.get(q) for q in ("20244", "20251", "20252")]
    rate = None
    if None not in prior + recent and sum(prior) > 0:
        rate = round(sum(recent) / sum(prior) - 1, 3)
    latest = max(counts)
    return {
        "latest_count": counts[latest],
        "change_rate": rate,
        "confidence": None if rate is None else "낮음",
        "latest_quarter": latest,
    }
