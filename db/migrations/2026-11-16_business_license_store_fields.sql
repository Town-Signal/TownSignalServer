-- business_license에 점포 단위 칼럼 4개 추가(schema.sql과 같음). 운영 DB에는 배포 전에 한 번 적용한다.
-- 모델 B(영업 유지)가 행정동 × 업종 단위 피처만 쓰면 같은 칸의 점포끼리 구분하지 못한다(분석계획서 8장 ③).
-- 점포 면적 하나로 업종 내 C가 0.569 → 0.623으로 올라가고(실험 B5), 상호로 행사용 한시적 신고 · 팝업을
-- 학습 · 평가에서 빼면 확률 보정이 좋아진다(실험 B9). 네 칼럼 모두 원천 LOCALDATA CSV에 이미 있는 값이다.
-- 값은 모두 NULL 허용 — 채우는 것은 학습 코드가 한다. 빈 면적을 한 값으로 채워 적재하면
-- 폐업 처리 때 면적 기록이 비는 경향(영업 중 0.4% · 폐업 17%, 실험 B4)이 미래 정보로 샌다.
-- 되돌리기: ALTER TABLE business_license DROP CONSTRAINT chk_license_area,
--           DROP COLUMN business_name, DROP COLUMN area_sqm, DROP COLUMN address, DROP COLUMN multi_use;
ALTER TABLE business_license
  ADD COLUMN IF NOT EXISTS business_name VARCHAR(200),
  ADD COLUMN IF NOT EXISTS area_sqm      NUMERIC(10,2),
  ADD COLUMN IF NOT EXISTS address       VARCHAR(300),
  ADD COLUMN IF NOT EXISTS multi_use     BOOLEAN;
ALTER TABLE business_license
  ADD CONSTRAINT chk_license_area CHECK (area_sqm IS NULL OR area_sqm > 0);
