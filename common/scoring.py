"""추천 순위 산정 (설계서 7.5절).

정규화 후 가중합. 각 항의 기여도를 함께 돌려주어 화면에서 근거로 쓴다.
"""

from common.constants import SCORE_WEIGHTS

FACTORS = ("sales", "survival", "room", "growth")


def min_max(values):
    """후보 집합 안에서 0~1로 정규화한다. 값이 모두 같으면 0.5로 둔다."""
    present = [v for v in values if v is not None]
    if not present:
        return [None] * len(values)
    low, high = min(present), max(present)
    if high == low:
        return [None if v is None else 0.5 for v in values]
    return [None if v is None else (v - low) / (high - low) for v in values]


def _redistribute(weights, missing):
    """값이 없는 항목의 가중치를 나머지 항목에 비례 배분한다."""
    usable = {k: w for k, w in weights.items() if k not in missing}
    total = sum(usable.values())
    if total == 0:
        return {k: 0.0 for k in weights}
    return {k: w / total for k, w in usable.items()}


def rank(candidates, weights=None):
    """후보 목록에 점수와 항목별 기여도를 붙여 내림차순으로 돌려준다.

    candidates: sales · survival · room · growth 키를 가진 dict 목록.
                growth처럼 값이 없을 수 있는 항목은 None으로 둔다.
    """
    weights = weights or SCORE_WEIGHTS
    normalized = {f: min_max([c.get(f) for c in candidates]) for f in FACTORS}

    scored = []
    for i, candidate in enumerate(candidates):
        values = {f: normalized[f][i] for f in FACTORS}
        missing = {f for f, v in values.items() if v is None}
        applied = _redistribute(weights, missing)
        factors = {f: round(applied.get(f, 0.0) * v, 4) for f, v in values.items() if v is not None}
        scored.append({**candidate, "score": round(sum(factors.values()), 4), "factors": factors})

    return sorted(scored, key=lambda c: c["score"], reverse=True)
