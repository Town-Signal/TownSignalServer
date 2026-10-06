"""④ 행정동 추천 · ⑤ 추천 스냅샷 재조회 · ⑥ 예산 여유 (전체 명세 7.6 ~ 7.8 · 8.3 · 8.6 · 판정 41).

- ②가 저장한 calculated_budgets를 재계산 없이 읽는다(3장 4번).
- 점수 · 백분위 · 순위 재료는 prediction에 배치가 채운 값 그대로다(판정 30). 여기서 점수를 계산하지 않는다.
- 예산 여유는 값만 옮긴다. 정렬 · 점수에 쓰지 않는다(판정 14).
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from api.errors import AppError
from api.repositories import program_repository as program_repo
from api.repositories import recommendation_repository as repo
from api.schemas.program import DistrictBudgetResult
from api.schemas.recommendation import (
    DongRankResult,
    MarginResponse,
    RecItemResult,
    RecommendationExecuteRequest,
    RecommendationResponse,
    RecommendationSnapshot,
    RecommendationSummary,
    ScoreBreakdown,
)
from api.schemas.region import BudgetSummary
from common.constants import PASSED_OK, PASSED_UNKNOWN, SERVING_MODEL_VERSION
from common.scoring import DATA_STATUS_SAMPLE_SHORT, applied_weights, data_status
from common.units import quarterly_to_monthly

# 8.3 ④ 예시 문구 그대로
DISCLAIMER = (
    "본 서비스의 추정 임대비용은 자치구 평균 기준이므로 동일 자치구 내 행정동은 금액이 동일하게 표시되며, "
    "권리금·인테리어·집기 비용은 포함되지 않습니다. "
    "예측 결과는 참고 정보이며 최종 판단과 책임은 사용자에게 있습니다."
)


def _rec_not_found() -> AppError:
    return AppError("REC_NOT_FOUND", 404, "추천 결과를 찾을 수 없어요. 다시 추천받아 주세요.")


def _float(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _load(session: Session, rec_id: uuid.UUID):
    rec = repo.get_recommendation(session, rec_id)
    if rec is None:  # 없거나 보관 기간(30일)이 지나 정리됨 → 프론트는 ts-last의 rec_id만 지운다
        raise _rec_not_found()
    return rec


def _industry_names(session: Session, industry_code: str) -> tuple[str, str]:
    industry = program_repo.get_industry(session, industry_code)
    # TODO(가정): 스냅샷 이후 업종 마스터에서 빠졌으면 코드를 이름 대신 쓴다
    return (industry.name, industry.category) if industry else (industry_code, "")


def execute(session: Session, req: RecommendationExecuteRequest) -> RecommendationResponse:
    rec = _load(session, req.rec_id)
    snapshot: dict[str, Any] = rec.calculated_budgets
    industry_code: str = rec.input_condition["industry_code"]
    industry_name, category = _industry_names(session, industry_code)

    budgets = {d["district_code"]: d for d in snapshot["district_budgets"]}
    eligible_districts = [code for code, d in budgets.items() if d["passed"] in (PASSED_OK, PASSED_UNKNOWN)]
    rows = repo.list_candidates(session, industry_code, SERVING_MODEL_VERSION, eligible_districts, req.top_k)

    dong_codes = [row.Prediction.dong_code for row in rows]
    store_averages = repo.recent_store_averages(session, industry_code, dong_codes)
    summaries = repo.get_summaries(session, industry_code, dong_codes)

    results: list[DongRankResult] = []
    for rank_no, row in enumerate(rows, start=1):
        p = row.Prediction
        budget = budgets[row.district_code]
        status = data_status(True, store_averages.get(p.dong_code))
        percentiles = {
            "sales": _float(p.sales_percentile),
            "survival": _float(p.survival_percentile),
            "growth": _float(p.growth_percentile),
        }
        results.append(
            DongRankResult(
                rank_no=rank_no,
                dong_code=p.dong_code,
                dong_name=row.dong_name,
                district_code=row.district_code,
                district_name=row.district_name,
                passed=budget["passed"],
                estimated_rent_cost=budget["estimated_rent_cost"],
                budget_margin=budget["budget_margin"],
                rent_confidence=budget["rent_confidence"],
                sales_quarterly_p50=p.sales_p50,
                sales_quarterly_range=(p.sales_p10, p.sales_p90),
                sales_monthly_p50=quarterly_to_monthly(p.sales_p50),
                sales_monthly_range=(quarterly_to_monthly(p.sales_p10), quarterly_to_monthly(p.sales_p90)),
                survival_p50=_float(p.survival_p50),
                survival_range=(_float(p.survival_p10), _float(p.survival_p90)),
                growth_rate=_float(p.growth_rate),
                growth_confidence=p.growth_confidence,
                data_status=status,
                is_residential=status == DATA_STATUS_SAMPLE_SHORT,
                score_breakdown=ScoreBreakdown(
                    sales_percentile=percentiles["sales"],
                    survival_percentile=percentiles["survival"],
                    growth_percentile=percentiles["growth"],
                    # TODO(가정): prediction에 적용 가중치 칼럼이 없어 백분위 결측 여부로 가중치만 구한다.
                    # total_score는 배치가 저장한 값을 그대로 쓴다(다시 계산하지 않음)
                    applied_weights=applied_weights(percentiles),
                    total_score=_float(p.total_score),
                ),
                summary_text=summaries.get(p.dong_code),
                geo_code=row.geo_code,
            )
        )

    repo.replace_rec_items(
        session,
        rec.rec_id,
        [
            {
                "rank_no": r.rank_no,
                "dong_code": r.dong_code,
                "budget_margin": r.budget_margin,
                "survival_p50": r.survival_p50,
                "sales_p50": r.sales_quarterly_p50,
            }
            for r in results
        ],
    )
    session.commit()

    return RecommendationResponse(
        rec_id=rec.rec_id,
        base_quarter=snapshot.get("base_quarter"),
        model_version=SERVING_MODEL_VERSION,
        industry_code=industry_code,
        industry_name=industry_name,
        degraded=snapshot.get("degraded", False),
        disclaimer=DISCLAIMER,
        summary=RecommendationSummary(
            recommended_count=len(results),
            passed_district_count=snapshot["summary"]["passed_district_count"],
            eligible_district_count=snapshot["summary"]["eligible_district_count"],
            industry_code=industry_code,
            industry_name=industry_name,
            category=category,
        ),
        recommendations=results,
    )


def snapshot(session: Session, rec_id: uuid.UUID) -> RecommendationSnapshot:
    """순위 · 예산 여유는 rec_item, 점수 · 예측 · 성장세 · data_status는 응답 시점 최신 prediction.

    (판정 41)
    """
    rec = _load(session, rec_id)
    saved: dict[str, Any] = rec.calculated_budgets
    industry_code: str = rec.input_condition["industry_code"]
    industry_name, _ = _industry_names(session, industry_code)

    rows = repo.list_rec_items_with_prediction(session, rec.rec_id, industry_code, SERVING_MODEL_VERSION)
    store_averages = repo.recent_store_averages(
        session, industry_code, [row.dong_code for row in rows if row.Prediction is not None]
    )
    items = []
    for row in rows:
        p = row.Prediction  # 예측 행이 없어졌으면 None → 값 null · '예측 불가'
        items.append(
            RecItemResult(
                rank_no=row.rank_no,
                dong_code=row.dong_code,
                dong_name=row.dong_name,
                district_code=row.district_code,
                district_name=row.district_name,
                budget_margin=row.budget_margin,
                total_score=_float(p.total_score) if p else None,
                survival_p50=_float(p.survival_p50) if p else None,
                survival_range=(_float(p.survival_p10), _float(p.survival_p90)) if p else (None, None),
                sales_p50=p.sales_p50 if p else None,
                sales_monthly_p50=quarterly_to_monthly(p.sales_p50) if p else None,
                sales_monthly_range=(
                    (quarterly_to_monthly(p.sales_p10), quarterly_to_monthly(p.sales_p90))
                    if p
                    else (None, None)
                ),
                growth_rate=_float(p.growth_rate) if p else None,
                growth_confidence=p.growth_confidence if p else None,
                data_status=data_status(p is not None, store_averages.get(row.dong_code)),
            )
        )

    return RecommendationSnapshot(
        rec_id=rec.rec_id,
        created_at=rec.created_at,
        base_quarter=saved.get("base_quarter"),
        input_condition=rec.input_condition,
        industry_name=industry_name,
        degraded=saved.get("degraded", False),
        summary=BudgetSummary.model_validate(saved["summary"]),
        district_budgets=[DistrictBudgetResult.model_validate(d) for d in saved["district_budgets"]],
        items=items,
    )


def margin(session: Session, rec_id: uuid.UUID, dong_code: str) -> MarginResponse:
    """calculated_budgets에서 그 동이 속한 자치구 값을 꺼낸다. 같은 구의 동은 모두 같은 값이다."""
    rec = _load(session, rec_id)
    dong = repo.get_dong(session, dong_code)
    budget = None
    if dong is not None:
        saved = rec.calculated_budgets["district_budgets"]
        budget = next((d for d in saved if d["district_code"] == dong.district_code), None)
    if dong is None or budget is None:
        raise AppError("DONG_NOT_FOUND", 404, "찾을 수 없는 동네예요.")
    return MarginResponse(
        rec_id=rec.rec_id,
        dong_code=dong_code,
        district_code=dong.district_code,
        district_name=budget["district_name"],
        available_budget=budget["available_budget"],
        estimated_rent_cost=budget["estimated_rent_cost"],
        budget_margin=budget["budget_margin"],
        passed=budget["passed"],
        rent_confidence=budget["rent_confidence"],
    )
