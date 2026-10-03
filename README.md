# TownSignalServer

타운시그널의 파이썬 서버. API와 배치가 한 저장소에 있고 `common/`을 공유한다.
프론트엔드는 [TownSignalFE](https://github.com/Town-Signal/TownSignalFE)에 따로 있다.

## 구조

```
TownSignalServer/
├── api/        FastAPI 동기식. 서빙 표 조회 + 사칙연산만 한다
├── batch/      수집 · 전처리 · 모델 학습 · 점수 선계산 · 표 생성 (API로 노출하지 않는다)
├── common/     evaluator · budget · scoring · constants
├── data/       raw · processed · seed
├── docs/       시스템 구조 문서 · 구조도
└── tests/
```

`common/`은 프레임워크와 DB에 의존하지 않는 순수 함수만 둔다. 배치와 API가 같은 코드를
불러 써야 배치가 만든 표와 요청 시 계산이 어긋나지 않는다. 요청 DTO는 `api/schemas/`에만 둔다.

## 왜 이렇게 나누나

배치가 서빙 표 네 개를 미리 채워두고, 요청 시에는 그 표를 조회해 더하고 빼기만 한다.
그래서 API 이미지에는 모델 라이브러리가 들어가지 않고 응답이 1초 안에 끝난다.

| 표 | 행 수 | 내용 | 주기 |
| --- | --- | --- | --- |
| `support_program` | 수백 | 지원사업 금액 · 자치구 · 자격조건 트리 · 중복수혜 여부 (검수분만 매칭) | 주 1회 |
| `district_rent` | 25 | 자치구별 ㎡당 월세 · 보증금 배수 · 신뢰도 | 기준 분기 변경 시 |
| `prediction` | 42,600 | 행정동 426 × 업종 100. 점포당 매출 · 생존기간 구간, 성장세, 백분위 · 종합점수 | 기준 분기 변경 시 |
| `summary_cache` | 조합 수 | 행정동 × 업종 요약문 (LLM 사전 생성) | 기준 분기 변경 시 |

## 문서

- [시스템 구조 v0.9](docs/타운시그널_시스템구조_v0.9.md) — 배치 · 요청 2단 구조, AI 모델, 데이터, 배포
- 「타운시그널 전체 명세」 v2.0 (Notion) — API · DB 스키마 · 화면 기능의 구현 기준. 문서끼리 다르면 전체 명세가 우선한다
- [db/schema.sql](db/schema.sql) — DB DDL 기준본 (전체 명세 5.3과 같다)

## 로컬 실행

```bash
cp .env.example .env
docker compose up --build        # db + api (http://localhost:8000/docs)
psql postgresql://townsignal:townsignal@localhost:5432/townsignal -f db/schema.sql   # 처음 한 번
```

### Docker가 없을 때 — Homebrew PostgreSQL 16

macOS에서 Docker 없이 DB만 로컬에 띄우는 대안이다. API · 테스트는 `.env`의 `DATABASE_URL`로만
DB에 붙으므로 어느 쪽을 써도 코드는 같다. compose의 db와 같은 5432 포트를 쓰므로 둘을 동시에 띄우지 않는다.

```bash
brew install postgresql@16
brew services start postgresql@16

psql -d postgres -c "CREATE ROLE townsignal LOGIN PASSWORD 'townsignal';"
psql -d postgres -c "CREATE DATABASE townsignal OWNER townsignal;"
psql postgresql://townsignal:townsignal@localhost:5432/townsignal -v ON_ERROR_STOP=1 -f db/schema.sql

cp .env.example .env             # DATABASE_URL 기본값이 위 DB(localhost:5432/townsignal)를 가리킨다
```

schema.sql의 `CREATE EXTENSION pgcrypto`는 PostgreSQL 13 이상에서 DB 소유자 권한으로 실행된다.
권한 오류가 나면 `psql -d townsignal -c "CREATE EXTENSION pgcrypto;"`를 설치 계정으로 먼저 실행한다.

배치 작업은 프로필로 분리돼 있다.

```bash
docker compose --profile batch run --rm batch batch.jobs.build_support_program
```

도커 없이 돌릴 때는 아래와 같다.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
uvicorn api.main:app --reload
```

## 테스트와 린트

```bash
pytest -q
ruff check .
```

## 의존성

| 파일 | 용도 |
| --- | --- |
| `requirements/api.txt` | FastAPI · SQLAlchemy 등 API 실행 |
| `requirements/batch.txt` | pandas · XGBoost · scikit-survival 등 학습 |
| `requirements/dev.txt` | 테스트 · 린트 (api 포함) |

## 배포

`Deploy API` 워크플로를 수동 실행하면 ECR에 이미지를 올리고 EC2에서 교체한다.
인프라와 시크릿 구성이 끝나면 main push 시 자동 배포로 바꾼다.
필요한 시크릿은 `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` · `EC2_HOST` ·
`EC2_USERNAME` · `EC2_KEY` · `DATABASE_URL` · `CORS_ORIGINS`.

## 커밋 규칙

- 커밋: `<Type>(<Scope>): <Subject>` / PR 제목: `<Type>: <Subject>`
- Type: feat · fix · docs · style · refactor · chore · todo
- 제목은 명령조로 쓰고 날짜는 적지 않는다.
