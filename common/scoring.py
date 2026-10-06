"""백분위 순위와 종합점수 (전체 명세 7.7).

- 모집단은 같은 업종 · 같은 model_version의 서울 전체 행정동이다(후보 집합이 아님, 판정 22).
- 배치가 prediction 적재 때 미리 계산해 저장하고, 요청 경로는 읽기만 한다(6.5, 판정 30).
- 예산 여유는 점수에 넣지 않는다(판정 14).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from common.constants import MIN_STORE_COUNT, SCORE_WEIGHTS

FACTORS = ("sales", "survival", "growth")

Value = int | float | Decimal


def percent_rank(population: Iterable[Value | None], value: Value | None) -> float | None:
    """SQL PERCENT_RANK와 같다: (순위 − 1) ÷ (N − 1). 값이 클수록 1.0에 가깝다.

    NULL은 모집단에서 뺀다. 동률은 같은 백분위. N = 1이면 1.0(가정). value가 None이면 None.
    """
    if value is None:
        return None
    present = [v for v in population if v is not None]
    n = len(present)
    if n <= 1:
        return 1.0
    below = sum(1 for v in present if v < value)
    return below / (n - 1)


def percent_ranks(values: list[Value | None]) -> list[float | None]:
    """배치용: 모집단 전체의 백분위를 한 번에 구한다. 입력 순서를 유지한다."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    first_index: dict[Value, int] = {}
    for i, v in enumerate(present):
        first_index.setdefault(v, i)
    return [None if v is None else (1.0 if n <= 1 else first_index[v] / (n - 1)) for v in values]


def _redistribute(weights: Mapping[str, float], missing: set[str]) -> dict[str, float]:
    """값이 없는 항목의 가중치를 나머지 항목에 비례 배분한다. 빠진 항목은 0.0."""
    usable = {k: w for k, w in weights.items() if k not in missing}
    total = sum(usable.values())
    if total == 0:
        return {k: 0.0 for k in weights}
    return {k: (usable[k] / total if k in usable else 0.0) for k in weights}


def applied_weights(
    percentiles: Mapping[str, float | None], weights: Mapping[str, float] = SCORE_WEIGHTS
) -> dict[str, float]:
    """백분위 결측 여부만 보고 적용 가중치를 구한다(근거 분해 표시용). 점수는 다시 계산하지 않는다.

    예) 성장세 백분위가 없으면 {"sales": 0.5, "survival": 0.5, "growth": 0.0}
    """
    return _redistribute(weights, {k for k in weights if percentiles.get(k) is None})


DATA_STATUS_OK = "정상"
DATA_STATUS_SAMPLE_SHORT = "표본 부족"
DATA_STATUS_NO_PREDICTION = "예측 불가"


def data_status(has_prediction: bool, recent_avg_store_count: float | None) -> str:
    """7.6 표본 판정. 예측 행이 없으면 '예측 불가', 최근 4개 분기 평균 점포 수가 5 미만이면 '표본 부족'.

    TODO(가정): 예측은 있는데 점포 기록이 없으면(평균 None) '표본 부족'으로 본다.
    표본 부족도 순위에서 빼지 않는다(판정 20).
    """
    if not has_prediction:
        return DATA_STATUS_NO_PREDICTION
    if recent_avg_store_count is None or recent_avg_store_count < MIN_STORE_COUNT:
        return DATA_STATUS_SAMPLE_SHORT
    return DATA_STATUS_OK


@dataclass(frozen=True)
class ScoreResult:
    total_score: float | None  # 0~100, 소수 1자리. 지표가 모두 없으면 None
    applied_weights: dict[str, float]  # 재배분 후 가중치. 결측 항목은 0.0
    contributions: dict[str, float | None]  # 적용 가중치 × 백분위 × 100 (근거 분해 2.6.3)


def total_score(
    percentiles: Mapping[str, float | None], weights: Mapping[str, float] = SCORE_WEIGHTS
) -> ScoreResult:
    """total_score = round(100 × Σ w'(i) × p(i), 1). 결측 지표는 빼고 가중치를 비례 재배분한다.

    예) 0.4 × 0.88 + 0.4 × 0.75 + 0.2 × 0.65 = 0.782 → 78.2
        성장세 없음: 0.5 × 0.55 + 0.5 × 0.60 = 0.575 → 57.5
    """
    missing = {k for k in weights if percentiles.get(k) is None}
    applied = _redistribute(weights, missing)
    if len(missing) == len(weights):
        return ScoreResult(None, applied, {k: None for k in weights})

    contributions = {
        k: None if k in missing else Decimal(str(applied[k])) * Decimal(str(percentiles[k])) * 100
        for k in weights
    }
    raw = sum(c for c in contributions.values() if c is not None)
    score = float(raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return ScoreResult(
        total_score=score,
        applied_weights=applied,
        # TODO(가정): 기여도는 소수 4자리로 둔다.
        # 화면에서 1자리로 반올림하면 합이 total_score와 0.1 차이 날 수 있다
        contributions={k: None if c is None else round(float(c), 4) for k, c in contributions.items()},
    )
