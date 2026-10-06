"""지역 · 업종 DTO (전체 명세 9장 api/schemas/region, 8.6 필드 추가 반영)."""

from typing import Literal

from pydantic import BaseModel

from api.schemas.types import RentConfidence


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
