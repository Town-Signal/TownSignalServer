"""recommendation 보관 기간 정리 (전체 명세 4.6 run_weekly ③).

로그인이 없어 소유자 없는 추천 스냅샷이 계속 쌓이므로 생성 후 30일이 지난 행을 주 1회 지운다.
rec_item은 ON DELETE CASCADE로 함께 지워진다. 지워진 rec_id로 ⑤ · ⑥을 부르면 REC_NOT_FOUND다.
"""

from datetime import datetime, timedelta

from sqlalchemy import Connection, text

from batch.pipeline.db import transaction
from common.constants import RECOMMENDATION_RETENTION_DAYS


def delete_expired_recommendations(
    conn: Connection, now: datetime | None = None, days: int = RECOMMENDATION_RETENTION_DAYS
) -> int:
    """created_at이 now − days보다 이전인 recommendation을 지우고 지운 행 수를 돌려준다.

    TODO(가정): '30일이 지난'은 30일 초과다. 정확히 30일 된 행은 다음 주에 지운다.
    now를 주지 않으면 DB 시각(now())을 쓴다.
    """
    with transaction(conn):
        if now is None:
            result = conn.execute(
                text("DELETE FROM recommendation WHERE created_at < now() - make_interval(days => :days)"),
                {"days": days},
            )
        else:
            result = conn.execute(
                text("DELETE FROM recommendation WHERE created_at < :cutoff"),
                {"cutoff": now - timedelta(days=days)},
            )
    return result.rowcount
