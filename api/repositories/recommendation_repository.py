"""추천 스냅샷 저장 · 조회 (② · ④ · ⑤ · ⑥)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from api.models import Recommendation


def create_recommendation(
    session: Session, input_condition: dict[str, Any], calculated_budgets: dict[str, Any]
) -> uuid.UUID:
    """recommendation 행을 만들고 DB가 만든 rec_id(UUIDv4, gen_random_uuid())를 돌려준다.

    commit은 하지 않는다(서비스가 한다).
    """
    rec = Recommendation(input_condition=input_condition, calculated_budgets=calculated_budgets)
    session.add(rec)
    session.flush()
    return rec.rec_id
