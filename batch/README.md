# batch

사용자 요청과 무관하게 주기 실행되어 서빙 표를 채운다. API로 노출하지 않는다.

| 작업 | 실행 | 주기 · 실행 위치 | 적재 표 | 담당 · 상태 |
| --- | --- | --- | --- | --- |
| 주간 배치 진입점 | `python -m batch.jobs.run_weekly` | 매주 · **EC2 cron** | recommendation(정리) | 백엔드 · 구현 |
| 지원사업 수집 · 구조화 · 적재 | `python -m batch.jobs.build_support_program` | 매주(run_weekly가 호출) · EC2 | `support_program` | 지원사업 담당 · **초안 구현**(LLM 호출 미연결) |
| 자치구 임대료 집계 | (예정) `batch.jobs.build_district_rent` | 임대료 기준 분기 변경 시 · 팀원 PC | `district_rent` | 데이터 · 미작성 |
| 학습 · 전 조합 예측 · 점수 선계산 | `python -m batch.jobs.train_and_predict` | 상권 기준 분기 변경 시 · **팀원 PC → SSH 터널** | `prediction` | AI · **모델 A 구현**(모델 B · 성장세는 골격) |
| 추천 순위 백테스트 | `python -m batch.jobs.backtest` | 모델 재학습 때 · 팀원 PC (DB 읽기만) | 없음 — `models/backtest.json` | AI · **구현** (분석계획서 6.1 · R1) |
| 행정동 × 업종 요약 생성 | `python -m batch.jobs.build_summary_cache` | prediction 갱신 후 · 팀원 PC | `summary_cache` | AI · **골격만** |
| **로컬 전용** 더미 데이터 적재 | `python -m batch.jobs.load_dev_fixtures` | 필요할 때 · 로컬 | 로컬 DB 15개 표 전체 ([data/dev](../data/dev/README.md)) | 백엔드 · 구현 |

- **골격만**인 작업은 함수 시그니처와 docstring(입력 · 출력 · 적재 표 · 명세 절)만 있고 본문은 `NotImplementedError`다. 담당 팀원이 본문을 채운다.
- 지원사업 작업은 K-Startup 수집 · 구조화 · 적재까지 구현돼 있고, LLM 호출(`batch/llm/client.py`)만 아직 연결 전이다.
  지금 실행하면 공고를 수집한 뒤 LLM 단계에서 `NotImplementedError`로 끝난다. `PUBLIC_DATA_API_KEY`가 필요하다.
- `load_dev_fixtures`는 실제 데이터가 아닌 더미를 넣는다. 환경변수 `APP_ENV=local`이 명시돼 있고 DB가 로컬일 때만 돈다.
- 운영 작업(run_weekly · build_support_program · train_and_predict · build_summary_cache)에는 APP_ENV 가드를 두지 않는다.
  run_weekly는 운영 EC2에서 도는 것이 정상이고(명세 12.3에서 APP_ENV는 batch에 주입하지 않음),
  학습 · 요약은 팀원 PC에서 SSH 터널로 운영 DB에 적재하는 것이 정상 경로다(판정 38).

### run_weekly (명세 4.6)

cron(가정: 매주 월요일 03:00 KST)이 배치 이미지로 실행한다. 단계를 각각 독립 실행하고 단계마다 성공 · 실패와 행 수를 로그로 남기며,
하나라도 실패하면 종료 코드 1이다. **학습 · 예측 · 요약은 실행하지 않는다.**

1. 지원사업 수집 · 구조화 — `build_support_program.main()` (LLM 연결 전에는 '미구현 실패'로 기록되고 다음 단계는 계속)
2. 생성 후 30일이 지난 recommendation 삭제 — `batch.pipeline.cleanup.delete_expired_recommendations` (30일 **초과**, rec_item은 CASCADE)
3. support_program · program_verification 백업 — `batch.pipeline.backup`이 pg_dump 명령(비밀번호는 PGPASSWORD 환경변수)과
   지울 옛 백업(최근 4개 유지)을 조립해 로그로 남긴다. **아직 실행하지 않는다** — 배치 이미지에 postgresql-client를 넣은 뒤 연결한다.
   백업 폴더는 `BACKUP_DIR`(기본 `backups`).

### 표 교체 규칙 (명세 4.6 · 6.5)

`batch.pipeline.swap`이 스테이징 표에 먼저 적재한 뒤 한 트랜잭션 안에서 교체한다. 실패하면 롤백되어 직전 표가 그대로 남는다.

- `replace_predictions(conn, rows, model_version)` — prediction 적재는 반드시 이 함수로 한다.
  적재 직전에 `fill_scores`(common.scoring)로 백분위 · total_score · score_rank를 채우고, ON CONFLICT 갱신 + 새 목록에 없는 조합 삭제 +
  해당 조합의 summary_cache 삭제(서빙 모델일 때)를 같은 트랜잭션에서 한다. 교체 단위는 model_version 전체다.
- `replace_rows(conn, table, rows, key_columns, scope)` — 다른 표에도 쓰는 일반 함수.

### train_and_predict (명세 6.2 ~ 6.5)

팀원 PC에서 실행한다. 모델 A(점포당 분기 매출, XGBoost 분위수 회귀)는 `batch/ml/sales.py`에 있다.
모델 B(생존기간, XGBoost AFT)와 성장세는 아직 골격이라 그 칼럼은 NULL로 둔다.

```bash
python -m batch.jobs.train_and_predict                  # 학습 · 평가 · 모델 저장만 (DB는 그대로)
python -m batch.jobs.train_and_predict --write          # prediction 표까지 교체
```

- `--model-version`(기본 xgb_v1) · `--model-dir`(기본 `models`, 환경변수 `MODEL_DIR`)을 바꿀 수 있다.
- 결과는 `models/<model_version>/`에 분위수별 모델(sales_q10 · q50 · q90.json)과 `metrics.json`(테스트 지표 · 분할 · 커밋 SHA)으로 남는다.
  `models/`는 저장소에 올리지 않고 팀 드라이브에 보관한다.
- 분할은 마지막 2개 분기 = 테스트, 그 앞 2개 분기 = 검증. 2024Q1 공간 기준 경계를 넘는 시차 피처는 결측으로 둔다.
- **모델 B 전에 운영 DB에 `--write` 하면 생존 칼럼이 전부 NULL이 되고 종합점수가 매출만으로 계산된다.**

학습 규격과 분할 방식은 노션 「시스템 구조」 5장과 전체 명세 6장을 따른다.

## 로컬 실행

```bash
docker compose --profile batch run --rm batch batch.jobs.run_weekly
```

### backtest (분석계획서 6.1 · R1)

과거 기준 분기 T 이후 데이터를 지우고 train_and_predict와 같은 코드로 순위를 만든 뒤, 지운 T+1 ~ T+2 분기의
실제 점포당 매출 · 폐업률과 비교한다. 같은 비교를 'T 분기 실제 매출 순위'(naive)로도 해서 모델 순위가 더 나은지 본다.

```bash
python -m batch.jobs.backtest                          # 기준 = 마지막 분기 − 2
python -m batch.jobs.backtest --cutoff 20244 --cutoff 20252
```

- 지표: 업종 안 Spearman 순위 상관 · 상위 20% 적중률(무작위면 약 0.2) · 상위/하위 20% 실제 평균 배율. 업종별로 재고 행정동 수로 가중 평균
- 표본 부족 조합은 평가에서 빼고, 2024Q1 공간 기준 경계를 넘는 기준 분기는 거부한다
- 결과는 `models/backtest.json`(커밋 SHA 포함). 분석계획서 실험 기록 R1 행으로 옮긴다

