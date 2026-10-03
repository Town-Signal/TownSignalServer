"""지원사업 — support_program · program_verification, 뷰 v_extraction_accuracy(읽기 전용)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import VIEW_METADATA, Base


class SupportProgram(Base):
    __tablename__ = "support_program"
    __table_args__ = (
        CheckConstraint("amount_max >= 0", name="support_program_amount_max_check"),
        Index("idx_program_district", "district_code"),
        Index("idx_program_deadline", "apply_end"),
        Index("idx_program_elig", "eligibility", postgresql_using="gin"),
    )

    program_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    agency: Mapped[str | None] = mapped_column(String(100))
    amount_max: Mapped[int] = mapped_column(BigInteger)
    district_code: Mapped[str | None] = mapped_column(
        CHAR(5), ForeignKey("district.district_code")
    )  # NULL = 전국 사업
    is_exclusive: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # 중복 수혜 불가
    apply_start: Mapped[date | None] = mapped_column(Date)
    apply_end: Mapped[date | None] = mapped_column(Date)
    eligibility: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 조건 트리(7.3)
    source_url: Mapped[str | None] = mapped_column(String(500))
    raw_text: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(40))  # NULL이면 매칭에 쓰지 않는다


class ProgramVerification(Base):
    __tablename__ = "program_verification"
    __table_args__ = (Index("idx_verif_program", "program_id"),)

    verification_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    program_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("support_program.program_id", ondelete="CASCADE")
    )
    field_name: Mapped[str] = mapped_column(String(40))
    extracted_value: Mapped[str | None] = mapped_column(String(200))
    actual_value: Mapped[str | None] = mapped_column(String(200))
    is_match: Mapped[bool] = mapped_column(Boolean)
    checked_by: Mapped[str | None] = mapped_column(String(40))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# 뷰는 읽기 전용 Table로만 매핑한다. Base.metadata에 넣지 않는다.
v_extraction_accuracy = Table(
    "v_extraction_accuracy",
    VIEW_METADATA,
    Column("field_name", String(40)),
    Column("checked_n", BigInteger),
    Column("accuracy_pct", Numeric),
)
