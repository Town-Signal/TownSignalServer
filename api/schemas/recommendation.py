"""추천 DTO (전체 명세 9장 api/schemas/recommendation, 8.6 필드 추가 반영)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from api.schemas.program import DistrictBudgetResult
from api.schemas.region import BudgetSummary, ScoreBreakdown
from api.schemas.types import DataStatus, GrowthConfidence, Passed, RentConfidence


class RecommendationExecuteRequest(BaseModel):
    """Step 2 → Step 3 (rec_id만 받아 건너뛰기 차단)"""

    rec_id: UUID
    top_k: int = Field(20, ge=1, le=50)


class DongRankResult(BaseModel):
    rank_no: int
    dong_code: str
    dong_name: str
    district_code: str
    district_name: str
    passed: Passed
    estimated_rent_cost: int | None = None
    budget_margin: int | None = None  # 값만 표시. 점수 · 정렬에 쓰지 않는다(판정 14)
    rent_confidence: RentConfidence
    # TODO(가정): prediction 칼럼이 NULL일 수 있어 매출 · 생존 값은 null 허용(9장은 필수)
    sales_quarterly_p50: int | None
    sales_quarterly_range: tuple[int | None, int | None]  # (p10, p90) — 80% 범위
    sales_monthly_p50: int | None  # sales_quarterly_p50 // 3
    survival_p50: float | None  # 개월
    survival_range: tuple[float | None, float | None]
    growth_rate: float | None = None
    growth_confidence: GrowthConfidence | None = None
    data_status: DataStatus
    score_breakdown: ScoreBreakdown
    summary_text: str | None = None  # summary_cache 미스 시 None
    # 8.6 추가
    sales_monthly_range: tuple[int | None, int | None]  # (p10 // 3, p90 // 3)
    geo_code: str | None
    is_residential: bool  # data_status가 "표본 부족"이면 true


class RecommendationSummary(BaseModel):
    """8.6 ④ data.summary"""

    recommended_count: int
    passed_district_count: int
    eligible_district_count: int
    industry_code: str
    industry_name: str
    category: str


class RecommendationResponse(BaseModel):
    rec_id: UUID
    base_quarter: str | None  # TODO(가정): ② 스냅샷에 기준 분기가 없으면 null
    model_version: str
    industry_code: str
    industry_name: str
    degraded: bool
    disclaimer: str
    summary: RecommendationSummary  # 8.6
    recommendations: list[DongRankResult]  # 0개면 빈 목록


class RecItemResult(BaseModel):
    """⑤ items — rank_no · budget_margin은 rec_item, 나머지는 응답 시점 최신 prediction(판정 41)"""

    rank_no: int
    dong_code: str
    dong_name: str
    district_code: str
    district_name: str
    budget_margin: int | None
    total_score: float | None
    survival_p50: float | None
    survival_range: tuple[float | None, float | None]
    sales_p50: int | None  # 9장 필드(분기)
    sales_monthly_p50: int | None
    sales_monthly_range: tuple[int | None, int | None]
    growth_rate: float | None
    growth_confidence: GrowthConfidence | None
    data_status: DataStatus


class RecommendationSnapshot(BaseModel):
    """GET /api/recommendations/{rec_id}"""

    rec_id: UUID
    created_at: datetime
    base_quarter: str | None
    input_condition: dict[str, Any]
    industry_name: str
    degraded: bool
    summary: BudgetSummary  # 8.6 — ②와 같은 3개 수
    district_budgets: list[DistrictBudgetResult]
    items: list[RecItemResult]  # Step 3 전이면 빈 목록


class MarginResponse(BaseModel):
    """기능명세서 4.2.1"""

    rec_id: UUID
    dong_code: str
    district_code: str
    district_name: str
    available_budget: int
    estimated_rent_cost: int | None
    budget_margin: int | None
    passed: Passed
    rent_confidence: RentConfidence
