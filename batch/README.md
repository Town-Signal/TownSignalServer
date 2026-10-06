# batch

사용자 요청과 무관하게 주기 실행되어 서빙 표를 채운다. API로 노출하지 않는다.

| 작업 | 실행 | 주기 · 실행 위치 | 적재 표 | 담당 · 상태 |
| --- | --- | --- | --- | --- |
| 주간 배치 진입점 | `python -m batch.jobs.run_weekly` | 매주 · **EC2 cron** | recommendation(정리) | 백엔드 · 구현 |
| 지원사업 수집 · 구조화 · 적재 | `python -m batch.jobs.build_support_program` | 매주(run_weekly가 호출) · EC2 | `support_program` | 지원사업 담당 · **골격만** |
| 자치구 임대료 집계 | (예정) `batch.jobs.build_district_rent` | 임대료 기준 분기 변경 시 · 팀원 PC | `district_rent` | 데이터 · 미작성 |
| 학습 · 전 조합 예측 · 점수 선계산 | `python -m batch.jobs.train_and_predict` | 상권 기준 분기 변경 시 · **팀원 PC → SSH 터널** | `prediction` | AI · **골격만** |
| 행정동 × 업종 요약 생성 | `python -m batch.jobs.build_summary_cache` | prediction 갱신 후 · 팀원 PC | `summary_cache` | AI · **골격만** |
| **로컬 전용** 더미 데이터 적재 | `python -m batch.jobs.load_dev_fixtures` | 필요할 때 · 로컬 | 로컬 DB 15개 표 전체 ([data/dev](../data/dev/README.md)) | 백엔드 · 구현 |

- **골격만**인 작업은 함수 시그니처와 docstring(입력 · 출력 · 적재 표 · 명세 절)만 있고 본문은 `NotImplementedError`다. 담당 팀원이 본문을 채운다.
- `load_dev_fixtures`는 실제 데이터가 아닌 더미를 넣는다. 환경변수 `APP_ENV=local`이 명시돼 있고 DB가 로컬일 때만 돈다.
- 운영 작업(run_weekly · build_support_program · train_and_predict · build_summary_cache)에는 APP_ENV 가드를 두지 않는다.
  run_weekly는 운영 EC2에서 도는 것이 정상이고(명세 12.3에서 APP_ENV는 batch에 주입하지 않음),
  학습 · 요약은 팀원 PC에서 SSH 터널로 운영 DB에 적재하는 것이 정상 경로다(판정 38).

### run_weekly (명세 4.6)

cron(가정: 매주 월요일 03:00 KST)이 배치 이미지로 실행한다. 단계를 각각 독립 실행하고 단계마다 성공 · 실패와 행 수를 로그로 남기며,
하나라도 실패하면 종료 코드 1이다. **학습 · 예측 · 요약은 실행하지 않는다.**

1. 지원사업 수집 · 구조화 — `build_support_program.main()` (구현 전에는 '미구현 실패'로 기록되고 다음 단계는 계속)
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

모델 A(점포당 매출, XGBoost 분위수 회귀) · 모델 B(생존기간, XGBoost AFT)의 학습 규격과
분할 방식은 [시스템 구조 v0.9](../docs/타운시그널_시스템구조_v0.9.md) 5장과 전체 명세 6장을 따른다.

## 로컬 실행

```bash
docker compose --profile batch run --rm batch batch.jobs.run_weekly
```
