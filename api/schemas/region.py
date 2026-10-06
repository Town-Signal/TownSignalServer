"""지역 · 업종 DTO (전체 명세 9장 api/schemas/region, 8.6 필드 추가 반영)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from api.schemas.types import (
    DataStatus,
    DongCode,
    GrowthConfidence,
    IndustryCode,
    Passed,
    RentConfidence,
)

StoreLevel = Literal["적음", "보통", "많음"]


class RegionSearchItem(BaseModel):
    """⑦ 행정동 자동완성 (기능명세서 3.1.1)"""

    dong_code: str
    dong_name: str
    district_code: str
    district_name: str
    type: Literal["dong"] = "dong"
    geo_code: str | None  # 8.6 — 행정동 경계 GeoJSON 코드(2013, 7자리). 경계가 없으면 null


class DistrictItem(BaseModel):
    """⑧ 자치구 목록"""

    district_code: str
    name: str
    rent_confidence: RentConfidence
    geo_code: str | None  # 8.6 — 자치구 경계 GeoJSON 코드(2013, 5자리)


class DongItem(BaseModel):
    """⑨ 자치구 산하 행정동"""

    dong_code: str
    name: str
    district_code: str
    # TODO(가정): 8.6은 ⑦ · ⑧에만 geo_code를 적었지만 대안 경로 화면도 지도 핀이 필요해 더함
    geo_code: str | None


class IndustryItem(BaseModel):
    """⑩ 업종 마스터"""

    industry_code: str
    name: str
    category: str


class BudgetSummary(BaseModel):
    """8.6 ② · ⑤"""

    passed_district_count: int
    eligible_district_count: int  # 통과 + 확인불가 (화면 '감당 가능한 구')
    excluded_district_count: int


class ScoreBreakdown(BaseModel):
    """기능명세서 2.6.3 항목별 점수 분해(④ · ⑫). 값은 prediction에 배치가 채운 것 그대로다."""

    sales_percentile: float | None
    survival_percentile: float | None
    growth_percentile: float | None = None
    applied_weights: dict[str, float]  # {"sales": 0.4, "survival": 0.4, "growth": 0.2}
    # TODO(가정): 9장은 float 필수지만 prediction.total_score는 스키마상 NULL일 수 있어 null 허용
    total_score: float | None  # 0.0 ~ 100.0, 소수 1자리


# ── ⑪ 상권 현황 ─────────────────────────────────────────────


class IndustryShare(BaseModel):
    industry_code: str | None  # None = 기타
    name: str
    store_count: int
    share: float
    is_selected: bool = False  # 8.6 — 쿼리 industry_code의 업종


class SalesPoint(BaseModel):
    year_quarter: str  # 20251
    # TODO(가정): 9장은 int지만 최근 8분기 중 그 동에 기록이 없는 분기는 null(화면 '빈 막대')
    amount: int | None
    store_count: int | None
    per_store_amount: int | None  # amount ÷ store_count, 사사오입
    per_store_monthly_amount: int | None  # 8.6 — per_store_amount ÷ 3, 버림


class RentInfo(BaseModel):
    basis: str  # "관악구 평균"
    rent_per_sqm: float | None
    area_sqm: float
    monthly_rent: int | None
    estimated_rent_cost: int | None
    confidence: RentConfidence
    base_quarter: str | None  # TODO(가정): district_rent 행이 없으면 null


class PopulationPoint(BaseModel):
    year_quarter: str
    total: int


class PopulationLatest(BaseModel):
    male: int | None
    female: int | None
    age_10: int | None
    age_20: int | None
    age_30: int | None
    age_40: int | None
    age_50: int | None
    age_60: int | None
    time_slots: dict[str, int | None]  # 8.6 — {"00-06": …, "21-24": …}
    peak_time_slot: str | None  # 8.6 — 가장 큰 구간


class GrowthInfo(BaseModel):
    growth_rate: float | None
    growth_confidence: GrowthConfidence | None


class AnalyticsResponse(BaseModel):
    """기능명세서 4.1.1 · 4.1.3 · 4.1.4"""

    dong_code: str
    dong_name: str
    district_code: str
    district_name: str
    latest_quarter: str | None
    data_status: Literal["정상", "조회 불가"]
    total_store_count: int | None  # 8.6 — 최근 분기 전체 점포 수
    industry_distribution: list[IndustryShare] | None
    sales_trend: list[SalesPoint] | None
    space_standard_break: str = "20241"
    rent: RentInfo
    population_trend: list[PopulationPoint] | None
    population_latest: PopulationLatest | None
    growth: GrowthInfo | None  # industry_code 지정 시에만


# ── ⑫ 예측 · ⑬ 요약 ─────────────────────────────────────────


class PredictionResponse(BaseModel):
    """기능명세서 4.1.2"""

    dong_code: str
    industry_code: str
    model_version: str
    sales_quarterly_p10: int | None
    sales_quarterly_p50: int | None
    sales_quarterly_p90: int | None
    sales_monthly_p50: int | None
    survival_p10: float | None
    survival_p50: float | None
    survival_p90: float | None
    growth_rate: float | None
    growth_confidence: GrowthConfidence | None
    store_count_avg: float | None  # 최근 4개 분기 평균 점포 수
    data_status: DataStatus
    computed_at: datetime | None
    # 8.6 추가
    total_score: float | None
    score_breakdown: ScoreBreakdown | None  # 예측 행이 없으면 null
    sales_monthly_p10: int | None
    sales_monthly_p90: int | None
    store_count_latest: int | None
    store_level: StoreLevel | None
    geo_code: str | None


class SummaryResponse(BaseModel):
    """기능명세서 4.1.5"""

    dong_code: str
    industry_code: str
    summary_text: str | None
    model_name: str | None
    generated_at: datetime | None


# ── ⑭ 비교 ──────────────────────────────────────────────────


class RegionCompareRequest(BaseModel):
    """기능명세서 5.1.1 (1~4개)"""

    dong_codes: list[DongCode] = Field(..., min_length=1, max_length=4)
    industry_code: IndustryCode
    rec_id: UUID | None = None

    @field_validator("dong_codes")
    @classmethod
    def no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            # type 'duplicate' → errors.reason DUPLICATE (api/errors 매핑)
            raise PydanticCustomError("duplicate", "dong_codes에 중복이 있음")
        return v


class CompareItem(BaseModel):
    dong_code: str
    dong_name: str
    district_code: str
    district_name: str
    sales_quarterly_p50: int | None
    sales_quarterly_range: tuple[int | None, int | None] | None
    survival_p50: float | None
    survival_range: tuple[float | None, float | None] | None
    growth_rate: float | None
    rent_per_sqm: float | None
    estimated_rent_cost: int | None
    rent_confidence: RentConfidence
    budget_margin: int | None  # rec_id가 있을 때만
    passed: Passed | None  # rec_id가 있을 때만
    store_count: int | None
    total_population: int | None
    data_status: DataStatus
    # 8.6 추가
    total_score: float | None
    sales_monthly_p50: int | None
    sales_monthly_range: tuple[int | None, int | None] | None
    geo_code: str | None


class RegionCompareResponse(BaseModel):
    industry_code: str
    industry_name: str
    comparison_items: list[CompareItem]  # 요청 순서


# ── ⑱ 랭킹 ──────────────────────────────────────────────────


class RankingItem(BaseModel):
    """8.6 ⑱"""

    rank: int | None
    dong_code: str
    dong_name: str
    district_code: str
    district_name: str
    geo_code: str | None
    total_score: float | None
    survival_p50: float | None
    sales_monthly_p50: int | None
    growth_rate: float | None
    store_count_latest: int | None
    store_level: StoreLevel | None
    data_status: DataStatus


class RankingResponse(BaseModel):
    industry_code: str
    industry_name: str
    items: list[RankingItem]
    total: int
