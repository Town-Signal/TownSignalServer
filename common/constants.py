"""배치와 API가 함께 쓰는 상수 (전체 명세 7.1 · 7.5 · 7.6 · 7.7 · 7.11).

여기 값이 바뀌면 배치가 만든 표와 요청 시 계산이 같이 바뀐다.
한쪽에만 상수를 두면 두 결과가 어긋나므로 반드시 이 파일을 쓴다.
"""

# 종합점수 가중치 (7.7). 값이 없는 지표는 빼고 남은 가중치를 비례 재배분한다.
# 예산 여유는 점수에 넣지 않는다(13장 판정 14).
# 2026-10-13 0.4 · 0.4 · 0.2 → 0.6 · 0.3 · 0.1. 추천 순위 백테스트(분석계획서 실험 R3 · R4)에서 기존 비중은
# 폐업 예측을 조금 얻는 대신 매출 순위 정확도가 크게 떨어졌다(0.94 → 0.79). 0.6 · 0.3 · 0.1은 매출 0.90에
# 폐업 신호를 약 90% 유지한다
SCORE_WEIGHTS = {
    "sales": 0.6,
    "survival": 0.3,
    "growth": 0.1,
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

# 업종 대분류 (1.3 · 4.4). 예전 표기(음식점 · 소매 · 서비스)는 쓰지 않는다. 순서는 화면 칩 · ⑩ 정렬 순서
INDUSTRY_CATEGORIES = ("외식업", "서비스업", "소매업")

# 표본 판정 (7.6): 최근 4개 분기 평균 점포 수가 이 값 미만이면 "표본 부족"
MIN_STORE_COUNT = 5

# 요청 경로가 읽는 prediction.model_version (5.2)
SERVING_MODEL_VERSION = "xgb_v1"

# LLM 호출 (6.6 · 판정 39). 공고 구조화 · 요약 모델은 15장 5번에서 확정하기 전까지 같은 값을 쓴다
LLM_MODEL = "claude-opus-5-5"
LLM_EFFORT = "medium"  # TODO(가정): 추출 정확도를 보고 high로 올린다(Opus 5.5 기본값은 medium)
LLM_MAX_TOKENS = 16000  # 공고 한 건의 구조화 출력은 짧다. 생각(thinking)도 이 안에서 쓴다
LLM_MAX_RETRIES = 5  # SDK가 429 · 5xx · 접속 실패를 간격을 늘려 다시 시도한다

# recommendation 보관 기간(4.6). 주 1회 run_weekly가 이보다 오래된 행을 지운다
RECOMMENDATION_RETENTION_DAYS = 30

# passed 3값 (3장 1번). 임대료가 없으면 "확인불가"이며 탈락시키지 않는다
PASSED_OK = "통과"
PASSED_EXCLUDED = "제외"
PASSED_UNKNOWN = "확인불가"

# 지원 형태 (support_program.benefit_types, 2026-11-07 팀 결정). 가용예산에는 현금(amount_max > 0)만 더하고,
# 금액이 0인 공고는 ②의 자치구별 other_supports 목록으로만 보여 준다. 값은 schema.sql CHECK와 같다
BENEFIT_CASH = "현금"
BENEFIT_TYPES = (BENEFIT_CASH, "공간", "교육·멘토링", "투자·판로·네트워크", "융자", "기타")
