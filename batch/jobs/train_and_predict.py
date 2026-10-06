"""모델 학습 · 전 조합 예측 · prediction 적재 — 담당: AI. 지금은 골격(인터페이스)만 있다.

실행: python -m batch.jobs.train_and_predict  (팀원 PC에서, SSH 터널로 운영 DB에 적재 — 판정 38)
주기: 상권 원천의 기준 분기가 바뀔 때. EC2 cron에서 돌리지 않는다. 명세 6.2 ~ 6.5 · 4.6.

입력 표: sales_quarterly · store_quarterly · population_quarterly · business_license · district_rent
출력 표: prediction (행정동 × 업종, model_version별)

적재는 반드시 batch.pipeline.swap.replace_predictions로 한다. 그 함수가 적재 직전에 fill_scores
(common.scoring)로 백분위 · total_score · score_rank를 채우고(판정 30), 사라진 조합 삭제와
summary_cache 정리를 같은 트랜잭션에서 한다. 학습 라이브러리(xgboost · scikit-survival)는 이 모듈의
함수 안에서 import한다(CI는 배치 의존성을 설치하지 않는다).
"""

import logging
from typing import Any

from sqlalchemy import Connection

logger = logging.getLogger(__name__)


def load_training_frames(conn: Connection) -> dict[str, Any]:
    """학습 · 예측에 쓸 표들을 읽어 데이터프레임 묶음으로 돌려준다(변수정의서 · 6.2 피처)."""
    raise NotImplementedError("학습 데이터 준비는 AI 담당이 구현한다(6.2 · 6.3)")


def train_sales_model(frames: dict[str, Any]) -> Any:
    """모델 A — 점포당 분기 매출 XGBoost 분위수 회귀(p10 · p50 · p90), 시간 분할(판정 37). 6.2."""
    raise NotImplementedError("모델 A 학습은 AI 담당이 구현한다(6.2)")


def train_survival_model(frames: dict[str, Any]) -> Any:
    """모델 B — 생존기간 XGBoost AFT(개월 p10 · p50 · p90), 2021년 이후 개업 표본. 6.3."""
    raise NotImplementedError("모델 B 학습은 AI 담당이 구현한다(6.3)")


def compute_growth(conn: Connection) -> dict[tuple[str, str], tuple[float | None, str | None]]:
    """성장세(growth_rate · growth_confidence)를 (dong_code, industry_code)별로 선계산한다. 6.4."""
    raise NotImplementedError("성장세 계산은 AI 담당이 구현한다(6.4)")


def predict_all(models: dict[str, Any], frames: dict[str, Any], model_version: str) -> list[dict[str, Any]]:
    """전 조합 예측 → prediction 행(dict) 목록. 6.5.

    행 칼럼: dong_code · industry_code · model_version · sales_p10/p50/p90(원, 분기) ·
    survival_p10/p50/p90(개월, 소수 1자리) · growth_rate · growth_confidence.
    매출 기록이 전혀 없는 조합은 행을 만들지 않는다. 분위수는 p10 ≤ p50 ≤ p90이 되게 정렬한다(CHECK).
    점수 칼럼은 채우지 않는다 — replace_predictions가 채운다.
    """
    raise NotImplementedError("전 조합 예측은 AI 담당이 구현한다(6.5)")


def main() -> None:
    """load_training_frames → 학습 → predict_all → replace_predictions(conn, rows, model_version)."""
    raise NotImplementedError("학습 · 예측 · 적재는 아직 구현 전이다(6.2 ~ 6.5)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
