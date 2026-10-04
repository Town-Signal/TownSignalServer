"""더미 데이터 생성 결과 검사 (DB 없음). CLAUDE.md 5절의 필수 경우가 계획한 행에 있는지 본다."""

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from batch.dev.fixtures import DEV_DIR, build
from common.budget import match_district, rent_cost
from common.evaluator import Applicant, Program
from common.scoring import percent_rank, total_score

DOBONG, GEUMCHEON, GANGBUK, GWANAK, MAPO, SEONGDONG, GANGNAM, JONGNO = (
    "11320", "11545", "11305", "11620", "11440", "11200", "11680", "11110",
)
CHEONGUN, HOEGI, BANGHAK3, GONGNEUNG2, MANGWON1, GASAN = (
    "11110901", "11230901", "11320901", "11350901", "11440903", "11545901",
)
NO_PRED_COMBOS = {("11530901", "CS200002"), ("11260902", "CS300001"), ("11380901", "CS100008")}

EXPECTED_COUNTS = {
    "district": 25,
    "dong": 54,
    "industry": 7,
    "commercial_market": 46,
    "district_rent": 25,
    "support_program": 11,
    "program_verification": 8,
    "store_quarterly": 2_968,
    "sales_quarterly": 2_888,
    "population_quarterly": 432,
    "prediction": 413,
    "summary_cache": 50,
}


@pytest.fixture(scope="module")
def fx():
    return build()


@pytest.fixture(scope="module")
def t(fx):
    return fx.tables


def _programs(t) -> list[Program]:
    return [
        Program(
            program_id=i,
            name=r["name"],
            amount_max=r["amount_max"],
            eligibility=r["eligibility"],
            is_exclusive=r["is_exclusive"],
            district_code=r["district_code"],
            verified_by=r["verified_by"],
            apply_end=r["apply_end"],
        )
        for i, r in enumerate(t["support_program"], 1)
    ]


def _xgb(t):
    return [p for p in t["prediction"] if p["model_version"] == "xgb_v1"]


# ── 1 · 2 마스터 ─────────────────────────────────────────────


def test_row_counts_match_plan(t):
    assert {name: len(rows) for name, rows in t.items()} == EXPECTED_COUNTS


def test_districts_and_dongs(t):
    districts = t["district"]
    assert len({d["district_code"] for d in districts}) == 25
    assert all(d["geo_code"] for d in districts)
    codes = {d["district_code"] for d in districts}
    per_district = {}
    for dong in t["dong"]:
        assert dong["dong_code"][:5] == dong["district_code"] in codes
        assert dong["dong_code"][5] == "9"  # 더미 표식
        per_district.setdefault(dong["district_code"], []).append(dong)
    assert all(2 <= len(v) <= 3 for v in per_district.values()) and len(per_district) == 25
    assert sorted(d["district_code"] for d in t["dong"] if d["name"] == "신사동") == [GWANAK, GANGNAM]
    assert [d["geo_code"] for d in t["dong"] if d["name"] == "위례동"] == [None]
    assert sum(d["geo_code"] is None for d in t["dong"]) == 1


def test_industries(t):
    by_category = {}
    for ind in t["industry"]:
        assert re.fullmatch(r"CS\d{6}", ind["industry_code"])
        by_category.setdefault(ind["category"], []).append(ind)
    assert set(by_category) == {"외식업", "서비스업", "소매업"}
    assert all(2 <= len(v) <= 3 for v in by_category.values())


# ── 3 임대료 ─────────────────────────────────────────────────


def test_rent_cases(t):
    rent = {r["district_code"]: r for r in t["district_rent"]}
    markets = {}
    for m in t["commercial_market"]:
        markets.setdefault(m["district_code"], []).append(m)

    assert (rent[DOBONG]["rent_per_sqm"], rent[DOBONG]["confidence"], rent[DOBONG]["market_count"]) == (
        None, "없음", 0,
    )
    assert DOBONG not in markets
    for code in (GEUMCHEON, GANGBUK):
        assert (rent[code]["confidence"], rent[code]["market_count"], len(markets[code])) == ("낮음", 1, 1)
    others = set(rent) - {DOBONG, GEUMCHEON, GANGBUK}
    assert len(others) == 22
    assert all(rent[c]["confidence"] == "높음" and rent[c]["market_count"] >= 2 for c in others)
    for code, rows in markets.items():  # 자치구 값 = 상권 평균
        mean = sum(m["rent_per_sqm"] for m in rows) / len(rows)
        assert rent[code]["rent_per_sqm"] == mean.quantize(Decimal("0.01"))


def test_gwanak_rent_gives_spec_regression_value(t):
    gwanak = next(r for r in t["district_rent"] if r["district_code"] == GWANAK)
    assert gwanak["rent_per_sqm"] == Decimal("28.50")
    cost = rent_cost(gwanak["rent_per_sqm"], 33, gwanak["deposit_multiplier"])
    assert cost.estimated_rent_cost == 16_929_000


# ── 4 · 5 지원사업 ───────────────────────────────────────────


def test_program_cases(t):
    rows = t["support_program"]
    national_exclusive = [
        r for r in rows if r["district_code"] is None and r["is_exclusive"] and r["verified_by"]
    ]
    assert len(national_exclusive) == 2
    assert len({r["amount_max"] for r in national_exclusive}) == 2
    assert any(r["district_code"] is None and not r["is_exclusive"] for r in rows)
    district_only = {r["district_code"] for r in rows if r["district_code"]}
    assert district_only == {GWANAK, MAPO, GEUMCHEON, SEONGDONG, GANGNAM}
    assert sum(r["verified_by"] is None for r in rows) == 1
    assert sum(r["apply_end"] is None for r in rows) == 1
    assert any(r["apply_end"] is not None and r["apply_end"].year == 2025 for r in rows)  # 마감 공고
    assert all(r["name"].startswith("[더미") for r in rows)


YOUTH = Applicant(age=27, certificates=("조리기능사",), industry_code="CS100001", industry_category="외식업")


@pytest.mark.parametrize(
    ("district", "expected"),
    [
        (JONGNO, 24_000_000),  # A 2,000 + 역량 300 + 바우처 100
        (GWANAK, 32_000_000),  # + 관악 800
        (MAPO, 29_000_000),  # 마포 배타 2,500 + 300 + 100 (A · B 제외)
        (GEUMCHEON, 33_000_000),  # + 금천 외식업 900
        (SEONGDONG, 30_000_000),  # + 성동 600
        (GANGNAM, 24_000_000),  # 강남 배타 2,000은 A와 동률 → A 선택
        (DOBONG, 24_000_000),
    ],
)
def test_reference_applicant_support_by_district(t, district, expected):
    assert match_district(_programs(t), YOUTH, district).support_fund_max == expected


def test_exclusion_reasons(t):
    programs = _programs(t)
    jongno = match_district(programs, YOUTH, JONGNO)
    assert [(e.program.program_id, e.reason) for e in jongno.excluded] == [
        (2, "더 큰 지원금인 [더미] 청년창업 정착지원금 A(2,000만 원)와 중복 수혜 불가")
    ]
    mapo = match_district(programs, YOUTH, MAPO)
    assert sorted(e.program.program_id for e in mapo.excluded) == [1, 2]
    assert all("마포구 청년 가게 지원(2,500만 원)" in e.reason for e in mapo.excluded)
    gangnam = match_district(programs, YOUTH, GANGNAM)
    assert 1 in {p.program_id for p in gangnam.chosen}
    assert [e.program.program_id for e in gangnam.excluded] == [2, 9]


def test_unverified_program_never_matches(t):
    programs = _programs(t)
    for district in (d["district_code"] for d in t["district"]):
        assert 10 not in {p.program_id for p in match_district(programs, YOUTH, district).chosen}


def test_over_40_gets_only_unrestricted_programs(t):
    older = Applicant(age=60, industry_code="CS200001", industry_category="서비스업")
    result = match_district(_programs(t), older, JONGNO)
    assert sorted(p.program_id for p in result.chosen) == [4, 11]
    assert result.support_fund_max == 6_000_000


# ── 6 지표 · 예측 필수 경우 ──────────────────────────────────


def _recent_store_avg(t, dong, industry) -> float:
    quarters = sorted({r["year_quarter"] for r in t["store_quarterly"]})[-4:]
    values = [
        r["store_count"]
        for r in t["store_quarterly"]
        if r["dong_code"] == dong and r["industry_code"] == industry and r["year_quarter"] in quarters
    ]
    return sum(values) / len(values)


def test_no_store_dong(t):
    assert not [r for r in t["store_quarterly"] if r["dong_code"] == CHEONGUN]
    assert not [r for r in t["prediction"] if r["dong_code"] == CHEONGUN]
    assert [r for r in t["population_quarterly"] if r["dong_code"] == CHEONGUN]  # 인구는 있다


def test_no_prediction_cases(t):
    store_combos = {(r["dong_code"], r["industry_code"]) for r in t["store_quarterly"]}
    pred_combos = {(r["dong_code"], r["industry_code"]) for r in _xgb(t)}
    missing = store_combos - pred_combos
    hoegi = {(HOEGI, i["industry_code"]) for i in t["industry"]}
    assert missing == hoegi | NO_PRED_COMBOS  # 점포는 있는데 예측이 없는 조합이 정확히 이것뿐
    sales_combos = {(r["dong_code"], r["industry_code"]) for r in t["sales_quarterly"]}
    assert sales_combos == pred_combos  # 예측은 매출 기록이 있는 조합에만(6.5)


def test_sample_short_cases_have_predictions(t):
    short = {(BANGHAK3, i["industry_code"]) for i in t["industry"]} | {(GONGNEUNG2, "CS200002")}
    pred_combos = {(r["dong_code"], r["industry_code"]) for r in _xgb(t)}
    store_combos = {(r["dong_code"], r["industry_code"]) for r in t["store_quarterly"]}
    for combo in store_combos:
        is_short = _recent_store_avg(t, *combo) < 5
        assert is_short == (combo in short), combo
    assert short <= pred_combos


def test_growth_null_and_low_cases(t):
    for p in t["prediction"]:
        expected_null = p["industry_code"] == "CS200002" or p["dong_code"] == MANGWON1
        assert (p["growth_rate"] is None) == expected_null
        if p["growth_rate"] is None:
            assert p["growth_confidence"] is None and p["growth_percentile"] is None
        else:
            assert p["growth_confidence"] == ("낮음" if p["dong_code"] in (GASAN, BANGHAK3) else "높음")


def test_intervals_are_ordered(t):
    for p in t["prediction"]:
        assert p["sales_p10"] <= p["sales_p50"] <= p["sales_p90"]
        assert p["survival_p10"] <= p["survival_p50"] <= p["survival_p90"]


def test_extra_model_rows(t):
    mlp = [p for p in t["prediction"] if p["model_version"] == "mlp_v1"]
    assert len(mlp) == 52 and {p["industry_code"] for p in mlp} == {"CS100001"}


def test_summary_cases(t):
    keys = {(s["dong_code"], s["industry_code"]) for s in t["summary_cache"]}
    cs1 = {(p["dong_code"], "CS100001") for p in _xgb(t) if p["industry_code"] == "CS100001"}
    assert keys == cs1 - {("11620901", "CS100001"), ("11620902", "CS100001")}


# ── 7 점수는 common.scoring으로 ──────────────────────────────


def _q4(value):
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def test_scores_match_independent_common_calculation(t):
    groups = {}
    for p in t["prediction"]:
        groups.setdefault((p["industry_code"], p["model_version"]), []).append(p)
    assert len(groups) == 8  # 7업종 × xgb_v1 + CS100001 × mlp_v1
    for rows in groups.values():
        population = {col: [r[col] for r in rows] for col in ("sales_p50", "survival_p50", "growth_rate")}
        for r in rows:
            pct = {
                "sales": _q4(percent_rank(population["sales_p50"], r["sales_p50"])),
                "survival": _q4(percent_rank(population["survival_p50"], r["survival_p50"])),
                "growth": _q4(percent_rank(population["growth_rate"], r["growth_rate"])),
            }
            assert (r["sales_percentile"], r["survival_percentile"], r["growth_percentile"]) == (
                pct["sales"], pct["survival"], pct["growth"],
            )
            expected = total_score({k: None if v is None else float(v) for k, v in pct.items()}).total_score
            assert r["total_score"] == Decimal(str(expected))
        ordered = sorted(rows, key=lambda r: r["score_rank"])
        assert [r["score_rank"] for r in ordered] == list(range(1, len(rows) + 1))
        assert all(a["total_score"] >= b["total_score"] for a, b in zip(ordered, ordered[1:], strict=False))


def test_growth_null_industry_uses_50_50(t):
    for p in _xgb(t):
        if p["industry_code"] == "CS200002":
            raw = (p["sales_percentile"] * 50 + p["survival_percentile"] * 50).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
            assert p["total_score"] == raw


# ── 8 · 9 결정성 · 더미 표식 ─────────────────────────────────


def test_build_is_deterministic(fx):
    assert build().tables == fx.tables


def test_dummy_markers(t):
    assert all(m["market_name"].startswith("[더미]") for m in t["commercial_market"])
    assert all(s["summary_text"].startswith("[더미 요약]") for s in t["summary_cache"])
    assert all(r["checked_by"] == "더미검수자" for r in t["program_verification"])
    for path in DEV_DIR.glob("*.json"):
        assert path.name.startswith("dummy_")
        assert "더미" in path.read_text(encoding="utf-8")


def test_nothing_in_seed_dir():
    seed = Path(DEV_DIR).parent / "seed"
    assert not seed.exists() or not any(p.name.startswith("dummy") for p in seed.iterdir())
