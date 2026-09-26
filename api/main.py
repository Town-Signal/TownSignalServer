"""TownSignal API.

요청 경로에서는 배치가 만들어둔 표를 조회하고 사칙연산만 한다.
모델 파일을 로드하지 않으며, 예측값은 prediction 표에서 읽는다.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import CORS_ORIGINS
from api.routers import health

app = FastAPI(title="TownSignal API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
