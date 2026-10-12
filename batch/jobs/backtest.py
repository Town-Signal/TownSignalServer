"""추천 순위 백테스트 — 담당: AI.

과거 시점까지의 데이터로 만든 순위가 그 뒤 실제로 맞았는지 잰다(분석계획서 6.1 · R1).

실행: python -m batch.jobs.backtest [--cutoff 20244 --cutoff 20252] [--horizon 2] [--model-dir models]
      팀원 PC에서. DB는 읽기만 한다(prediction을 건드리지 않는다).
주기: 모델을 다시 학습할 때 함께. 결과는 model_dir/backtest.json에 남겨 분석계획서 실험 기록(R1)에 옮긴다.

입력 표: sales_quarterly · store_quarterly · population_quarterly · district_rent (train_and_predict와 같다)
--cutoff를 여러 번 주면 시점마다 따로 돌린다. 안 주면 '데이터 마지막 분기 − horizon' 한 번.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine

from batch import config
from batch.jobs.train_and_predict import DEFAULT_MODEL_DIR, _commit_sha, load_training_frames

logger = logging.getLogger(__name__)

HORIZON = 2  # batch.ml.backtest.HORIZON과 같다. 학습 라이브러리 없이도 --help가 뜨게 여기 둔다


def run(conn: Connection, cutoffs: list[str] | None, horizon: int, model_dir: str) -> dict[str, Any]:
    """load_training_frames → 시점마다 backtest.run → model_dir/backtest.json."""
    from batch.ml import backtest

    frames = load_training_frames(conn)
    conn.commit()

    results = [backtest.run(frames, cutoff, horizon) for cutoff in (cutoffs or [None])]
    report = {"commit": _commit_sha(), "results": results}

    out = Path(model_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        m, n = r["model"]["sales"], r["naive"]["sales"]
        logger.info(
            "기준 %s → %s: 순위 상관 모델 %.3f / naive %.3f, "
            "상위 20%% 적중 모델 %.3f / naive %.3f (업종 %d개)",
            r["cutoff"],
            ",".join(r["outcome_quarters"]),
            m.get("spearman", float("nan")),
            n.get("spearman", float("nan")),
            m.get("precision_top", float("nan")),
            n.get("precision_top", float("nan")),
            m.get("industries", 0),
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="추천 순위 백테스트(분석계획서 6.1)")
    parser.add_argument("--cutoff", action="append", help="기준 분기(예: 20252). 여러 번 줄 수 있다")
    parser.add_argument("--horizon", type=int, default=HORIZON, help="기준 이후 몇 개 분기의 실제 값을 볼지")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    args = parser.parse_args(argv)

    engine = create_engine(config.DATABASE_URL)
    with engine.connect() as conn:
        run(conn, args.cutoff, args.horizon, args.model_dir)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
