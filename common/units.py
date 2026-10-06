"""단위 변환 (전체 명세 판정 31).

매출은 분기 기준으로 저장 · 계산하고, 화면에는 월 매출(분기 ÷ 3, 버림)로 내린다.
"""

from decimal import ROUND_HALF_UP, Decimal


def quarterly_to_monthly(value: int | None) -> int | None:
    """분기 매출(원) → 월 매출(원). 3으로 나눠 버린다. 결측은 0이 아니라 None 그대로."""
    return None if value is None else int(value) // 3


def per_store_amount(amount: int | None, store_count: int | None) -> int | None:
    """점포당 매출 = 매출 ÷ 점포 수, 사사오입(8.4 ⑪). 점포 수가 없거나 0이면 None(0으로 채우지 않는다)."""
    if amount is None or not store_count:
        return None
    return int((Decimal(amount) / Decimal(store_count)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
