"""배치와 API가 함께 쓰는 상수.

여기 값이 바뀌면 배치가 만든 표와 요청 시 계산이 같이 바뀐다.
한쪽에만 상수를 두면 두 결과가 어긋나므로 반드시 이 파일을 쓴다.
"""

# 지원 유형 — 융자는 갚아야 하는 돈, 현물은 현금이 아니므로 보조금과 합산하지 않는다
SUPPORT_GRANT = "보조금"
SUPPORT_LOAN = "융자"
SUPPORT_IN_KIND = "현물"

# 선정 방식 — 경쟁 선정은 자격을 충족해도 받는다고 보장할 수 없다
SELECTION_AUTO = "자동"
SELECTION_COMPETITIVE = "경쟁"

# 순위 가중치 (설계서 7.5절 · 가정값, 팀 확정 필요)
SCORE_WEIGHTS = {
    "sales": 0.35,
    "survival": 0.30,
    "room": 0.20,
    "growth": 0.15,
}

# 초기비용에 포함할 운영 예비 개월수 (설계서 7.4절 · 가정값, 근거 자료 확보 필요)
RESERVE_MONTHS = 6
