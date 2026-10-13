"""prediction 점수 선계산(batch.pipeline.scores) — 생존 백분위의 원값 선택."""

from batch.pipeline.scores import fill_scores


def _row(dong, **values):
    base = {
        "dong_code": dong,
        "industry_code": "CS100001",
        "model_version": "xgb_v1",
        "sales_p50": 100,
        "survival_p50": None,
        "survival_prob_3y": None,
        "growth_rate": None,
    }
    return {**base, **values}


def test_survival_percentile_uses_three_year_probability():
    rows = fill_scores([_row("A", survival_prob_3y=0.5), _row("B", survival_prob_3y=0.8)])
    pct = {r["dong_code"]: float(r["survival_percentile"]) for r in rows}
    assert pct == {"A": 0.0, "B": 1.0}


def test_survival_percentile_falls_back_to_months():
    rows = fill_scores([_row("A", survival_p50=40), _row("B", survival_p50=20)])
    pct = {r["dong_code"]: float(r["survival_percentile"]) for r in rows}
    assert pct == {"A": 1.0, "B": 0.0}


def test_no_survival_redistributes_weight():
    rows = fill_scores([_row("A", sales_p50=10), _row("B", sales_p50=20)])
    assert all(r["survival_percentile"] is None for r in rows)
    assert {r["dong_code"]: float(r["total_score"]) for r in rows} == {"A": 0.0, "B": 100.0}
