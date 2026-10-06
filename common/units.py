"""단위 변환 (전체 명세 판정 31).

매출은 분기 기준으로 저장 · 계산하고, 화면에는 월 매출(분기 ÷ 3, 버림)로 내린다.
"""


def quarterly_to_monthly(value: int | None) -> int | None:
    """분기 매출(원) → 월 매출(원). 3으로 나눠 버린다. 결측은 0이 아니라 None 그대로."""
    return None if value is None else int(value) // 3
