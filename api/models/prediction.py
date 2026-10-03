"""예측 — prediction. 배치가 백분위 · total_score · score_rank까지 채우고 API는 읽기만 한다(6.5)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base


class Prediction(Base):
    __tablename__ = "prediction"
    __table_args__ = (
        CheckConstraint(
            "survival_p10 <= survival_p50 AND survival_p50 <= survival_p90", name="chk_pred_survival"
        ),
        CheckConstraint("sales_p10 <= sales_p50 AND sales_p50 <= sales_p90", name="chk_pred_sales"),
        CheckConstraint(
            "growth_confidence IN ('높음','낮음')", name="prediction_growth_confidence_check"
        ),
    )

    dong_code: Mapped[str] = mapped_column(CHAR(8), ForeignKey("dong.dong_code"), primary_key=True)
    industry_code: Mapped[str] = mapped_column(
        CHAR(8), ForeignKey("industry.industry_code"), primary_key=True
    )
    model_version: Mapped[str] = mapped_column(String(30), primary_key=True)  # xgb_v1 / mlp_v1
    # p10 · p50 · p90 = 80% 예측구간. "최소 · 최대"가 아니다
    survival_p10: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    survival_p50: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    survival_p90: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    sales_p10: Mapped[int | None] = mapped_column(BigInteger)  # 원, 분기
    sales_p50: Mapped[int | None] = mapped_column(BigInteger)
    sales_p90: Mapped[int | None] = mapped_column(BigInteger)
    growth_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    growth_confidence: Mapped[str | None] = mapped_column(String(10))
    sales_percentile: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    survival_percentile: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    growth_percentile: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    score_rank: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# schema.sql과 같이 sales_p50 DESC. 문자열 칼럼 이름으로는 DESC를 줄 수 없어 클래스 밖에서 만든다
Index("idx_pred_rank", Prediction.industry_code, Prediction.model_version, Prediction.sales_p50.desc())
