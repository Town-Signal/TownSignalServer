/* =============================================================================
   타운시그널 (TownSignal) 전체 데이터베이스 DDL 명세 (schema.sql)
   - PostgreSQL 16 · 15테이블 + 뷰 1
   - 2026.09.29 로그인 삭제(기능명세서 v0.7) 반영: app_user · favorite 제거,
     recommendation.user_id 제거. 즐겨찾기 · 최근 추천 목록은 브라우저 localStorage.

   ============================================================================= */

BEGIN;

-- UUID 생성을 위한 pgcrypto 확장 (PostgreSQL 13+ 내장 gen_random_uuid 사용)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

/* =============================================================================
   1. 마스터 테이블 (district, dong, industry)
   ============================================================================= */
CREATE TABLE district (                              -- 자치구
  district_code   CHAR(5)      PRIMARY KEY,          -- 11680 = 강남구
  name            VARCHAR(20)  NOT NULL UNIQUE,
  geo_code        CHAR(5)                            -- 자치구 경계 GeoJSON 코드(통계청 2013, 예: 11250 = 강동구)
);

CREATE TABLE dong (                                  -- 행정동
  dong_code       CHAR(8)      PRIMARY KEY,          -- 11110515 = 청운효자동
  name            VARCHAR(30)  NOT NULL,
  district_code   CHAR(5)      NOT NULL REFERENCES district (district_code),
  geo_code        CHAR(7)                            -- 행정동 경계 GeoJSON 코드(통계청 2013, 7자리). 경계가 없는 동은 NULL
);
CREATE INDEX idx_dong_district ON dong (district_code);
-- 자치구 코드는 행정동 코드 앞 5자리. 따로 받을 필요 없음.

CREATE TABLE industry (                              -- 업종
  industry_code   CHAR(8)      PRIMARY KEY,          -- CS100001 = 한식음식점
  name            VARCHAR(40)  NOT NULL,
  category        VARCHAR(20)  NOT NULL              -- 외식업 / 서비스업 / 소매업 (서울시 상권분석 대분류)
);
CREATE INDEX idx_industry_category ON industry (category);
-- 원천 데이터에는 소분류만 있음. category는 우리가 붙임.
-- 사용자는 category로 고르고, 예측은 industry_code 단위로 돌림.

/* =============================================================================
   2. 상권 지표 테이블 (sales_quarterly, store_quarterly, population_quarterly, business_license)
   - 배치 적재, 서비스는 읽기 전용
   ============================================================================= */
CREATE TABLE sales_quarterly (                       -- 추정매출 OA-22175
  dong_code       CHAR(8)   NOT NULL REFERENCES dong (dong_code),
  industry_code   CHAR(8)   NOT NULL REFERENCES industry (industry_code),
  year_quarter    CHAR(5)   NOT NULL,                -- 20251
  amount          BIGINT    NOT NULL,
  txn_count       INTEGER   NOT NULL,
  weekday_amount  BIGINT,  weekend_amount BIGINT,
  hour_00_06 BIGINT, hour_06_11 BIGINT, hour_11_14 BIGINT,
  hour_14_17 BIGINT, hour_17_21 BIGINT, hour_21_24 BIGINT,
  male_count INTEGER, female_count INTEGER,
  age_10 INTEGER, age_20 INTEGER, age_30 INTEGER,
  age_40 INTEGER, age_50 INTEGER, age_60 INTEGER,
  PRIMARY KEY (dong_code, industry_code, year_quarter)
);
CREATE INDEX idx_sales_dong_q ON sales_quarterly (dong_code, year_quarter);
-- 2021년 이후만 제공(20분기). 2024년부터 공간기준이 표준단위구역으로 바뀌었으므로
-- 2023↔2024 경계를 그냥 이어붙여 성장세를 계산하면 안 됨.

CREATE TABLE store_quarterly (                       -- 점포 OA-22172
  dong_code       CHAR(8)   NOT NULL REFERENCES dong (dong_code),
  industry_code   CHAR(8)   NOT NULL REFERENCES industry (industry_code),
  year_quarter    CHAR(5)   NOT NULL,
  store_count     INTEGER   NOT NULL,
  franchise_count INTEGER, open_count INTEGER, close_count INTEGER,
  PRIMARY KEY (dong_code, industry_code, year_quarter)
);

CREATE TABLE population_quarterly (                  -- 길단위인구 OA-22178
  dong_code    CHAR(8) NOT NULL REFERENCES dong (dong_code),
  year_quarter CHAR(5) NOT NULL,
  total        BIGINT  NOT NULL,
  male BIGINT, female BIGINT,
  age_10 BIGINT, age_20 BIGINT, age_30 BIGINT,
  age_40 BIGINT, age_50 BIGINT, age_60 BIGINT,
  time_00_06 BIGINT, time_06_11 BIGINT, time_11_14 BIGINT,   -- 시간대별 인구(⑪ time_slots)
  time_14_17 BIGINT, time_17_21 BIGINT, time_21_24 BIGINT,
  PRIMARY KEY (dong_code, year_quarter)
);

CREATE TABLE business_license (                      -- 인허가 원본 LOCALDATA
  license_id    VARCHAR(40) PRIMARY KEY,
  dong_code     CHAR(8)     REFERENCES dong (dong_code),
  industry_code CHAR(8)     REFERENCES industry (industry_code),
  open_date     DATE        NOT NULL,                -- 인허가일자
  close_date    DATE,                                -- NULL = 아직 영업 중
  status        VARCHAR(10),
  CONSTRAINT chk_license_dates CHECK (close_date IS NULL OR close_date >= open_date)
);
CREATE INDEX idx_license_dong_ind ON business_license (dong_code, industry_code);
CREATE INDEX idx_license_open     ON business_license (open_date);
-- close_date가 NULL인 행이 상당수임. 버리고 학습하면 생존기간이 심하게
-- 짧게 추정됨. 일반 회귀가 아니라 생존분석을 써야 하는 이유.

/* =============================================================================
   3. 임대료 테이블 (commercial_market, district_rent)
   ============================================================================= */
CREATE TABLE commercial_market (                     -- 부동산원 조사 상권 서울 59개
  market_name   VARCHAR(40)  NOT NULL,               -- 명동 / 신림역 / 천호
  base_quarter  CHAR(6)      NOT NULL,               -- 2026Q2
  district_code CHAR(5)      NOT NULL REFERENCES district (district_code),
  rent_per_sqm  NUMERIC(8,2) NOT NULL,               -- 천원/㎡ · 월
  PRIMARY KEY (market_name, base_quarter)
);
-- 자치구 매핑은 수작업 결과. shapefile의 .dbf에 시군구 필드가 없음.
-- 나중에 공시지가로 행정동 해상도로 내려갈 때의 재료이므로 원본을 남김.

CREATE TABLE district_rent (                         -- 자치구 집계. 필터가 읽는 표
  district_code      CHAR(5)      PRIMARY KEY REFERENCES district (district_code),
  base_quarter       CHAR(6)      NOT NULL,
  rent_per_sqm       NUMERIC(8,2),                   -- 도봉구는 NULL
  deposit_multiplier NUMERIC(5,2) NOT NULL DEFAULT 15,
  market_count       INTEGER      NOT NULL DEFAULT 0,
  confidence         VARCHAR(10)  NOT NULL
    CHECK (confidence IN ('높음','낮음','없음'))
);
-- 상권 2개 이상 = 높음 / 1개 = 낮음(8개 구) / 0개 = 없음(도봉구)
-- 보증금 = rent_per_sqm × 면적 × deposit_multiplier. 공공데이터에 상가
-- 보증금이 없어 가정한 값임.
-- rent_per_sqm이 NULL인 도봉구를 필터에서 탈락시키지 말 것.
-- 데이터 없음을 비싸다로 처리하면 멀쩡한 지역이 통째로 사라짐.

/* =============================================================================
   4. 지원사업 테이블 (support_program, program_verification, v_extraction_accuracy)
   ============================================================================= */
CREATE TABLE support_program (
  program_id    INTEGER      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name          VARCHAR(200) NOT NULL,
  agency        VARCHAR(100),
  amount_max    BIGINT       NOT NULL CHECK (amount_max >= 0),
  district_code CHAR(5)      REFERENCES district (district_code),  -- NULL = 전국
  is_exclusive  BOOLEAN      NOT NULL DEFAULT FALSE,               -- 중복수혜 불가
  apply_start   DATE,  apply_end DATE,
  eligibility   JSONB        NOT NULL,
  source_url    VARCHAR(500),
  raw_text      TEXT,                                -- 공고 원문. 검증 대조용
  extracted_at  TIMESTAMPTZ,  verified_by VARCHAR(40)
);
CREATE INDEX idx_program_district ON support_program (district_code);
CREATE INDEX idx_program_deadline ON support_program (apply_end);
CREATE INDEX idx_program_elig     ON support_program USING GIN (eligibility);

/*  eligibility 예시 — 애플리케이션에서 재귀로 평가함.
    SQL로 트리를 파싱하려 들면 코드가 감당이 안 됨.
    행이 수백 개뿐이라 전부 읽어서 메모리에서 판정해도 밀리초.

{ "and": [
    { "field": "age",          "op": "<=",           "value": 39 },
    { "or": [
        { "field": "career_years", "op": ">=",           "value": 2 },
        { "field": "certificates", "op": "contains_any", "value": ["조리기능사"] }
    ]}
]}                                                                     */

CREATE TABLE program_verification (                  -- LLM 추출 정확도 검증 기록
  verification_id INTEGER     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  program_id      INTEGER     NOT NULL
                  REFERENCES support_program (program_id) ON DELETE CASCADE,
  field_name      VARCHAR(40) NOT NULL,              -- age / amount / district
  extracted_value VARCHAR(200),                      -- LLM이 뽑은 값
  actual_value    VARCHAR(200),                      -- 사람이 원문에서 읽은 값
  is_match        BOOLEAN     NOT NULL,
  checked_by      VARCHAR(40),
  checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_verif_program ON program_verification (program_id);

-- 계획서 성능목표 지원사업 정보 추출 정확도가 이 뷰 하나로 나옴.
CREATE VIEW v_extraction_accuracy AS
SELECT field_name,
       COUNT(*)                             AS checked_n,
       ROUND(100.0 * AVG(is_match::int), 1) AS accuracy_pct
FROM   program_verification
GROUP  BY field_name
ORDER  BY accuracy_pct;

/* =============================================================================
   5. 예측 결과 테이블 (prediction)
   - 행정동 426 × 업종 100 × 모델 2종 ≒ 약 85,200행 (기준 분기가 바뀐 주에만 재생성)
   ============================================================================= */
CREATE TABLE prediction (
  dong_code         CHAR(8)     NOT NULL REFERENCES dong (dong_code),
  industry_code     CHAR(8)     NOT NULL REFERENCES industry (industry_code),
  model_version     VARCHAR(30) NOT NULL,            -- xgb_v1 / mlp_v1
  survival_p10 NUMERIC(6,1), survival_p50 NUMERIC(6,1), survival_p90 NUMERIC(6,1),
  sales_p10    BIGINT,       sales_p50    BIGINT,    sales_p90    BIGINT,
  growth_rate       NUMERIC(6,3),
  growth_confidence VARCHAR(10) CHECK (growth_confidence IN ('높음','낮음')),
  sales_percentile    NUMERIC(5,4),                  -- 7.7 백분위(배치 선계산, 6.5)
  survival_percentile NUMERIC(5,4),
  growth_percentile   NUMERIC(5,4),
  total_score         NUMERIC(5,1),                  -- 종합점수 0-100
  score_rank          INTEGER,                       -- 같은 업종 · 모델 안의 순위(⑱)
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (dong_code, industry_code, model_version),
  CONSTRAINT chk_pred_survival CHECK (survival_p10 <= survival_p50
                                  AND survival_p50 <= survival_p90),
  CONSTRAINT chk_pred_sales    CHECK (sales_p10 <= sales_p50
                                  AND sales_p50 <= sales_p90)
);
CREATE INDEX idx_pred_rank ON prediction (industry_code, model_version, sales_p50 DESC);

-- 행정동 426 × 업종 100 × 모델 2종 ≒ 85,200행. cron은 주 1회지만
-- 원천이 분기 갱신이라 기준 분기가 바뀐 주에만 갈아끼움.
-- p10/p50/p90 = 80% 예측구간. 최솟값·최댓값이라 부르면 표본 min/max로 읽혀
-- 계획서 성능목표 예측 구간의 실측 포함률을 측정할 수 없게 됨.
-- model_version이 PK에 있어 XGBoost와 MLP를 나란히 저장·비교할 수 있음.

-- 배치 재적재 (MySQL의 ON DUPLICATE KEY UPDATE에 해당)
--   INSERT INTO prediction (...) VALUES (...)
--   ON CONFLICT (dong_code, industry_code, model_version)
--   DO UPDATE SET sales_p50 = EXCLUDED.sales_p50, computed_at = now();

-- 분위수는 내장 함수로. MySQL 8에는 없음.
--   SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)
--   FROM sales_quarterly WHERE industry_code = 'CS100001';

/* =============================================================================
   6. LLM 요약 캐시 테이블 (summary_cache)
   - Redis 대체용 영속 캐시
   ============================================================================= */
CREATE TABLE summary_cache (
  dong_code     CHAR(8)     NOT NULL REFERENCES dong (dong_code),
  industry_code CHAR(8)     NOT NULL REFERENCES industry (industry_code),
  summary_text  TEXT        NOT NULL,
  model_name    VARCHAR(40),
  generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (dong_code, industry_code)
);
-- 요약은 행정동 × 업종 조합당 한 번만 만들면 되고 자주 바뀌지 않음.
-- TTL이나 메모리 속도가 필요한 성격이 아니므로 테이블로 충분함.
-- 배치가 prediction을 갱신할 때 해당 행을 함께 지움.
-- 캐시가 없으면 추천 리스트를 훑을 때마다 호출이 곱절로 늘어남.

/* =============================================================================
   7. 서비스 테이블 (recommendation, rec_item)
   - 추천 스냅샷. 로그인 없음(MVP) — 사용자 식별 컬럼을 두지 않음
   ============================================================================= */
-- app_user · favorite은 로그인 삭제로 제거함(2026.09.29).
-- 즐겨찾기(관심 창업지) · 최근 추천 목록은 프론트가 브라우저 localStorage에
-- 키(rec_id · dong_code · industry_code)만 저장하고, 수치는 조회 API로 가져옴.
-- recommendation · rec_item은 로그인과 무관하게 스텝 2 → 3을 rec_id로
-- 강제하는 스냅샷이라 남김.
-- condition · rank는 PostgreSQL 예약어라 이름을 바꿨음.
-- 따옴표로 감싸서 버티는 건 나중에 반드시 사고가 남.

CREATE TABLE recommendation (
  rec_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(), -- [v1.5 변경] 순차정수 IDOR 방지
  input_condition    JSONB       NOT NULL,              -- 입력 조건 스냅샷 전체
  calculated_budgets JSONB       NOT NULL,              -- [v1.5 신설] 스텝2 자치구 25개 산출결과 스냅샷
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rec_created ON recommendation (created_at);
-- 스냅샷이 있어야 명세서 4.2.1 예산 여유 표시의 최근 조건을 재현할 수 있음.
-- 소유자가 없는 행이 계속 쌓이므로 보관 기간을 정해 created_at 기준으로 정리함
-- (보관 기간 30일, 주 1회 cron에서 created_at 기준 삭제 — 전체 명세 4.6).

COMMENT ON COLUMN recommendation.rec_id IS
  'UUIDv4. 로그인이 없어 rec_id가 유일한 접근 키. 순차 정수면 값을 하나씩 바꿔 타인의 나이·자본금 열람 가능';
COMMENT ON COLUMN recommendation.calculated_budgets IS
  '스텝 2 가용예산 산출 결과 스냅샷. 스텝 3은 재계산 없이 이것을 읽음';

CREATE TABLE rec_item (
  rec_id        UUID    NOT NULL                        -- [v1.5 변경] recommendation.rec_id (UUID) 참조
                REFERENCES recommendation (rec_id) ON DELETE CASCADE,
  rank_no       INTEGER NOT NULL,
  dong_code     CHAR(8) NOT NULL REFERENCES dong (dong_code),
  budget_margin BIGINT,                              -- 예산 여유. 음수 가능. 도봉구 등 임대료 결측은 NULL
  survival_p50  NUMERIC(6,1),
  sales_p50     BIGINT,
  PRIMARY KEY (rec_id, rank_no)
);

COMMENT ON COLUMN rec_item.budget_margin IS
  '도봉구 등 임대료 결측 자치구는 NULL. 0을 넣으면 임대료 0원 상권으로 1위가 됨';

COMMIT;
