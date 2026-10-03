"""추천 스냅샷 — recommendation · rec_item. rec_id는 UUID이며 소유자 검증이 없다."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base


class Recommendation(Base):
    __tablename__ = "recommendation"
    __table_args__ = (Index("idx_rec_created", "created_at"),)

    rec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    input_condition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    calculated_budgets: Mapped[dict[str, Any]] = mapped_column(JSONB)  # Step 3은 재계산 없이 이것을 읽는다
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecItem(Base):
    __tablename__ = "rec_item"

    rec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recommendation.rec_id", ondelete="CASCADE"), primary_key=True
    )
    rank_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    dong_code: Mapped[str] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"))
    budget_margin: Mapped[int | None] = mapped_column(BigInteger)  # 임대료 결측 자치구는 NULL
    survival_p50: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    sales_p50: Mapped[int | None] = mapped_column(BigInteger)