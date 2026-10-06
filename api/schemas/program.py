"""지원사업 · 가용예산 DTO (전체 명세 9장 api/schemas/program, 8.6 필드 추가 반영)."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from api.schemas.region import BudgetSummary
from api.schemas.types import Certificate, IndustryCode, Passed, RentConfidence
from common.constants import (
    AGE_MAX,
    AGE_MIN,
    AREA_MAX_SQM,
    CERTIFICATES_MAX_COUNT,
    DEFAULT_AREA_SQM,
)

UnavailableReason = Literal["RENT_DATA_MISSING", "SUPPORT_DATA_ERROR"]


class BudgetCalculationRequest(BaseModel):
    """Step 1 → Step 2 (rec_id 발급)"""

    age: int = Field(..., ge=AGE_MIN, le=AGE_MAX, description="만 나이")
    capital: int = Field(..., ge=0, description="자기자본금(원)")
    industry_code: IndustryCode = Field(..., description="소분류 업종코드 CS######")
    career_years: int = Field(0, ge=0, description="동종업계 경력(년)")
    certificates: list[Certificate] = Field(default_factory=list, max_length=CERTIFICATES_MAX_COUNT)
    target_area_sqm: float = Field(DEFAULT_AREA_SQM, gt=0, le=AREA_MAX_SQM, description="희망 면적(㎡)")


class MatchedProgram(BaseModel):
    program_id: int
    name: str
    amount: int
    is_exclusive: bool
    district_code: str | None  # None = 전국
    apply_end: date | None  # None = 상시모집


class ExcludedProgram(BaseModel):
    program_id: int
    name: str
    amount: int
    exclude_reason: str


class DistrictBudgetResult(BaseModel):
    """기능명세서 2.3.1 자치구별 가용예산"""

    district_code: str
    district_name: str
    support_fund_max: int
    available_budget: int
    estimated_rent_cost: int | None  # 도봉구 등 결측 시 None
    budget_margin: int | None
    passed: Passed
    rent_confidence: RentConfidence
    matched_programs: list[MatchedProgram]
    excluded_programs: list[ExcludedProgram]
    # 8.6 추가
    monthly_rent: int | None  # 원/월, 확인불가면 None
    rent_per_sqm: float | None  # 천원/㎡ · 월
    geo_code: str | None  # 자치구 GeoJSON 코드
    unavailable_reason: UnavailableReason | None


class BudgetCalculationResponse(BaseModel):
    rec_id: UUID
    # TODO(가정): 9장은 str이지만 district_rent가 비어 있으면 기준 분기가 없어 null을 허용한다
    base_quarter: str | None  # 2026Q2
    own_capital: int
    normalized_certificates: list[str]
    degraded: bool
    summary: BudgetSummary  # 8.6
    district_budgets: list[DistrictBudgetResult]  # 25개, available_budget 내림차순


class UpcomingProgram(BaseModel):
    """기능명세서 1.1.3"""

    program_id: int
    name: str
    agency: str | None
    amount_max: int
    district_code: str | None
    district_name: str  # 전국이면 "전국"
    apply_start: date | None
    apply_end: date | None
    always_open: bool  # apply_end가 None이면 True
    source_url: str | None


class ProgramDetail(UpcomingProgram):
    is_exclusive: bool
    eligibility: dict[str, Any]
    raw_text: str | None
