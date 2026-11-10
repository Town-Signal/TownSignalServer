-- support_program에 지원 형태 칼럼 추가(schema.sql과 같음). 운영 DB에는 배포 전에 한 번 적용한다.
-- 가용예산에는 현금(amount_max > 0)만 더하고, 금액이 0인 공고는 ②의 other_supports 목록으로만 보여 준다.
-- 되돌리기: ALTER TABLE support_program DROP CONSTRAINT chk_program_benefit_types,
--           DROP COLUMN benefit_types;
ALTER TABLE support_program
  ADD COLUMN IF NOT EXISTS benefit_types TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE support_program
  ADD CONSTRAINT chk_program_benefit_types CHECK (benefit_types <@ ARRAY['현금','공간','교육·멘토링',
                                  '투자·판로·네트워크','융자','기타']::TEXT[]);
