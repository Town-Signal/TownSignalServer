# TownSignalServer

타운시그널의 파이썬 서버(API + 배치). 프론트엔드는 [TownSignalFE](https://github.com/Town-Signal/TownSignalFE).

- 서비스: https://town-signal.vercel.app
- 설계 문서는 노션에만 둔다 — 「타운시그널 전체 명세」(구현 기준) · 「시스템 구조」 · 「배포」. DB 기준본은 [db/schema.sql](db/schema.sql)

## 한눈에 보기

배치가 서빙 표를 미리 채우고, API는 그 표를 조회해 더하고 빼기만 한다. 그래서 API에는 모델이 없고 응답이 1초 안에 끝난다.

| 표 | 내용 | 채우는 곳 |
| --- | --- | --- |
| `support_program` | 지원사업 금액 · 자치구 · 자격조건 · 중복수혜 | 주간 배치(EC2) |
| `district_rent` | 자치구 25곳의 ㎡당 월세 | 팀원 PC |
| `prediction` | 행정동 × 업종 예측(매출 · 생존 구간, 성장세, 종합점수) | 팀원 PC(분기 1회 학습) |
| `summary_cache` | 행정동 × 업종 요약문(LLM) | 팀원 PC |

```
api/          FastAPI(동기). 요청 DTO는 api/schemas/에만
batch/        수집 · 전처리 · 학습 · 표 생성 — 작업 목록은 batch/README.md
common/       자격 판정 · 예산 · 점수 계산(순수 함수, api와 batch가 같이 씀)
db/           schema.sql
data/         seed · dev(더미)
docs/         노션 문서에 들어가는 그림
tests/
compose.prod.yml   운영(EC2) 구성 · docker-compose.yml 로컬 구성
```

## 로컬 실행

Python **3.11**과 Docker가 필요하다. Windows는 Git Bash에서 아래 명령을 그대로 쓴다.

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # Windows: py -3.11 -m venv .venv && source .venv/Scripts/activate
pip install -r requirements/dev.txt
cp .env.example .env

docker compose up -d db                                                   # PostgreSQL 16
docker compose exec -T db psql -U townsignal -d townsignal -v ON_ERROR_STOP=1 < db/schema.sql
python -m batch.jobs.load_dev_fixtures                                    # 더미 데이터(로컬 전용)

uvicorn api.main:app --reload                                             # http://localhost:8000/docs
```

- 로컬 DB는 각자 PC에 따로 생긴다. 접속 정보는 `.env.example`의 `DATABASE_URL` 그대로(`townsignal` / `townsignal`, 5432).
- 5432 포트를 다른 PostgreSQL이 쓰고 있으면 db가 뜨지 않는다. Docker 없이 PostgreSQL 16을 직접 설치해도 되고, 같은 `DATABASE_URL`로 붙는다.
- `load_dev_fixtures`는 `APP_ENV=local`이고 DB가 로컬일 때만 돌고, 표를 비운 뒤 더미를 넣는다.
- 초기화: `docker compose down -v` 후 schema.sql부터 다시.

## 테스트

```bash
pytest -q && ruff check .
```

- DB 테스트는 테스트마다 롤백한다. 로컬에서 DB가 없으면 skip, CI에서는 실패한다.
- 모델 학습 테스트는 `requirements/batch.txt`가 설치돼 있어야 돈다(기본 CI는 건너뛰고 `ci-batch`가 돌린다).

| 의존성 | 용도 |
| --- | --- |
| `requirements/api.txt` | API |
| `requirements/batch.txt` | 전처리 · 모델 학습(팀원 PC). Windows에서 Python 3.13은 scikit-survival 설치가 안 된다 |
| `requirements/weekly.txt` | 운영 주간 배치 이미지(학습 라이브러리 없음) |
| `requirements/dev.txt` | 테스트 · 린트(api 포함) |

## 배포

웹은 Vercel, API · DB는 AWS EC2 한 대(시드니)에서 `compose.prod.yml`로 돈다. 웹의 `/api/*`는 Vercel이 EC2로 넘긴다.

| 워크플로 | 언제 | 하는 일 |
| --- | --- | --- |
| `ci.yml` | 모든 PR · develop push | ruff + pytest |
| `ci-batch.yml` | batch · common · requirements 변경 | 배치 의존성까지 설치해 전체 테스트 |
| `deploy.yml` | **수동 실행**(Actions → Deploy) | 이미지를 ECR에 올리고 EC2에서 교체 → `/health` 확인, 실패하면 직전 버전으로 자동 롤백 |

- 배포 브랜치는 develop 하나다(main 없음).
- 운영 비밀값은 EC2의 `~/townsignal/.env`에만 있다(양식 `.env.prod.example`). GitHub에는 배포 접속 정보만 둔다.
- 모델 학습은 EC2가 아니라 팀원 PC에서 하고, 결과 표만 SSH 터널로 운영 DB에 넣는다.
- AWS가 무료 플랜이라 리전은 시드니, 인증은 배포 전용 IAM 사용자 키다. 자세한 절차 · 이유는 노션 「배포」.

## 커밋 규칙

- 커밋 `<Type>(<Scope>): <Subject>` · PR 제목 `<Type>: <Subject>`
- Type: feat · fix · docs · style · refactor · chore · todo. 제목은 명령조, 날짜는 적지 않는다.
