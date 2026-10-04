# data/dev — 로컬 개발용 더미 데이터 (실제 데이터 아님)

화면과 API를 끝까지 돌려 보기 위한 **더미** 데이터다. 실제 원천 데이터 · seed · 지원사업 · 모델 결과가 아니다.
실제 수작업 파일은 `data/seed/`에 사람이 만든다. 이 폴더의 파일을 `data/seed/`로 옮기지 않는다.

## 적재

```bash
# .env에 APP_ENV=local이 있어야 한다(.env.example 기본값). 비어 있거나 local이 아니면 거부한다.
python -m batch.jobs.load_dev_fixtures            # 로컬 DB를 비우고 더미를 다시 넣는다
python -m batch.jobs.load_dev_fixtures --force    # 더미가 아닌 행이 있어도 지우고 넣는다
```

- 환경변수 `APP_ENV`가 명시적으로 `local`이고 DB 호스트가 `localhost · 127.0.0.1 · ::1 · db`일 때만 돈다.
  운영 EC2도 compose 안에서는 DB 호스트가 `db`라서, APP_ENV를 반드시 함께 본다.
- 한 트랜잭션에서 15개 표를 모두 비우고(`TRUNCATE … RESTART IDENTITY`) 다시 넣는다. 몇 번을 돌려도 결과가 같다.
  로컬에서 API로 만든 추천 기록(recommendation · rec_item)도 지워진다.
- 더미 표식이 없는 행(dong_code 6번째 자리가 9가 아닌 행정동, 이름이 `[더미]`로 시작하지 않는 지원사업)이 있으면
  지우지 않고 거부한다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `dummy_districts.json` | 자치구 25개(행정 코드 · 이름 · 2013 경계 geo_code) |
| `dummy_dongs.json` | 행정동 54개. dong_code는 `{구코드}9{nn}` 더미 코드, 이름 · geo_code는 TownSignalFE `public/geo`와 같음 |
| `dummy_industries.json` | 업종 7개(외식업 3 · 서비스업 2 · 소매업 2) |
| `dummy_rent.json` | 조사 상권 46개의 ㎡당 월세(천원). 자치구 값은 로더가 상권 평균으로 계산 |
| `dummy_support_programs.json` | 지원사업 11개(행마다 `case`에 어떤 경우인지 적음) + 추출 검증 기록 8개 |
| `dummy_special_cases.json` | 필수 경우를 어느 동 · 업종 조합에 둘지 |

매출 · 점포 · 인구 · 예측 원값은 `batch/dev/fixtures.py`가 `random.Random("townsignal-dev:…")`로 결정적으로 만든다.
백분위 · total_score · score_rank는 손으로 넣지 않고 `batch/pipeline/scores.py`가 `common.scoring`으로 계산한다.

## 행 수

| 표 | 행 수 |
| --- | --- |
| district · dong · industry | 25 · 54 · 7 |
| commercial_market · district_rent | 46 · 25 |
| support_program · program_verification | 11 · 8 |
| store_quarterly · sales_quarterly · population_quarterly | 2,968 · 2,888 · 432 (8분기 20233 ~ 20252) |
| prediction | 413 (xgb_v1 361 + mlp_v1 52) |
| summary_cache | 50 |
| business_license · recommendation · rec_item | 0 |

## 필수 경우가 있는 곳

| 경우 | 행 |
| --- | --- |
| 도봉구 임대료 없음 | district_rent 11320: rent_per_sqm NULL · confidence 없음 → passed "확인불가" |
| 조사 상권 1개 '낮음' 구 | 금천구 11545 · 강북구 11305 |
| 관악구 회귀값 | 상권 27.0 · 30.0 → 28.5 → 33㎡ 추정 임대비용 16,929,000원 |
| 전국 배타 2개 | #1 A 2,000만 · #2 B 700만(A와 함께면 제외) |
| 비배타 | #3 300만(경력 2년↑ 또는 조리 · 제과기능사, 상시) · #4 100만(조건 없음, 2025-12-31 마감) · #11 500만(40세↑) |
| 구 전용 사업 | #5 관악 800만 · #6 마포 2,500만 배타 · #7 금천 900만(외식업) · #8 성동 600만(34세↓) · #9 강남 2,000만 배타(A와 동률) |
| 미검수 공고 | #10 verified_by NULL |
| 점포 데이터 없는 동 | 청운효자동(11110901) — 점포 · 매출 · 예측 없음 |
| 예측 없는 조합 | 회기동(11230901) 전 업종, 구로3동 × 외국어학원, 상봉1동 × 슈퍼마켓, 녹번동 × 분식전문점 |
| 표본 부족 | 방학3동(11320901) 전 업종, 공릉2동 × 외국어학원 — 최근 4분기 평균 점포 수 < 5, 예측은 있음 |
| growth_rate NULL | 외국어학원(CS200002) 전체, 망원1동(11440903) 전 업종 |
| growth 신뢰도 낮음 | 가산동(11545901) · 방학3동 |
| 요약 없음 | 한식음식점만 요약이 있고, 그중 신림동 · 낙성대동은 비움 |
| 서빙 외 모델 | mlp_v1 — 한식음식점 52행 |
| geo_code 없는 동 · 이름 같은 동 | 위례동(2015 신설) · 신사동(강남구 · 관악구) |

나이 27 · 경력 0 · 조리기능사 · 한식음식점 기준 지원금 합계: 일반 구 2,400만, 관악 3,200만, 마포 2,900만, 금천 3,300만, 성동 3,000만, 강남 2,400만.
