# TownSignalServer

타운시그널의 파이썬 서버. API와 배치가 한 저장소에 있고 `common/`을 공유한다.
프론트엔드는 [TownSignalFE](https://github.com/Town-Signal/TownSignalFE)에 따로 있다.

## 구조

```
townsignal/
├── api/        FastAPI 동기식. 서빙 표 조회 + 사칙연산만 한다
├── batch/      수집 · 전처리 · 모델 학습 · 표 생성 (API로 노출하지 않는다)
├── common/     evaluator · budget · scoring · constants
├── data/       raw · processed · seed
└── tests/
```

`common/`은 프레임워크와 DB에 의존하지 않는 순수 함수만 둔다. 배치와 API가 같은 코드를
불러 써야 배치가 만든 표와 요청 시 계산이 어긋나지 않는다. 요청 DTO는 `api/schemas/`에만 둔다.

## 왜 이렇게 나누나

배치가 세 표(`support_program` · `district_rent` · `prediction`)를 미리 채워두고,
요청 시에는 그 표를 조회해 더하고 빼기만 한다. 그래서 API 이미지에는 모델 라이브러리가
들어가지 않고 응답이 1초 안에 끝난다. 자세한 내용은 시스템 설계서를 참고한다.

## 로컬 실행

```bash
cp .env.example .env
docker compose up --build        # db + api (http://localhost:8000/docs)
```

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
