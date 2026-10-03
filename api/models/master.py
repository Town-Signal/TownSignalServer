"""마스터 — district · dong · industry."""

from sqlalchemy import CHAR, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base


class District(Base):
    __tablename__ = "district"

    district_code: Mapped[str] = mapped_column(CHAR(5), primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    geo_code: Mapped[str | None] = mapped_column(CHAR(5))  # 자치구 경계 GeoJSON 코드(통계청 2013)


class Dong(Base):
    __tablename__ = "dong"
    __table_args__ = (Index("idx_dong_district", "district_code"),)

    dong_code: Mapped[str] = mapped_column(CHAR(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(30))
    district_code: Mapped[str] = mapped_column(CHAR(5), ForeignKey("district.district_code"))
    geo_code: Mapped[str | None] = mapped_column(CHAR(7))  # 행정동 경계 GeoJSON 코드. 경계가 없으면 NULL


class Industry(Base):
    __tablename__ = "industry"
    __table_args__ = (Index("idx_industry_category", "category"),)

    industry_code: Mapped[str] = mapped_column(CHAR(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(20))  # 외식업 · 서비스업 · 소매업
