"""배치와 API가 함께 쓰는 상수 (전체 명세 7.1 · 7.5 · 7.6 · 7.7 · 7.11).

여기 값이 바뀌면 배치가 만든 표와 요청 시 계산이 같이 바뀐다.
한쪽에만 상수를 두면 두 결과가 어긋나므로 반드시 이 파일을 쓴다.
"""

# 종합점수 가중치 (7.7). 값이 없는 지표는 빼고 남은 가중치를 비례 재배분한다.
# 예산 여유는 점수에 넣지 않는다(13장 판정 14).
SCORE_WEIGHTS = {
    "sales": 0.4,
    "survival": 0.4,
    "growth": 0.2,
}

# 추정 임대비용 = 보증금(월세 × 배수) + 월세 × 개월수 (7.5, 판정 4)
DEPOSIT_MULTIPLIER_DEFAULT = 15  # district_rent.deposit_multiplier 기본값과 같다
INITIAL_RENT_MONTHS = 3
RENT_PER_SQM_UNIT_WON = 1000  # district_rent.rent_per_sqm 단위는 천원/㎡ · 월

# 입력 검증 (7.1)
AGE_MIN = 15
AGE_MAX = 99
DEFAULT_AREA_SQM = 33.0
AREA_MAX_SQM = 1000.0  # 0 초과 이 값 이하 (상한은 명세상 가정)
CERTIFICATES_MAX_COUNT = 20  # 가정
CERTIFICATE_MAX_LENGTH = 50  # 가정

# 표본 판정 (7.6): 최근 4개 분기 평균 점포 수가 이 값 미만이면 "표본 부족"
MIN_STORE_COUNT = 5

# 요청 경로가 읽는 prediction.model_version (5.2)
SERVING_MODEL_VERSION = "xgb_v1"

# passed 3값 (3장 1번). 임대료가 없으면 "확인불가"이며 탈락시키지 않는다
PASSED_OK = "통과"
PASSED_EXCLUDED = "제외"
PASSED_UNKNOWN = "확인불가"
