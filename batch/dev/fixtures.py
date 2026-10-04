"""더미 데이터 생성 — 실제 데이터 아님 (CLAUDE.md 5절).

data/dev/dummy_*.json(손으로 정한 마스터 · 지원사업 · 필수 경우)을 읽고, 매출 · 점포 · 인구 · 예측 원값은
random.Random("townsignal-dev:…")로 결정적으로 생성한다(실행할 때마다 같은 값).
백분위 · total_score · score_rank는 손으로 넣지 않고 batch.pipeline.scores(= common.scoring)가 계산한다.

DB에 의존하지 않는다. build()가 표 이름 → 행(dict) 목록을 돌려주고,
적재는 batch.jobs.load_dev_fixtures가 한다.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from batch.pipeline.scores import fill_scores

DEV_DIR = Path(__file__).resolve().parents[2] / "data" / "dev"
SEED = "townsignal-dev"

# 업종별 기준값(더미): 동 평균 점포 수, 점포당 분기 매출(원)
_INDUSTRY_BASE = {
    "CS100001": (30, 90_000_000),
    "CS100010": (25, 45_000_000),
    "CS100008": (12, 40_000_000),
    "CS200001": (15, 35_000_000),
    "CS200002": (10, 50_000_000),
    "CS300001": (10, 60_000_000),
    "CS300002": (14, 55_000_000),
}
_AGE_WEIGHTS = (0.10, 0.22, 0.20, 0.18, 0.16, 0.14)  # 10 · 20 · 30 · 40 · 50 · 60대 이상
_TIME_WEIGHTS = (0.08, 0.16, 0.20, 0.18, 0.24, 0.14)  # 00-06 · 06-11 · 11-14 · 14-17 · 17-21 · 21-24
_TIME_COLUMNS = ("time_00_06", "time_06_11", "time_11_14", "time_14_17", "time_17_21", "time_21_24")
_AGE_COLUMNS = ("age_10", "age_20", "age_30", "age_40", "age_50", "age_60")
_EXTRACTED_AT = datetime.fromisoformat("2026-10-01T09:00:00+09:00")


def _rng(*parts: str) -> random.Random:
    return random.Random(":".join((SEED, *parts)))


def _load(name: str) -> dict[str, Any]:
    return json.loads((DEV_DIR / name).read_text(encoding="utf-8"))


def _round_to(value: Decimal | float, places: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _thousand(value: float | Decimal) -> int:
    return int(_round_to(Decimal(str(value)) / 1000, "1")) * 1000


@dataclass
class Fixtures:
    """표 이름 → 행 목록. support_program 행의 '_key'와 program_verification 행의 '_program_key'는
    적재 때 program_id로 바꾸는 연결 키라 DB 칼럼이 아니다."""

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    cases: dict[str, Any] = field(default_factory=dict)

    def count(self, table: str) -> int:
        return len(self.tables.get(table, []))


def _masters(fx: Fixtures) -> None:
    fx.tables["district"] = [
        {k: d[k] for k in ("district_code", "name", "geo_code")}
        for d in _load("dummy_districts.json")["districts"]
    ]
    fx.tables["dong"] = [
        {k: d[k] for k in ("dong_code", "name", "district_code", "geo_code")}
        for d in _load("dummy_dongs.json")["dongs"]
    ]
    fx.tables["industry"] = [dict(i) for i in _load("dummy_industries.json")["industries"]]


def _rent(fx: Fixtures) -> None:
    data = _load("dummy_rent.json")
    base_quarter = data["base_quarter"]
    names = {d["district_code"]: d["name"] for d in fx.tables["district"]}
    markets, rents = [], []
    for district_code, values in data["markets_per_district"].items():
        for i, value in enumerate(values, 1):
            markets.append(
                {
                    "market_name": f"[더미] {names[district_code]} 상권 {i}",
                    "base_quarter": base_quarter,
                    "district_code": district_code,
                    "rent_per_sqm": _round_to(value, "0.01"),
                }
            )
        count = len(values)
        rents.append(
            {
                "district_code": district_code,
                "base_quarter": base_quarter,
                # 상권 평균. 상권이 없으면 NULL — 0으로 채우지 않는다
                "rent_per_sqm": _round_to(sum(values) / count, "0.01") if count else None,
                "deposit_multiplier": Decimal("15.00"),
                "market_count": count,
                "confidence": "높음" if count >= 2 else "낮음" if count == 1 else "없음",
            }
        )
    fx.tables["commercial_market"] = markets
    fx.tables["district_rent"] = rents


def _programs(fx: Fixtures) -> None:
    data = _load("dummy_support_programs.json")
    fx.tables["support_program"] = [
        {
            "_key": p["key"],
            "name": p["name"],
            "agency": p["agency"],
            "amount_max": p["amount_max"],
            "district_code": p["district_code"],
            "is_exclusive": p["is_exclusive"],
            "apply_start": date.fromisoformat(p["apply_start"]) if p["apply_start"] else None,
            "apply_end": date.fromisoformat(p["apply_end"]) if p["apply_end"] else None,
            "eligibility": p["eligibility"],
            "source_url": f"https://example.com/townsignal-dummy/programs/{p['key']}",
            "raw_text": f"[더미] 공고 원문 없음 — {p['case']}",
            "extracted_at": _EXTRACTED_AT,
            "verified_by": p["verified_by"],
        }
        for p in data["programs"]
    ]
    fx.tables["program_verification"] = [
        {
            "_program_key": v["program"],
            "field_name": v["field_name"],
            "extracted_value": v["extracted_value"],
            "actual_value": v["actual_value"],
            "is_match": v["is_match"],
            "checked_by": "더미검수자",
        }
        for v in data["verifications"]
    ]


def _indicators(fx: Fixtures) -> None:
    cases = fx.cases
    quarters = cases["quarters"]
    no_store = set(cases["no_store_dongs"]["dongs"])
    no_sales_dongs = set(cases["no_prediction_dongs"]["dongs"])
    no_sales_combos = {tuple(c) for c in cases["no_prediction_combos"]["combos"]}
    short_dongs = set(cases["sample_short_dongs"]["dongs"])
    short_combos = {tuple(c) for c in cases["sample_short_combos"]["combos"]}

    stores, sales, population = [], [], []
    for dong in fx.tables["dong"]:
        code = dong["dong_code"]
        population.extend(_population(code, quarters))
        if code in no_store:
            continue
        for industry in fx.tables["industry"]:
            ind = industry["industry_code"]
            short = code in short_dongs or (code, ind) in short_combos
            counts = _store_counts(code, ind, quarters, short)
            stores.extend(
                {"dong_code": code, "industry_code": ind, "year_quarter": q, "store_count": n}
                for q, n in zip(quarters, counts, strict=True)
            )
            if code in no_sales_dongs or (code, ind) in no_sales_combos:
                continue
            sales.extend(_sales(code, ind, quarters, counts))
    fx.tables["store_quarterly"] = stores
    fx.tables["sales_quarterly"] = sales
    fx.tables["population_quarterly"] = population


def _store_counts(dong: str, industry: str, quarters: list[str], short: bool) -> list[int]:
    rng = _rng("stores", dong, industry)
    if short:  # 표본 부족: 분기마다 1~4개
        return [rng.randint(1, 4) for _ in quarters]
    base = _INDUSTRY_BASE[industry][0] * rng.uniform(0.6, 1.6)
    return [max(5, round(base * rng.uniform(0.9, 1.1))) for _ in quarters]


def _sales(dong: str, industry: str, quarters: list[str], counts: list[int]) -> list[dict[str, Any]]:
    rng = _rng("sales", dong, industry)
    per_store = _INDUSTRY_BASE[industry][1] * rng.uniform(0.7, 1.5)
    trend = rng.uniform(-0.02, 0.04)  # 분기당 증감
    rows = []
    for i, (quarter, count) in enumerate(zip(quarters, counts, strict=True)):
        amount = round(per_store * (1 + trend * (i - len(quarters) + 1)) * rng.uniform(0.95, 1.05)) * count
        rows.append(
            {
                "dong_code": dong,
                "industry_code": industry,
                "year_quarter": quarter,
                "amount": amount,
                "txn_count": amount // 12_000,
            }
        )
    return rows


def _population(dong: str, quarters: list[str]) -> list[dict[str, Any]]:
    rng = _rng("population", dong)
    base = rng.uniform(20_000, 90_000)
    male_share = rng.uniform(0.45, 0.55)
    rows = []
    for quarter in quarters:
        total = round(base * rng.uniform(0.95, 1.05))
        row = {"dong_code": dong, "year_quarter": quarter, "total": total}
        row["male"] = round(total * male_share)
        row["female"] = total - row["male"]
        for columns, weights in ((_AGE_COLUMNS, _AGE_WEIGHTS), (_TIME_COLUMNS, _TIME_WEIGHTS)):
            for column, weight in zip(columns, weights, strict=True):
                row[column] = round(total * weight * rng.uniform(0.9, 1.1))
        rows.append(row)
    return rows


def _predictions(fx: Fixtures) -> None:
    cases = fx.cases
    growth_null_ind = set(cases["growth_null_industries"]["industries"])
    growth_null_dongs = set(cases["growth_null_dongs"]["dongs"])
    growth_low = set(cases["growth_low_dongs"]["dongs"])
    extra = cases["extra_model"]

    # 예측은 매출 기록이 있는 조합에만 만든다(6.5). 점포당 매출 = amount ÷ store_count, 최근 4분기 평균
    stores = {(r["dong_code"], r["industry_code"], r["year_quarter"]): r["store_count"]
              for r in fx.tables["store_quarterly"]}
    recent = cases["quarters"][-4:]
    per_store: dict[tuple[str, str], list[float]] = {}
    for r in fx.tables["sales_quarterly"]:
        if r["year_quarter"] in recent:
            key = (r["dong_code"], r["industry_code"])
            per_store.setdefault(key, []).append(r["amount"] / stores[(*key, r["year_quarter"])])

    rows = []
    for (dong, ind), values in per_store.items():
        p50 = sum(values) / len(values)
        rng = _rng("prediction", dong, ind)
        survival = _round_to(rng.uniform(20, 70), "0.1")
        has_growth = ind not in growth_null_ind and dong not in growth_null_dongs
        growth = _round_to(rng.uniform(-0.10, 0.20), "0.001") if has_growth else None
        base = {
            "dong_code": dong,
            "industry_code": ind,
            "growth_rate": growth,
            "growth_confidence": None if growth is None else ("낮음" if dong in growth_low else "높음"),
        }
        rows.append({**base, "model_version": cases["serving_model_version"], **_intervals(p50, survival)})
        if ind in extra["industries"]:  # 서빙하지 않는 비교 모델(mlp_v1)
            mlp = _rng("prediction-mlp", dong, ind)
            rows.append(
                {
                    **base,
                    "model_version": extra["model_version"],
                    **_intervals(p50 * mlp.uniform(0.9, 1.1), _round_to(mlp.uniform(20, 70), "0.1")),
                }
            )
    fx.tables["prediction"] = fill_scores(rows)


def _intervals(sales_p50: float, survival_p50: Decimal) -> dict[str, Any]:
    """80% 예측구간(p10 · p50 · p90). 최소 · 최대가 아니다."""
    p50 = _thousand(sales_p50)
    return {
        "sales_p10": _thousand(p50 * 0.7),
        "sales_p50": p50,
        "sales_p90": _thousand(p50 * 1.35),
        "survival_p10": _round_to(survival_p50 * Decimal("0.6"), "0.1"),
        "survival_p50": survival_p50,
        "survival_p90": _round_to(survival_p50 * Decimal("1.5"), "0.1"),
    }


def _summaries(fx: Fixtures) -> None:
    cases = fx.cases
    industries = set(cases["summary_industries"]["industries"])
    missing = set(cases["summary_missing_dongs"]["dongs"])
    names = {d["dong_code"]: d["name"] for d in fx.tables["dong"]}
    industry_names = {i["industry_code"]: i["name"] for i in fx.tables["industry"]}
    fx.tables["summary_cache"] = [
        {
            "dong_code": p["dong_code"],
            "industry_code": p["industry_code"],
            "summary_text": f"[더미 요약] {names[p['dong_code']]} {industry_names[p['industry_code']]} — "
            "실제 분석 문장이 아닙니다.",
            "model_name": "dummy",
        }
        for p in fx.tables["prediction"]
        if p["model_version"] == cases["serving_model_version"]
        and p["industry_code"] in industries
        and p["dong_code"] not in missing
    ]


def build() -> Fixtures:
    fx = Fixtures(cases=_load("dummy_special_cases.json"))
    _masters(fx)
    _rent(fx)
    _programs(fx)
    _indicators(fx)
    _predictions(fx)
    _summaries(fx)
    return fx
