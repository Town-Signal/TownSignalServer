"""배치 환경변수. 배치 이미지에는 api/가 없으므로 api.config를 import하지 않고 여기서 읽는다."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal")
APP_ENV = os.getenv("APP_ENV", "local")