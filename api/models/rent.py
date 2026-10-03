"""임대료 — commercial_market · district_rent. base_quarter는 "2026Q2" 형식."""

from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base


class CommercialMarket(Base):
    __tablename__ = "commercial_market"

    market_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    base_quarter: Mapped[str] = mapped_column(CHAR(6), primary_key=True)
    district_code: Mapped[str] = mapped_column(CHAR(5), ForeignKey("district.district_code"))
    rent_per_sqm: Mapped[Decimal] = mapped_column(Numeric(8, 2))  # 천원/㎡ · 월


class DistrictRent(Base):
    __tablename__ = "district_rent"
    __table_args__ = (
        CheckConstraint("confidence IN ('높음','낮음','없음')", name="district_rent_confidence_check"),
    )

    district_code: Mapped[str] = mapped_column(
        CHAR(5), ForeignKey("district.district_code"), primary_key=True
    )
    base_quarter: Mapped[str] = mapped_column(CHAR(6))
    rent_per_sqm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))  # 도봉구는 NULL. 0으로 채우지 않는다
    deposit_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default=text("15"))
    market_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    confidence: Mapped[str] = mapped_column(String(10))
