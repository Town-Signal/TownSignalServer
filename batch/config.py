"""배치 환경변수. 배치 이미지에는 api/가 없으므로 api.config를 import하지 않고 여기서 읽는다."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal")
APP_ENV = os.getenv("APP_ENV", "local")

# TODO(가정): 지원사업표 백업(pg_dump) 폴더. 명세 12.3 환경변수 6종에 없어 기본값 'backups'
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
