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
from api.routers import admin, certificates, health, programs, recommendations, regions


def create_app() -> FastAPI:
    # 라우터가 각자 /api prefix를 들고 있고 Vercel rewrites는 /api/*만 EC2로 넘기므로(명세 12.1)
    # 문서도 /api 아래에 둔다. 운영에서 https://town-signal.vercel.app/api/docs로 열린다 —
    # vercel.json · 프론트는 그대로고, EC2 :8000을 http로 직접 열 필요가 없어진다
    app = FastAPI(
        title="TownSignal API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        # 이 값은 docs_url을 따라오지 않아 따로 옮긴다. 안 옮기면 /docs/oauth2-redirect 한 줄이
        # /api 밖에 남아 운영에서 닿지 않는다(이 API는 OAuth2를 쓰지 않아 동작에는 영향 없음)
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
    )

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
    app.include_router(programs.router)
    app.include_router(recommendations.router)
    app.include_router(admin.router)
    return app


app = create_app()
