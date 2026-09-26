"""환경변수 설정. 값은 컨테이너 환경변수로 주입한다 (저장소에 .env를 두지 않는다)."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal")
APP_ENV = os.getenv("APP_ENV", "local")
CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o]
