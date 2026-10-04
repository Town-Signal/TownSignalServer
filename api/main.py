"""TownSignal API.

요청 경로에서는 배치가 만들어둔 표를 조회하고 사칙연산만 한다.
모델 파일을 로드하지 않으며, 예측값은 prediction 표에서 읽는다.
모든 응답은 8.1.1 공통 봉투로 나간다(api/schemas/common · api/errors).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import CORS_ORIGINS
from api.errors import register_exception_handlers
from api.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from api.routers import certificates, health, regions


def create_app() -> FastAPI:
    app = FastAPI(title="TownSignal API", version="0.1.0")

    # 나중에 추가한 미들웨어가 바깥쪽이다. request_id는 CORS 처리 응답에도 붙도록 가장 바깥에 둔다
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(regions.router)
    app.include_router(certificates.router)
    return app


app = create_app()
