"""공통 의존성 — DB 세션 주입, 관리자 토큰 검증."""

import secrets
from typing import Annotated

from fastapi import Header

from api import config
from api.db import get_session
from api.errors import AppError

__all__ = ["get_session", "require_admin"]


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """관리자 API 전용. X-Admin-Token이 ADMIN_TOKEN과 같아야 한다(8.1).

    TODO(가정): 서버에 ADMIN_TOKEN이 설정돼 있지 않으면 어떤 값이 와도 401로 막는다.
    """
    expected = config.ADMIN_TOKEN
    if not expected or not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise AppError("UNAUTHORIZED", 401, "관리자 인증이 필요해요.")