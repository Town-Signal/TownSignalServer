"""관리자 조회 (⑮)."""

from collections.abc import Sequence

from sqlalchemy import Row, select
from sqlalchemy.orm import Session

from api.models import v_extraction_accuracy


def list_extraction_accuracy(session: Session) -> Sequence[Row]:
    """v_extraction_accuracy(읽기 전용 뷰). accuracy_pct 오름차순 → field_name."""
    view = v_extraction_accuracy
    stmt = select(view.c.field_name, view.c.checked_n, view.c.accuracy_pct).order_by(
        view.c.accuracy_pct, view.c.field_name
    )
    return session.execute(stmt).all()
