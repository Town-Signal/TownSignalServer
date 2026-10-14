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
| `prediction` | 행정동 × 업종 예측(매출 80% 구간 · 1 · 3년 생존 확률, 성장세, 종합점수) | 팀원 PC(분기 1회 학습) |
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

## 실데이터로 로컬 DB 채우기 · 모델 만들기

더미 대신 실데이터로 화면을 보거나 모델을 다시 만들 때. 원천 파일은 필요 없고, 데이터 담당이 [팀 구글 드라이브](https://drive.google.com/drive/folders/1GTuJ8_3WFgiVLr34jAYI72gp4TP0zBqY)(팀원만 접근 가능, 열리지 않으면 데이터 담당에게 권한 요청)에 올린 **전처리 덤프**(예: `townsignal_preprocessed_20261009.dump`, 9개 표 — district · dong · industry · sales_quarterly · store_quarterly · population_quarterly · business_license · commercial_market · district_rent)만 있으면 된다.

### 1. 덤프 넣기

덤프 파일은 **저장소 밖**(예: 저장소 옆 폴더)에 둔다. `*.dump`는 .gitignore에 없어서 저장소 안에 두면 커밋될 수 있다.

```bash
# (선택) 지금 로컬 DB의 지원사업 · 검수 기록을 따로 떠 두기 — 실제 지원사업 데이터가 아직 없을 때 더미 공고를 다시 쓰려면
pg_dump -h localhost -U townsignal -d townsignal -Fc --data-only -t support_program -t program_verification -f ../local_support.dump

# 1) 로컬 DB를 비우고 표 다시 만들기 (로컬 DB 데이터가 전부 지워진다)
psql -h localhost -U townsignal -d townsignal -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql -h localhost -U townsignal -d townsignal -v ON_ERROR_STOP=1 -f db/schema.sql

# 2) 덤프 넣기 — 슈퍼유저로 (아래 표 참고)
pg_restore -h localhost -U <슈퍼유저> -d townsignal --data-only --disable-triggers --single-transaction ../townsignal_preprocessed_YYYYMMDD.dump
pg_restore -h localhost -U <슈퍼유저> -d townsignal --data-only --disable-triggers --single-transaction ../local_support.dump   # (선택)

# 3) 확인 — 기대값: 구 25 · 동 425 · 업종 100 · 인허가 약 82만
psql -h localhost -U townsignal -d townsignal -c \
  "SELECT (SELECT count(*) FROM district) 구, (SELECT count(*) FROM dong) 동, (SELECT count(*) FROM industry) 업종, (SELECT count(*) FROM business_license) 인허가;"
```

| 로컬 DB | 슈퍼유저 | 명령 앞에 붙일 것 |
| --- | --- | --- |
| Homebrew 등 직접 설치한 PostgreSQL | 설치한 PC 사용자 이름(macOS 사용자명) | 없음 |
| docker compose의 db | `townsignal` | `docker compose exec -T db` (파일은 `< 경로`로 넘긴다) |

- `--disable-triggers`는 슈퍼유저만 쓸 수 있다. `townsignal` 계정이 슈퍼유저가 아니면 `permission denied: ... is a system trigger`로 실패한다. `--single-transaction`이라 실패해도 반쯤 들어가지 않고 전부 취소된다.
- 5432 포트를 누가 쓰는지는 `lsof -nP -iTCP:5432 -sTCP:LISTEN`으로 본다(`postgres`면 직접 설치, `com.docke`면 Docker).
- 덤프에는 **prediction이 없다**. 이대로면 추천 3단계가 0곳이고 점수 · 예측이 비어 있다 → 2번에서 만든다.

### 2. 모델 학습 · prediction 만들기

```bash
source .venv/bin/activate
brew install libomp                       # macOS만 — xgboost에 필요
pip install -r requirements/batch.txt     # 학습 라이브러리(약 600MB)

grep DATABASE_URL .env                    # 반드시 localhost — SSH 터널 주소면 운영 DB가 바뀐다
python -m batch.jobs.train_and_predict            # 학습 · 평가 · 모델 저장만 (DB는 그대로)
python -m batch.jobs.train_and_predict --write    # prediction 표까지 교체

psql -h localhost -U townsignal -d townsignal -c "SELECT count(*), count(survival_prob_3y) FROM prediction;"
```

- `--write`를 주면 모델 A(매출) · 모델 B(1 · 3년 생존 확률) · 성장세 · 종합점수를 계산해 `replace_predictions`로 prediction을 교체한다. 2026. 10. 13. 실데이터 기준 18,950행(생존 확률은 15개 업종 5,527행), MacBook Air에서 약 10분.
- 모델 파일 · 지표는 `models/<model_version>/`(sales_q10 · q50 · q90.json, survival_aft.json, metrics.json)에 남는다. `models/`는 커밋하지 않고 팀 드라이브에 보관한다.
- 끝나면 학습 라이브러리는 지워도 된다(API · 테스트는 `dev.txt`만 쓴다). 가장 깔끔한 방법은 `.venv`를 새로 만들고 `pip install -r requirements/dev.txt`, `pip cache purge`.
- 운영 DB에 적재하는 경로(SSH 터널)와 model_version 교체 시점은 노션 「배포」 5장을 따른다. 학습 세부 규격은 [batch/README.md](batch/README.md) · 전체 명세 6장.

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
| `deploy.yml` | develop push → **CI 성공 시 자동**(수동 실행도 가능) | 이미지를 ECR에 올리고 EC2에서 교체 → `/health` 확인, 실패하면 직전 버전으로 자동 롤백 |

- 배포 브랜치는 develop 하나다(main 없음).
- 운영 비밀값은 EC2의 `~/townsignal/.env`에만 있다(양식 `.env.prod.example`). GitHub에는 배포 접속 정보만 둔다.
- 모델 학습은 EC2가 아니라 팀원 PC에서 하고, 결과 표만 SSH 터널로 운영 DB에 넣는다.
- AWS가 무료 플랜이라 리전은 시드니, 인증은 배포 전용 IAM 사용자 키다. 자세한 절차 · 이유는 노션 「배포」.

## 커밋 규칙

- 커밋 `<Type>(<Scope>): <Subject>` · PR 제목 `<Type>: <Subject>`
- Type: feat · fix · docs · style · refactor · chore · todo. 제목은 명령조, 날짜는 적지 않는다.
