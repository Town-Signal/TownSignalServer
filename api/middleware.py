"""요청마다 request_id를 정해 응답 헤더 X-Request-ID와 meta.request_id에 넣는다 (8.1.1)."""

import re
import secrets
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

# TODO(가정): 클라이언트가 보낸 값은 영문 · 숫자 · . _ - 1~64자일 때만 쓴다. 그 밖이면 새로 발급한다
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return "req_" + secrets.token_hex(5)  # 예: req_7f3a9c1e2b


def current_request_id() -> str:
    """지금 처리 중인 요청의 id. 요청 밖(테스트 등)에서 부르면 새로 만든다."""
    return _request_id.get() or new_request_id()


def request_id_of(scope: Scope) -> str | None:
    return scope.get("state", {}).get("request_id")


class RequestIdMiddleware:
    """순수 ASGI 미들웨어. scope.state와 contextvar에 request_id를 넣고 응답 헤더에 붙인다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        sent = dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1")
        request_id = sent if _CLIENT_ID.match(sent) else new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)
