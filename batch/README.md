# batch

사용자 요청과 무관하게 주기 실행되어 서빙 표를 채운다. API로 노출하지 않는다.

| 작업 | 실행 | 주기 | 산출물 |
| --- | --- | --- | --- |
| 지원사업 수집·구조화 | `python -m batch.jobs.build_support_program` | 주 1회 | `support_program` |
| 임대료 추정 | (예정) | 분기 1회 | `district_rent` |
| 모델 학습·전 조합 예측 | (예정) | 분기 1회 | `prediction` |
| 행정동 요약 생성 | (예정) | 분기 1회 | `summary_cache` |

임대료·예측·요약은 원천(상권·인구)이 분기 갱신이므로 기준 분기가 바뀐 주에만 재생성한다.

## 로컬 실행

```bash
docker compose --profile batch run --rm batch batch.jobs.build_support_program
```
