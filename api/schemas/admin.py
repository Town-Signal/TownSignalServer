"""관리자 DTO (전체 명세 9장 api/schemas/admin)."""

from pydantic import BaseModel


class AccuracyItem(BaseModel):
    """⑮ v_extraction_accuracy 한 행 — 필드별 LLM 추출 정확도(목표 85% 이상, 4.8)."""

    field_name: str
    checked_n: int
    accuracy_pct: float
