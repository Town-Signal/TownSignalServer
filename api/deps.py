"""공통 의존성 — DB 세션 주입, 관리자 토큰 검증, 오늘 날짜."""

import secrets
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Header

from api import config
from api.db import get_session
from api.errors import AppError

__all__ = ["get_session", "get_today", "require_admin"]

KST = ZoneInfo("Asia/Seoul")


def get_today() -> date:
    """오늘(KST). 마감 판정에 쓴다. 테스트에서는 dependency_overrides로 고정한다."""
    return datetime.now(KST).date()


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """관리자 API 전용. X-Admin-Token이 ADMIN_TOKEN과 같아야 한다(8.1).

    TODO(가정): 서버에 ADMIN_TOKEN이 설정돼 있지 않으면 어떤 값이 와도 401로 막는다.
    """
    expected = config.ADMIN_TOKEN
    if not expected or not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise AppError("UNAUTHORIZED", 401, "관리자 인증이 필요해요.")
