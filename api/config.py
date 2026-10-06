"""환경변수 설정. 값은 컨테이너 환경변수로 주입한다 (저장소에 .env를 두지 않는다)."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal")
APP_ENV = os.getenv("APP_ENV", "local")
CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o]

# 관리자 API(X-Admin-Token) 검증값. 비어 있으면 관리자 API는 모두 401이다
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# 자격증 정규화 LLM 폴백(7.2). 요청 경로에서 LLM을 부르는 유일한 예외라 플래그로 끌 수 있다.
# TODO(가정): 기본은 꺼 둔다. 켜도 LLM_API_KEY가 없으면 쓰지 않는다
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
CERT_LLM_FALLBACK_ENABLED = os.getenv("CERT_LLM_FALLBACK_ENABLED", "false").lower() in ("1", "true", "yes")
CERT_LLM_TIMEOUT_SECONDS = float(os.getenv("CERT_LLM_TIMEOUT_SECONDS", "2"))
