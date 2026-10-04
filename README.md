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

로컬 DB는 **팀원마다 각자 PC에 따로** 생긴다. 서로 공유되지 않으므로 데이터가 필요하면 각자 적재한다.
API와 테스트는 `.env`의 `DATABASE_URL` 하나로만 DB에 붙으므로, DB를 어떤 방법으로 띄우든 코드는 같다.

| 방법 | macOS | Windows |
| --- | --- | --- |
| 기본 — docker compose | Docker Desktop | Docker Desktop (WSL2 백엔드) · **검증 전** |
| 대안 — Docker 없이 | Homebrew `postgresql@16` · 검증됨 | PostgreSQL 16 공식 설치 프로그램 · **검증 전** |

> docker compose 경로는 작성자 PC에 Docker가 없어 아직 직접 돌려 보지 못했다. 윈도우 절차는 모두 검증 전이다.
> 따라 하다 막히면 고친 내용을 이 문서에 반영해 주세요.

### 1. 준비

- Python 3.11 (CI · Dockerfile과 같은 버전)
- 5432 포트가 비어 있는지 확인한다. 다른 PostgreSQL이 이미 쓰고 있으면 compose의 db가 뜨지 않는다.

| | macOS (zsh) | Windows (PowerShell) |
| --- | --- | --- |
| 5432 사용 확인 | `lsof -nP -iTCP:5432 -sTCP:LISTEN` | `Get-NetTCPConnection -LocalPort 5432 -State Listen` |
| 가상환경 만들기 | `python3.11 -m venv .venv` | `py -3.11 -m venv .venv` |
| 가상환경 켜기 | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| 의존성 설치 | `pip install -r requirements/dev.txt` | `pip install -r requirements/dev.txt` |
| .env 만들기 | `cp .env.example .env` | `Copy-Item .env.example .env` |

- Windows에서 `Activate.ps1`이 실행 정책 때문에 막히면 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 한 번 실행한다.
- `.env`의 `DATABASE_URL` 기본값(`postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal`)은 아래 두 방법 모두에 그대로 맞는다.

### 2-A. 기본 — docker compose로 DB 띄우기

```bash
docker compose up -d db          # PostgreSQL 16 (db 서비스)만 띄운다
```

schema.sql은 처음 한 번 적용한다. PowerShell은 `<` 리다이렉션을 쓸 수 없어서 파일을 컨테이너에 복사한 뒤 `-f`로 실행한다.

```bash
# macOS (zsh)
docker compose exec -T db psql -U townsignal -d townsignal -v ON_ERROR_STOP=1 < db/schema.sql
```

```powershell
# Windows (PowerShell) — 검증 전
docker compose cp db/schema.sql db:/tmp/schema.sql
docker compose exec db psql -U townsignal -d townsignal -v ON_ERROR_STOP=1 -f /tmp/schema.sql
```

테이블 확인 (두 OS 같음) — 테이블 15개, 뷰 1개(`v_extraction_accuracy`)가 나오면 된다.

```bash
docker compose exec db psql -U townsignal -d townsignal -c "\dt" -c "\dv"
```

초기화

```bash
docker compose down              # 컨테이너만 내린다. DB 데이터(db-data 볼륨)는 남는다
docker compose down -v           # 볼륨까지 지운다. 다시 띄우면 schema.sql부터 다시 적용
```

### 2-B. 대안 — Docker 없이 PostgreSQL 16 직접 설치

**macOS — Homebrew**

```bash
brew install postgresql@16
brew services start postgresql@16

psql -d postgres -c "CREATE ROLE townsignal LOGIN PASSWORD 'townsignal';"
psql -d postgres -c "CREATE DATABASE townsignal OWNER townsignal;"
psql postgresql://townsignal:townsignal@localhost:5432/townsignal -v ON_ERROR_STOP=1 -f db/schema.sql
psql postgresql://townsignal:townsignal@localhost:5432/townsignal -c "\dt" -c "\dv"
```

**Windows — PostgreSQL 16 공식 설치 프로그램 (검증 전)**

1. postgresql.org의 Windows 설치 프로그램(EDB)으로 PostgreSQL 16을 설치한다. 포트는 5432, 설치 중 정한 `postgres` 계정 비밀번호를 기억해 둔다.
2. `psql`이 PATH에 없으면 `C:\Program Files\PostgreSQL\16\bin`을 PATH에 추가하거나 전체 경로로 실행한다.
3. PowerShell에서 아래를 실행한다. schema.sql에 한글(CHECK 값 · 주석)이 있어 클라이언트 인코딩을 UTF8로 먼저 맞춘다.

```powershell
$env:PGCLIENTENCODING = "UTF8"
psql -U postgres -h localhost -c "CREATE ROLE townsignal LOGIN PASSWORD 'townsignal';"
psql -U postgres -h localhost -c "CREATE DATABASE townsignal OWNER townsignal;"
psql "postgresql://townsignal:townsignal@localhost:5432/townsignal" -v ON_ERROR_STOP=1 -f db/schema.sql
psql "postgresql://townsignal:townsignal@localhost:5432/townsignal" -c "\dt" -c "\dv"
```

schema.sql의 `CREATE EXTENSION pgcrypto`는 PostgreSQL 13 이상에서 DB 소유자 권한으로 실행된다.
권한 오류가 나면 관리자 계정으로 `psql -d townsignal -c "CREATE EXTENSION pgcrypto;"`를 먼저 실행한다.

초기화는 `DROP DATABASE townsignal;` 후 `CREATE DATABASE`부터 다시 한다.

### 3. DB 접속 정보

| 항목 | 값 |
| --- | --- |
| 호스트 · 포트 | `localhost:5432` (compose 안에서는 `db:5432`) |
| DB · 사용자 · 비밀번호 | `townsignal` · `townsignal` · `townsignal` |
| DATABASE_URL | `postgresql+psycopg://townsignal:townsignal@localhost:5432/townsignal` |

### 4. API 실행

가상환경을 켠 상태에서 (두 OS 같음)

```bash
uvicorn api.main:app --reload    # http://localhost:8000/docs, http://localhost:8000/health
```

docker compose로 API까지 띄우려면 `docker compose up --build`(db + api, 8000 포트)를 쓴다.

배치 작업은 프로필로 분리돼 있다.

```bash
docker compose --profile batch run --rm batch batch.jobs.build_support_program
```

## 테스트와 린트

```bash
pytest -q
ruff check .
```

- DB 테스트는 `DATABASE_URL`로만 DB에 붙고 테스트마다 롤백한다. 개발 DB의 데이터는 바뀌지 않는다.
- 로컬에서 DB에 붙지 못하면 DB 테스트는 skip된다. CI(GitHub Actions)는 PostgreSQL 16 서비스 컨테이너를 띄우고, DB에 붙지 못하면 skip이 아니라 실패한다.

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
