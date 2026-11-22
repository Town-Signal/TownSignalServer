-- business_license에 점포 좌표 2개 추가(schema.sql과 같음). 운영 DB에는 배포 전에 한 번 적용한다.
-- 점포 칼럼 4개(2026-11-16)에 이어 '위치' 축을 넣는다. 모델 B의 피처가 거의 다 행정동 × 업종 단위라
-- 같은 동이면 대로변 가게와 골목 안쪽 가게가 같은 값을 받아 업종 내 순위를 가리지 못했다(업종 내 C 0.608).
-- 좌표로 만든 반경 100m · 300m 경쟁 점포 수 · 최근접 거리 · 반경 내 1년 폐업 수를 넣으면
-- 테스트 C 0.643 → 0.661, 업종 내 0.608 → 0.628로 올라간다(실험 B11, 2026-11-22).
-- 원천 LOCALDATA CSV의 `좌표정보(X · Y)`(EPSG:5174)를 WGS84 경위도로 바꾼 값이다. 행정동 공간조인에
-- 이미 쓰고 버리던 값이라 새 원천 · 외부 지오코딩이 필요 없다(batch/pipeline/licenses.py).
-- 미터 거리가 필요한 곳에서 EPSG:5181로 다시 바꾼다(batch/ml/spatial.py).
-- 둘 다 NULL 허용 — 원천 좌표 결측이 약 8.5%다. 다만 행정동은 좌표로 공간조인해 넣으므로
-- dong_code가 있는 행은 좌표도 있다. 한쪽만 채우는 일은 없게 CHECK로 묶는다.
-- 되돌리기: ALTER TABLE business_license DROP CONSTRAINT chk_license_coord,
--           DROP COLUMN lon, DROP COLUMN lat;
ALTER TABLE business_license
  ADD COLUMN IF NOT EXISTS lon NUMERIC(9,6),
  ADD COLUMN IF NOT EXISTS lat NUMERIC(8,6);
ALTER TABLE business_license
  ADD CONSTRAINT chk_license_coord CHECK (
    (lon IS NULL) = (lat IS NULL)
    AND (lon IS NULL OR (lon BETWEEN 126.6 AND 127.3 AND lat BETWEEN 37.3 AND 37.75))
  );
