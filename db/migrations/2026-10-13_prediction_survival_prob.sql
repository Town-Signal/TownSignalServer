-- prediction에 1 · 3년 생존 확률 칼럼 추가(schema.sql과 같음). 운영 DB에는 배포 전에 한 번 적용한다.
-- 되돌리기: ALTER TABLE prediction DROP CONSTRAINT chk_pred_survival_prob,
--           DROP COLUMN survival_prob_1y, DROP COLUMN survival_prob_3y;
ALTER TABLE prediction
  ADD COLUMN IF NOT EXISTS survival_prob_1y NUMERIC(4,3),
  ADD COLUMN IF NOT EXISTS survival_prob_3y NUMERIC(4,3);
ALTER TABLE prediction
  ADD CONSTRAINT chk_pred_survival_prob CHECK (survival_prob_3y >= 0
                                  AND survival_prob_3y <= survival_prob_1y
                                  AND survival_prob_1y <= 1);
