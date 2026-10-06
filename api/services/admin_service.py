"""⑮ 공고 추출 정확도 (전체 명세 8.5 · 4.8)."""

from sqlalchemy.orm import Session

from api.repositories import admin_repository as repo
from api.schemas.admin import AccuracyItem
from api.schemas.common import ListData


def extraction_accuracy(session: Session) -> ListData[AccuracyItem]:
    items = [
        AccuracyItem(field_name=row.field_name, checked_n=row.checked_n, accuracy_pct=float(row.accuracy_pct))
        for row in repo.list_extraction_accuracy(session)
    ]
    return ListData(items=items, total=len(items))
