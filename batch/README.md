# batch

사용자 요청과 무관하게 주기 실행되어 서빙 표를 채운다. API로 노출하지 않는다.

| 작업 | 실행 | 주기 | 산출물 |
| --- | --- | --- | --- |
| 지원사업 수집·구조화 | `python -m batch.jobs.build_support_program` | 매주 | `support_program` |
| 자치구 임대료 집계 | (예정) `batch.jobs.build_district_rent` | 기준 분기 변경 시 | `district_rent` |
| 모델 학습·전 조합 예측·점수 선계산 | (예정) `batch.jobs.train_and_predict` | 기준 분기 변경 시 | `prediction` |
| 행정동 × 업종 요약 생성 | (예정) `batch.jobs.build_summary_cache` | 기준 분기 변경 시 | `summary_cache` |
| **로컬 전용** 더미 데이터 적재 | `python -m batch.jobs.load_dev_fixtures` | 필요할 때 | 로컬 DB 15개 표 전체 ([data/dev](../data/dev/README.md)) |

`load_dev_fixtures`는 실제 데이터가 아닌 더미를 넣는다. 환경변수 `APP_ENV=local`이 명시돼 있고 DB가 로컬일 때만 돈다.

cron은 주 1회 `run_weekly`(예정)를 실행한다. 매주 다시 만드는 것은 지원사업표뿐이고,
임대료·예측·요약은 원천(상권·인구)이 분기 갱신이므로 기준 분기가 바뀐 주에만 재생성한다.
표 교체는 트랜잭션으로 하고, 실패하면 직전 표를 유지한다.

모델 A(점포당 매출, XGBoost 분위수 회귀) · 모델 B(생존기간, XGBoost AFT)의 학습 규격과
분할 방식은 [시스템 구조 v0.9](../docs/타운시그널_시스템구조_v0.9.md) 5장을 따른다.

## 로컬 실행

```bash
docker compose --profile batch run --rm batch batch.jobs.build_support_program
```
