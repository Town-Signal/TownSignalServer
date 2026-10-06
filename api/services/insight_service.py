"""⑪ 상권 현황 · ⑫ 예측 · ⑬ 요약 · ⑱ 랭킹 · ⑭ 비교 (전체 명세 8.4 · 8.6 · 판정 22 · 30 · 31).

- prediction은 SERVING_MODEL_VERSION만 읽고, 백분위 · total_score · score_rank는 저장값을 옮기기만 한다.
- 월 매출 · 점포당 매출 · 임대비용 · 표본 판정 · 점포 수준은 common 함수로 구한다.
- 결측은 0이 아니라 null이다.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from api.errors import MESSAGES, AppError, field_error
from api.models import PopulationQuarterly, SalesQuarterly, StoreQuarterly
from api.repositories import insight_repository as repo
from api.repositories import program_repository as program_repo
from api.repositories import recommendation_repository as rec_repo
from api.schemas.region import (
    AnalyticsResponse,
    CompareItem,
    GrowthInfo,
    IndustryShare,
    PopulationLatest,
    PopulationPoint,
    PredictionResponse,
    RankingItem,
    RankingResponse,
    RegionCompareRequest,
    RegionCompareResponse,
    RentInfo,
    SalesPoint,
    ScoreBreakdown,
    SummaryResponse,
)
from common.budget import rent_cost
from common.constants import DEFAULT_AREA_SQM, DEPOSIT_MULTIPLIER_DEFAULT, SERVING_MODEL_VERSION
from common.scoring import applied_weights, data_status, store_level
from common.units import per_store_amount, quarterly_to_monthly

TREND_QUARTERS = 8  # 8.6 — 최근 8개 분기
TOP_INDUSTRIES = 5  # ⑪ 업종 분포 상위 5개 + 기타
SPACE_STANDARD_BREAK = "20241"
TIME_SLOTS = {
    "00-06": "time_00_06",
    "06-11": "time_06_11",
    "11-14": "time_11_14",
    "14-17": "time_14_17",
    "17-21": "time_17_21",
    "21-24": "time_21_24",
}
AGE_COLUMNS = ("age_10", "age_20", "age_30", "age_40", "age_50", "age_60")


def _float(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _dong_or_404(session: Session, dong_code: str):
    dong = repo.get_dong(session, dong_code)
    if dong is None:
        raise AppError("DONG_NOT_FOUND", 404, "찾을 수 없는 동네예요.")
    return dong


def _industry_in_query(session: Session, industry_code: str):
    """쿼리 · 본문 안의 업종 코드가 마스터에 없으면 422 UNKNOWN_CODE(8.1.1)."""
    industry = program_repo.get_industry(session, industry_code)
    if industry is None:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            MESSAGES["VALIDATION_ERROR"],
            [field_error("industry_code", "UNKNOWN_CODE", industry_code)],
        )
    return industry


def market_base_quarter(session: Session) -> str | None:
    """상권 응답(⑪ ⑫ ⑭ ⑱)의 meta.base_quarter.

    TODO(가정): 상권 지표(sales_quarterly)의 최신 year_quarter(예: 20252)로 둔다.
    """
    quarters = repo.latest_quarters(session, SalesQuarterly, 1)
    return quarters[-1] if quarters else None


def _latest_store_quarter(session: Session) -> str | None:
    quarters = repo.latest_quarters(session, StoreQuarterly, 1)
    return quarters[-1] if quarters else None


def _rent_info(session: Session, district_code: str, district_name: str, area_sqm: float) -> RentInfo:
    rent = repo.get_district_rent(session, district_code)
    rent_per_sqm = rent.rent_per_sqm if rent else None
    multiplier = rent.deposit_multiplier if rent else DEPOSIT_MULTIPLIER_DEFAULT
    cost = rent_cost(rent_per_sqm, area_sqm, multiplier)  # 임대료가 없으면 None(0으로 채우지 않음)
    return RentInfo(
        basis=f"{district_name} 평균",
        rent_per_sqm=_float(rent_per_sqm),
        area_sqm=area_sqm,
        monthly_rent=None if cost is None else cost.monthly_rent,
        estimated_rent_cost=None if cost is None else cost.estimated_rent_cost,
        confidence=rent.confidence if rent else "없음",
        base_quarter=rent.base_quarter if rent else None,
    )


def _score_breakdown(p) -> ScoreBreakdown:
    percentiles = {
        "sales": _float(p.sales_percentile),
        "survival": _float(p.survival_percentile),
        "growth": _float(p.growth_percentile),
    }
    return ScoreBreakdown(
        sales_percentile=percentiles["sales"],
        survival_percentile=percentiles["survival"],
        growth_percentile=percentiles["growth"],
        applied_weights=applied_weights(percentiles),  # 가중치만 구한다. total_score는 저장값
        total_score=_float(p.total_score),
    )


# ── ⑪ 상권 현황 ─────────────────────────────────────────────


def _distribution(session: Session, dong_code: str, selected: str | None):
    quarter = _latest_store_quarter(session)
    rows = repo.store_counts(session, dong_code, quarter) if quarter else []
    if not rows:
        return None, None
    total = sum(r.store_count for r in rows)
    shown = list(rows[:TOP_INDUSTRIES])
    if selected and selected not in {r.industry_code for r in shown}:
        shown += [r for r in rows if r.industry_code == selected]  # 선택 업종은 5위 밖이어도 넣는다(8.6)
    shown_codes = {r.industry_code for r in shown}
    rest = sum(r.store_count for r in rows if r.industry_code not in shown_codes)

    def share(count: int) -> float:
        return round(count / total, 4)  # TODO(가정): 소수 4자리

    items = [
        IndustryShare(
            industry_code=r.industry_code,
            name=r.name,
            store_count=r.store_count,
            share=share(r.store_count),
            is_selected=r.industry_code == selected,
        )
        for r in shown
    ]
    if rest:  # TODO(가정): 기타가 0개면 넣지 않는다
        items.append(IndustryShare(industry_code=None, name="기타", store_count=rest, share=share(rest)))
    return items, total


def _sales_trend(session: Session, dong_code: str, industry_code: str | None) -> list[SalesPoint]:
    quarters = repo.latest_quarters(session, SalesQuarterly, TREND_QUARTERS)
    series = repo.sales_series(session, dong_code, industry_code, quarters)
    points = []
    for quarter in quarters:
        amount, count = series.get(quarter, (None, None))  # 그 동에 없는 분기는 빈 값(화면 '빈 막대')
        per_store = per_store_amount(amount, count)
        points.append(
            SalesPoint(
                year_quarter=quarter,
                amount=amount,
                store_count=count,
                per_store_amount=per_store,
                per_store_monthly_amount=quarterly_to_monthly(per_store),
            )
        )
    return points


def _population(session: Session, dong_code: str):
    quarters = repo.latest_quarters(session, PopulationQuarterly, TREND_QUARTERS)
    rows = repo.population_rows(session, dong_code, quarters)
    if not rows:
        return None, None
    trend = [PopulationPoint(year_quarter=r.year_quarter, total=r.total) for r in rows]
    latest = rows[-1]
    slots = {label: getattr(latest, column) for label, column in TIME_SLOTS.items()}
    present = {label: v for label, v in slots.items() if v is not None}
    peak = max(present, key=present.get) if present else None  # 동률이면 앞 구간
    return trend, PopulationLatest(
        male=latest.male,
        female=latest.female,
        **{c: getattr(latest, c) for c in AGE_COLUMNS},
        time_slots=slots,
        peak_time_slot=peak,
    )


def analytics(
    session: Session, dong_code: str, industry_code: str | None, area_sqm: float
) -> AnalyticsResponse:
    dong = _dong_or_404(session, dong_code)
    if industry_code is not None:
        _industry_in_query(session, industry_code)

    latest_quarter = repo.latest_sales_quarter_of_dong(session, dong_code)
    has_sales = latest_quarter is not None  # 매출 기록이 하나도 없으면 '조회 불가'(상권분석 대상 아님, 가정)
    distribution, total_stores = _distribution(session, dong_code, industry_code)
    population_trend, population_latest = _population(session, dong_code)

    growth = None
    if industry_code is not None:
        p = repo.get_prediction(session, dong_code, industry_code, SERVING_MODEL_VERSION)
        growth = GrowthInfo(
            growth_rate=_float(p.growth_rate) if p else None,
            growth_confidence=p.growth_confidence if p else None,
        )

    return AnalyticsResponse(
        dong_code=dong.dong_code,
        dong_name=dong.name,
        district_code=dong.district_code,
        district_name=dong.district_name,
        latest_quarter=latest_quarter,
        data_status="정상" if has_sales else "조회 불가",
        total_store_count=total_stores,
        industry_distribution=distribution,
        sales_trend=_sales_trend(session, dong_code, industry_code) if has_sales else None,
        space_standard_break=SPACE_STANDARD_BREAK,
        rent=_rent_info(session, dong.district_code, dong.district_name, area_sqm),
        population_trend=population_trend,
        population_latest=population_latest,
        growth=growth,
    )


# ── ⑫ 예측 ──────────────────────────────────────────────────


def prediction(session: Session, dong_code: str, industry_code: str) -> PredictionResponse:
    dong = _dong_or_404(session, dong_code)
    if program_repo.get_industry(session, industry_code) is None:  # 경로의 업종 코드 → 404(8.1.1)
        raise AppError("INDUSTRY_NOT_FOUND", 404, "찾을 수 없는 업종이에요. 업종을 다시 선택해 주세요.")

    p = repo.get_prediction(session, dong_code, industry_code, SERVING_MODEL_VERSION)
    store_avg = rec_repo.recent_store_averages(session, industry_code, [dong_code]).get(dong_code)
    counts = repo.industry_store_counts(session, industry_code, _latest_store_quarter(session))
    latest_count = counts.get(dong_code)
    return PredictionResponse(
        dong_code=dong_code,
        industry_code=industry_code,
        model_version=SERVING_MODEL_VERSION,
        sales_quarterly_p10=p.sales_p10 if p else None,
        sales_quarterly_p50=p.sales_p50 if p else None,
        sales_quarterly_p90=p.sales_p90 if p else None,
        sales_monthly_p10=quarterly_to_monthly(p.sales_p10) if p else None,
        sales_monthly_p50=quarterly_to_monthly(p.sales_p50) if p else None,
        sales_monthly_p90=quarterly_to_monthly(p.sales_p90) if p else None,
        survival_p10=_float(p.survival_p10) if p else None,
        survival_p50=_float(p.survival_p50) if p else None,
        survival_p90=_float(p.survival_p90) if p else None,
        growth_rate=_float(p.growth_rate) if p else None,
        growth_confidence=p.growth_confidence if p else None,
        store_count_avg=store_avg,
        data_status=data_status(p is not None, store_avg),  # 예측 행이 없으면 200 + '예측 불가'
        computed_at=p.computed_at if p else None,
        total_score=_float(p.total_score) if p else None,
        score_breakdown=_score_breakdown(p) if p else None,
        store_count_latest=latest_count,
        store_level=store_level(counts.values(), latest_count),
        geo_code=dong.geo_code,
    )


# ── ⑬ 요약 ──────────────────────────────────────────────────


def summary(session: Session, dong_code: str, industry_code: str) -> SummaryResponse:
    """summary_cache 조회만 한다. 없으면 null — 요청 경로에서 LLM을 부르지 않는다(판정 9)."""
    _dong_or_404(session, dong_code)
    _industry_in_query(session, industry_code)
    cached = repo.get_summary(session, dong_code, industry_code)
    return SummaryResponse(
        dong_code=dong_code,
        industry_code=industry_code,
        summary_text=cached.summary_text if cached else None,
        model_name=cached.model_name if cached else None,
        generated_at=cached.generated_at if cached else None,
    )


# ── ⑱ 랭킹 ──────────────────────────────────────────────────


def rankings(session: Session, industry_code: str, limit: int) -> RankingResponse:
    """전체 행정동. total_score↓(예측 불가는 맨 뒤) → dong_code↑. rank는 저장된 score_rank 그대로."""
    industry = _industry_in_query(session, industry_code)
    dongs = repo.list_dongs(session)
    predictions = repo.predictions_by_dong(session, industry_code, SERVING_MODEL_VERSION)
    counts = repo.industry_store_counts(session, industry_code, _latest_store_quarter(session))
    averages = rec_repo.recent_store_averages(session, industry_code, list(predictions))

    items = []
    for d in dongs:
        p = predictions.get(d.dong_code)
        count = counts.get(d.dong_code)
        items.append(
            RankingItem(
                rank=p.score_rank if p else None,
                dong_code=d.dong_code,
                dong_name=d.name,
                district_code=d.district_code,
                district_name=d.district_name,
                geo_code=d.geo_code,
                total_score=_float(p.total_score) if p else None,
                survival_p50=_float(p.survival_p50) if p else None,
                sales_monthly_p50=quarterly_to_monthly(p.sales_p50) if p else None,
                growth_rate=_float(p.growth_rate) if p else None,
                store_count_latest=count,
                store_level=store_level(counts.values(), count),
                data_status=data_status(p is not None, averages.get(d.dong_code)),
            )
        )
    items.sort(key=lambda i: (i.total_score is None, -(i.total_score or 0), i.dong_code))
    # TODO(가정): 예측 없는 동도 목록 끝에 넣고, total은 limit 전 전체 행정동 수
    return RankingResponse(
        industry_code=industry_code, industry_name=industry.name, items=items[:limit], total=len(items)
    )


# ── ⑭ 비교 ──────────────────────────────────────────────────


def compare(session: Session, req: RegionCompareRequest) -> RegionCompareResponse:
    industry = program_repo.get_industry(session, req.industry_code)
    dongs = {d.dong_code: d for d in repo.list_dongs(session, req.dong_codes)}
    errors = [
        field_error(f"dong_codes.{i}", "UNKNOWN_CODE", code)
        for i, code in enumerate(req.dong_codes)
        if code not in dongs
    ]  # 본문 안의 없는 코드는 422 UNKNOWN_CODE(8.6)
    if industry is None:
        errors.append(field_error("industry_code", "UNKNOWN_CODE", req.industry_code))
    if errors:
        raise AppError("VALIDATION_ERROR", 422, MESSAGES["VALIDATION_ERROR"], errors)

    budgets: dict[str, dict] = {}
    area = DEFAULT_AREA_SQM
    if req.rec_id is not None:  # rec_id를 줬는데 없으면 404(8.1.1 예외)
        rec = rec_repo.get_recommendation(session, req.rec_id)
        if rec is None:
            raise AppError("REC_NOT_FOUND", 404, "추천 결과를 찾을 수 없어요. 다시 추천받아 주세요.")
        budgets = {d["district_code"]: d for d in rec.calculated_budgets["district_budgets"]}
        area = rec.input_condition.get("target_area_sqm", DEFAULT_AREA_SQM)

    counts = repo.industry_store_counts(session, req.industry_code, _latest_store_quarter(session))
    averages = rec_repo.recent_store_averages(session, req.industry_code, req.dong_codes)
    population_quarter = repo.latest_quarters(session, PopulationQuarterly, 1)
    populations = repo.latest_population_totals(
        session, req.dong_codes, population_quarter[-1] if population_quarter else None
    )

    items = []
    for code in req.dong_codes:  # 요청 순서 그대로
        d = dongs[code]
        p = repo.get_prediction(session, code, req.industry_code, SERVING_MODEL_VERSION)
        rent = _rent_info(session, d.district_code, d.district_name, area)
        budget = budgets.get(d.district_code)
        items.append(
            CompareItem(
                dong_code=code,
                dong_name=d.name,
                district_code=d.district_code,
                district_name=d.district_name,
                sales_quarterly_p50=p.sales_p50 if p else None,
                sales_quarterly_range=(p.sales_p10, p.sales_p90) if p else None,
                sales_monthly_p50=quarterly_to_monthly(p.sales_p50) if p else None,
                sales_monthly_range=(
                    (quarterly_to_monthly(p.sales_p10), quarterly_to_monthly(p.sales_p90)) if p else None
                ),
                survival_p50=_float(p.survival_p50) if p else None,
                survival_range=(_float(p.survival_p10), _float(p.survival_p90)) if p else None,
                growth_rate=_float(p.growth_rate) if p else None,
                total_score=_float(p.total_score) if p else None,
                rent_per_sqm=rent.rent_per_sqm,
                estimated_rent_cost=rent.estimated_rent_cost,
                rent_confidence=rent.confidence,
                budget_margin=budget["budget_margin"] if budget else None,
                passed=budget["passed"] if budget else None,
                store_count=counts.get(code),
                total_population=populations.get(code),
                data_status=data_status(p is not None, averages.get(code)),
                geo_code=d.geo_code,
            )
        )
    return RegionCompareResponse(
        industry_code=req.industry_code, industry_name=industry.name, comparison_items=items
    )
