"""prediction 점수 선계산 (전체 명세 6.5 · 7.7).

같은 industry_code · model_version 안의 서울 전체 행정동을 모집단으로 백분위 · total_score · score_rank를
계산해 같은 행에 채운다. 계산은 common.scoring에 맡기고 여기서는 묶고 정렬만 한다.
요청 경로(④ · ⑫ · ⑭ · ⑱)는 이 값을 읽기만 한다.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from common.scoring import percent_ranks, total_score

# 지표 → (prediction 원값 칼럼, 백분위 칼럼)
FACTOR_COLUMNS = {
    "sales": ("sales_p50", "sales_percentile"),
    "survival": ("survival_p50", "survival_percentile"),
    "growth": ("growth_rate", "growth_percentile"),
}

_PERCENTILE_PLACES = Decimal("0.0001")  # NUMERIC(5,4)
_SCORE_PLACES = Decimal("0.1")  # NUMERIC(5,1)


def _to_percentile(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(_PERCENTILE_PLACES, rounding=ROUND_HALF_UP)


def _rank_key(row: dict[str, Any]) -> tuple:
    # TODO(가정): 동점은 sales_p50 내림차순 → dong_code 오름차순(7.6 정렬 규칙과 같게)
    score = row["total_score"]
    return (score is None, -(score or 0), -(row["sales_p50"] or 0), row["dong_code"])


def fill_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """prediction 행 목록을 받아 점수 칼럼을 채운 새 목록을 돌려준다(입력 순서 유지).

    total_score는 저장되는 값(소수 4자리 백분위)으로 계산한다. 그래야 DB에 있는 백분위로
    근거 분해를 다시 계산해도 total_score와 맞는다.
    """
    result = [dict(row) for row in rows]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in result:
        groups[(row["industry_code"], row["model_version"])].append(row)

    for group in groups.values():
        for raw_col, pct_col in FACTOR_COLUMNS.values():
            for row, pct in zip(group, percent_ranks([r[raw_col] for r in group]), strict=True):
                row[pct_col] = _to_percentile(pct)

        for row in group:
            percentiles = {
                factor: None if row[pct_col] is None else float(row[pct_col])
                for factor, (_, pct_col) in FACTOR_COLUMNS.items()
            }
            score = total_score(percentiles).total_score
            row["total_score"] = None if score is None else Decimal(str(score)).quantize(_SCORE_PLACES)

        rank = 0
        for row in sorted(group, key=_rank_key):
            if row["total_score"] is None:
                row["score_rank"] = None
            else:
                rank += 1
                row["score_rank"] = rank
    return result
