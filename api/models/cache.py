"""요약 캐시 — summary_cache. 요청 경로에서 LLM을 부르지 않고 이 표만 읽는다."""

from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base


class SummaryCache(Base):
    __tablename__ = "summary_cache"

    dong_code: Mapped[str] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"), primary_key=True)
    industry_code: Mapped[str] = mapped_column(
        CHAR(8), ForeignKey("industry.industry_code"), primary_key=True
    )
    summary_text: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(40))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())