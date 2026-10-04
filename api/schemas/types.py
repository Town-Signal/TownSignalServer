"""DTO 공용 타입 (전체 명세 9장 api/schemas/types)."""

from typing import Annotated, Literal

from pydantic import StringConstraints

from common.constants import INDUSTRY_CATEGORIES

Passed = Literal["통과", "제외", "확인불가"]
RentConfidence = Literal["높음", "낮음", "없음"]
GrowthConfidence = Literal["높음", "낮음"]
DataStatus = Literal["정상", "표본 부족", "예측 불가"]
IndustryCode = Annotated[str, StringConstraints(pattern=r"^CS\d{6}$")]
DongCode = Annotated[str, StringConstraints(pattern=r"^\d{8}$")]
Certificate = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]

# TODO(가정): 9장에 없어 더함 — 자치구 코드는 숫자 5자리(5.3 district.district_code CHAR(5))
DistrictCode = Annotated[str, StringConstraints(pattern=r"^\d{5}$")]
# 업종 대분류 3종. 값은 common.constants.INDUSTRY_CATEGORIES와 같다
IndustryCategory = Literal[INDUSTRY_CATEGORIES]
