"""모델 학습 · 전 조합 예측 · prediction 적재 — 담당: AI. 모델 A(매출) · 모델 B(생존) · 성장세 구현.

실행: python -m batch.jobs.train_and_predict [--write] [--model-version xgb_v1] [--model-dir models]
      팀원 PC에서, SSH 터널로 운영 DB에 적재 — 판정 38
주기: 상권 원천의 기준 분기가 바뀔 때. EC2 cron에서 돌리지 않는다. 명세 6.2 ~ 6.5 · 4.6.

입력 표: sales_quarterly · store_quarterly · population_quarterly · district_rent (business_license는 모델 B)
출력 표: prediction (행정동 × 업종, model_version별)

--write 없이 돌리면 학습 · 평가 · 모델 파일 저장까지만 하고 DB는 건드리지 않는다.
적재는 반드시 batch.pipeline.swap.replace_predictions로 한다. 그 함수가 적재 직전에 fill_scores
(common.scoring)로 백분위 · total_score · score_rank를 채우고(판정 30), 사라진 조합 삭제와
summary_cache 정리를 같은 트랜잭션에서 한다. 학습 라이브러리(pandas · xgboost · scikit-learn)는 함수 안에서
import한다(CI는 배치 의존성을 설치하지 않는다).
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine

from batch import config
from common.constants import SERVING_MODEL_VERSION

logger = logging.getLogger(__name__)

# TODO(가정): 모델 파일 · 지표 저장 폴더. 팀 드라이브로 옮기는 것은 사람이 한다(배포 v0.4 1.3)
DEFAULT_MODEL_DIR = os.getenv("MODEL_DIR", "models")

_QUERIES = {
    "sales": "SELECT dong_code, industry_code, year_quarter, amount FROM sales_quarterly",
    "stores": (
        "SELECT dong_code, industry_code, year_quarter, store_count, franchise_count, open_count, "
        "close_count FROM store_quarterly"
    ),
    "population": (
        "SELECT dong_code, year_quarter, total, male, age_10, age_20, age_30, age_40, age_50, age_60 "
        "FROM population_quarterly"
    ),
    "rent": "SELECT district_code, rent_per_sqm FROM district_rent",
    # 모델 B — 2021년 이후 개업 · 행정동 · 업종 매핑 성공 점포만(6.3).
    # 관측 종료일은 전체 인허가의 마지막 날짜로 잡는다
    "licenses": (
        "SELECT dong_code, industry_code, open_date, close_date FROM business_license "
        "WHERE open_date >= DATE '2021-01-01' AND dong_code IS NOT NULL AND industry_code IS NOT NULL"
    ),
    "license_dates": "SELECT max(open_date) AS max_open, max(close_date) AS max_close FROM business_license",
}


def load_training_frames(conn: Connection) -> dict[str, Any]:
    """학습 · 예측에 쓸 표들을 읽어 데이터프레임 묶음으로 돌려준다(변수정의서 3.2 피처).

    출력: {"sales", "stores", "population", "rent"} → DataFrame. CHAR 칼럼의 공백은 지운다.
    """
    import pandas as pd
    from sqlalchemy import text

    frames = {}
    for name, sql in _QUERIES.items():
        df = pd.read_sql(text(sql), conn)
        for c in ("dong_code", "industry_code", "year_quarter", "district_code"):
            if c in df:
                df[c] = df[c].str.strip()
        frames[name] = df
    return frames


def _panel(frames: dict[str, Any]) -> Any:
    """모델 A · B가 같이 쓰는 행정동 × 업종 × 분기 패널. 한 번만 만들어 frames에 둔다."""
    from batch.ml import sales

    if "panel" not in frames:
        frames["panel"] = sales.build_panel(
            frames["sales"], frames["stores"], frames["population"], frames["rent"]
        )
    return frames["panel"]


def _obs_end(frames: dict[str, Any]) -> Any:
    """관측 종료일 = 인허가 원천의 마지막 개업 · 폐업일(추출일 근사, 변수정의서 4.1). 없으면 None."""
    dates = frames.get("license_dates")
    if dates is None or dates.empty:
        return None
    values = [v for v in (dates.iloc[0]["max_open"], dates.iloc[0]["max_close"]) if v is not None]
    return max(values) if values else None


def train_sales_model(frames: dict[str, Any]) -> Any:
    """모델 A — 점포당 분기 매출 XGBoost 분위수 회귀(p10 · p50 · p90), 시간 분할(판정 37). 6.2.

    출력: batch.ml.sales.SalesModel(재학습된 분위수 모델 3개, CQR 보정 폭, 테스트 지표).
    """
    from batch.ml import sales

    return sales.train(_panel(frames))


def train_survival_model(frames: dict[str, Any]) -> Any:
    """모델 B — 생존기간 XGBoost AFT(개월 p10 · p50 · p90), 2021년 이후 개업 표본. 6.3.

    출력: batch.ml.survival.SurvivalModel(적용 업종 목록, 선택한 분포 · 폭, 테스트 지표).
    인허가 행이 없으면 NotImplementedError — 그 경우 생존 칼럼을 NULL로 두고 진행한다.
    """
    from batch.ml import survival

    licenses = frames.get("licenses")
    if licenses is None or licenses.empty:
        raise NotImplementedError("business_license가 비어 있어 모델 B를 학습할 수 없다")
    return survival.train(licenses, _panel(frames), obs_end=_obs_end(frames))


# growth_rate는 NUMERIC(6,3)이라 절댓값 1000 이상은 들어가지 않는다. 직전 구간 매출이 아주 작아 비율이 터지는
# 조합은 상한으로 자른다 — 백분위(순위)에는 '가장 크게 오름'으로 똑같이 반영된다
GROWTH_RATE_LIMIT = 999.999


def compute_growth(sales: Any) -> dict[tuple[str, str], tuple[float | None, str | None]]:
    """성장세(growth_rate · growth_confidence)를 (dong_code, industry_code)별로 선계산한다. 6.4.

    입력: sales_quarterly 데이터프레임(dong_code · industry_code · year_quarter · amount).
    매출 **합계**(amount)의 최근 4분기 ÷ 직전 4분기 − 1. 2024Q1 이후 분기만 쓰고, 분기 수에 따른 구간 길이 ·
    신뢰도와 빠진 분기 처리는 common.growth.period_change를 따른다. 최신 분기는 데이터 전체의 최신 분기다.
    출력: {(dong_code, industry_code): (growth_rate 소수 3자리 또는 None, '높음' · '낮음' 또는 None)}.
    """
    from common.growth import period_change

    if sales.empty:
        return {}
    latest = str(sales["year_quarter"].max())
    out: dict[tuple[str, str], tuple[float | None, str | None]] = {}
    for (dong, industry), g in sales.groupby(["dong_code", "industry_code"], sort=False):
        pairs = zip(g["year_quarter"], g["amount"], strict=True)
        values = {str(q): (None if a is None else float(a)) for q, a in pairs}
        change = period_change(values, latest)
        rate = None
        if change.rate is not None:
            rate = round(max(-GROWTH_RATE_LIMIT, min(GROWTH_RATE_LIMIT, change.rate)), 3)
        out[(str(dong), str(industry))] = (rate, change.confidence if rate is not None else None)
    return out


def predict_all(models: dict[str, Any], frames: dict[str, Any], model_version: str) -> list[dict[str, Any]]:
    """전 조합 예측 → prediction 행(dict) 목록. 6.5.

    입력: models = {"sales": SalesModel, "survival": 없으면 생략}, frames = load_training_frames 결과
          (+ 선택 "growth": compute_growth 결과)
    행 칼럼: dong_code · industry_code · model_version · sales_p10/p50/p90(원, 분기) ·
    survival_p10/p50/p90(개월, 소수 1자리) · growth_rate · growth_confidence.
    매출 기록이 전혀 없는 조합은 행을 만들지 않는다. 분위수는 p10 ≤ p50 ≤ p90이 되게 정렬한다(CHECK).
    모델 B · 성장세가 없으면 그 칼럼은 NULL이다. 점수 칼럼은 채우지 않는다 — replace_predictions가 채운다.
    """
    from batch.ml import sales

    panel = _panel(frames)
    predicted = sales.predict_next(models["sales"], panel)
    growth = frames.get("growth") or {}
    surv: dict[tuple[str, str], tuple[float, float, float]] = {}
    if models.get("survival") is not None:
        from batch.ml import survival

        for r in survival.predict_next(models["survival"], panel).itertuples(index=False):
            surv[(r.dong_code, r.industry_code)] = (r.survival_p10, r.survival_p50, r.survival_p90)

    rows = []
    for r in predicted.itertuples(index=False):
        rate, confidence = growth.get((r.dong_code, r.industry_code), (None, None))
        s10, s50, s90 = surv.get((r.dong_code, r.industry_code), (None, None, None))
        rows.append(
            {
                "dong_code": r.dong_code,
                "industry_code": r.industry_code,
                "model_version": model_version,
                "survival_p10": None if s10 is None else float(s10),
                "survival_p50": None if s50 is None else float(s50),
                "survival_p90": None if s90 is None else float(s90),
                "sales_p10": int(r.sales_p10),
                "sales_p50": int(r.sales_p50),
                "sales_p90": int(r.sales_p90),
                "growth_rate": rate,
                "growth_confidence": confidence,
            }
        )
    return rows


def _commit_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _save_artifacts(model: Any, model_version: str, model_dir: str) -> Path:
    """모델 파일과 metrics.json을 model_dir/model_version/에 남긴다(배포 v0.4 1.3 — 커밋 SHA 포함)."""
    from batch.ml import sales

    out = Path(model_dir) / model_version
    files = sales.save(model, out)
    report = {
        "model_version": model_version,
        "commit": _commit_sha(),
        "predict_quarter": model.predict_quarter,
        "files": [Path(f).name for f in files],
        **model.metrics,
    }
    (out / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _save_survival(model: Any, model_version: str, model_dir: str) -> None:
    """모델 B 파일을 같은 model_version 폴더에 두고, metrics.json에 survival 항목을 더한다."""
    from batch.ml import survival

    out = Path(model_dir) / model_version
    survival.save(model, out)
    metrics_file = out / "metrics.json"
    report = json.loads(metrics_file.read_text(encoding="utf-8")) if metrics_file.exists() else {}
    report["survival"] = {
        "file": "survival_aft.json",
        "distribution": model.distribution,
        "scale": model.scale,
        **model.metrics,
    }
    metrics_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run(conn: Connection, model_version: str, model_dir: str, write: bool) -> dict[str, Any]:
    """load_training_frames → 모델 A 학습 · 평가 · 저장 → predict_all → (write면) replace_predictions."""
    frames = load_training_frames(conn)
    conn.commit()  # 읽기 트랜잭션을 닫는다 — 적재는 새 트랜잭션에서 한다

    sales_model = train_sales_model(frames)
    saved = _save_artifacts(sales_model, model_version, model_dir)
    logger.info("모델 A 저장: %s", saved)
    logger.info(
        "모델 A 테스트(보정 후): %s", json.dumps(sales_model.metrics["test_calibrated"], ensure_ascii=False)
    )

    models: dict[str, Any] = {"sales": sales_model}
    for name, step in (
        ("모델 B", lambda: train_survival_model(frames)),
        ("성장세", lambda: compute_growth(frames["sales"])),
    ):
        try:
            value = step()
        except NotImplementedError as exc:
            logger.warning("%s 미구현 — 해당 칼럼은 NULL로 둔다: %s", name, exc)
            continue
        if name == "모델 B":
            models["survival"] = value
            _save_survival(value, model_version, model_dir)
            logger.info("모델 B 테스트: %s", json.dumps(value.metrics["test"], ensure_ascii=False))
        else:
            frames["growth"] = value

    rows = predict_all(models, frames, model_version)
    summary = {"predict_quarter": sales_model.predict_quarter, "rows": len(rows), "written": False}
    if write:
        from batch.pipeline.swap import replace_predictions

        result = replace_predictions(conn, rows, model_version)
        conn.commit()
        summary.update(
            written=True,
            upserted=result.upserted,
            deleted=result.deleted,
            summaries_deleted=result.summaries_deleted,
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="모델 학습 · 전 조합 예측 · prediction 적재")
    parser.add_argument("--model-version", default=SERVING_MODEL_VERSION)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--write", action="store_true", help="prediction 표에 적재한다(없으면 학습 · 평가 · 저장만)"
    )
    args = parser.parse_args(argv)

    engine = create_engine(config.DATABASE_URL)
    with engine.connect() as conn:
        summary = run(conn, args.model_version, args.model_dir, args.write)
    logger.info("완료: %s", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
