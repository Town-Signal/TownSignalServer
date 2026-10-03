"""상권 지표 — sales_quarterly · store_quarterly · population_quarterly · business_license.

배치가 적재하고 API는 읽기만 한다. year_quarter는 "20251" 형식.
"""

from datetime import date

from sqlalchemy import CHAR, BigInteger, CheckConstraint, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base


class SalesQuarterly(Base):
    __tablename__ = "sales_quarterly"
    __table_args__ = (Index("idx_sales_dong_q", "dong_code", "year_quarter"),)

    dong_code: Mapped[str] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"), primary_key=True)
    industry_code: Mapped[str] = mapped_column(
        CHAR(8), ForeignKey("industry.industry_code"), primary_key=True
    )
    year_quarter: Mapped[str] = mapped_column(CHAR(5), primary_key=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    txn_count: Mapped[int] = mapped_column(Integer)
    weekday_amount: Mapped[int | None] = mapped_column(BigInteger)
    weekend_amount: Mapped[int | None] = mapped_column(BigInteger)
    hour_00_06: Mapped[int | None] = mapped_column(BigInteger)
    hour_06_11: Mapped[int | None] = mapped_column(BigInteger)
    hour_11_14: Mapped[int | None] = mapped_column(BigInteger)
    hour_14_17: Mapped[int | None] = mapped_column(BigInteger)
    hour_17_21: Mapped[int | None] = mapped_column(BigInteger)
    hour_21_24: Mapped[int | None] = mapped_column(BigInteger)
    male_count: Mapped[int | None] = mapped_column(Integer)
    female_count: Mapped[int | None] = mapped_column(Integer)
    age_10: Mapped[int | None] = mapped_column(Integer)
    age_20: Mapped[int | None] = mapped_column(Integer)
    age_30: Mapped[int | None] = mapped_column(Integer)
    age_40: Mapped[int | None] = mapped_column(Integer)
    age_50: Mapped[int | None] = mapped_column(Integer)
    age_60: Mapped[int | None] = mapped_column(Integer)


class StoreQuarterly(Base):
    __tablename__ = "store_quarterly"

    dong_code: Mapped[str] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"), primary_key=True)
    industry_code: Mapped[str] = mapped_column(
        CHAR(8), ForeignKey("industry.industry_code"), primary_key=True
    )
    year_quarter: Mapped[str] = mapped_column(CHAR(5), primary_key=True)
    store_count: Mapped[int] = mapped_column(Integer)
    franchise_count: Mapped[int | None] = mapped_column(Integer)
    open_count: Mapped[int | None] = mapped_column(Integer)
    close_count: Mapped[int | None] = mapped_column(Integer)


class PopulationQuarterly(Base):
    __tablename__ = "population_quarterly"

    dong_code: Mapped[str] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"), primary_key=True)
    year_quarter: Mapped[str] = mapped_column(CHAR(5), primary_key=True)
    total: Mapped[int] = mapped_column(BigInteger)
    male: Mapped[int | None] = mapped_column(BigInteger)
    female: Mapped[int | None] = mapped_column(BigInteger)
    age_10: Mapped[int | None] = mapped_column(BigInteger)
    age_20: Mapped[int | None] = mapped_column(BigInteger)
    age_30: Mapped[int | None] = mapped_column(BigInteger)
    age_40: Mapped[int | None] = mapped_column(BigInteger)
    age_50: Mapped[int | None] = mapped_column(BigInteger)
    age_60: Mapped[int | None] = mapped_column(BigInteger)
    time_00_06: Mapped[int | None] = mapped_column(BigInteger)  # 시간대별 인구(⑪ time_slots)
    time_06_11: Mapped[int | None] = mapped_column(BigInteger)
    time_11_14: Mapped[int | None] = mapped_column(BigInteger)
    time_14_17: Mapped[int | None] = mapped_column(BigInteger)
    time_17_21: Mapped[int | None] = mapped_column(BigInteger)
    time_21_24: Mapped[int | None] = mapped_column(BigInteger)


class BusinessLicense(Base):
    __tablename__ = "business_license"
    __table_args__ = (
        CheckConstraint("close_date IS NULL OR close_date >= open_date", name="chk_license_dates"),
        Index("idx_license_dong_ind", "dong_code", "industry_code"),
        Index("idx_license_open", "open_date"),
    )

    license_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dong_code: Mapped[str | None] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"))
    industry_code: Mapped[str | None] = mapped_column(CHAR(8), ForeignKey("industry.industry_code"))
    open_date: Mapped[date] = mapped_column(Date)
    close_date: Mapped[date | None] = mapped_column(Date)  # NULL = 아직 영업 중
    status: Mapped[str | None] = mapped_column(String(10))
