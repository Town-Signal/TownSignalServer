"""② 자치구별 가용예산 산출 · rec_id 발급 (전체 명세 7.1 ~ 7.5 · 8.2 ② · 8.6).

계산은 common(certificate_normalizer · evaluator · budget)이 한다. 여기서는 조회 → common 호출 →
DTO 조립 → 정렬 · 개수 세기 → recommendation 저장만 한다.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.errors import MESSAGES, AppError, field_error
from api.repositories import program_repository as program_repo
from api.repositories import recommendation_repository as rec_repo
from api.schemas.common import Notice
from api.schemas.program import (
    BudgetCalculationRequest,
    BudgetCalculationResponse,
    DistrictBudgetResult,
    ExcludedProgram,
    MatchedProgram,
)
from api.schemas.region import BudgetSummary
from common.budget import available_budget, judge, match_district, rent_cost, select_programs
from common.certificate_normalizer import Mapper, normalize
from common.constants import DEPOSIT_MULTIPLIER_DEFAULT, PASSED_EXCLUDED, PASSED_OK, PASSED_UNKNOWN
from common.evaluator import Applicant, Program

logger = logging.getLogger(__name__)

WARNING_MESSAGES = {
    "SUPPORT_PROGRAM_UNAVAILABLE": "지원사업 정보를 불러오지 못해 지원금 없이 계산했어요.",  # 8.1.1 예시 문구
    "PARTIAL_DISTRICT_ERROR": "일부 자치구는 지원사업을 계산하지 못해 지원금 없이 표시했어요.",
}


def _notice(code: str) -> Notice:
    return Notice(code=code, message=WARNING_MESSAGES[code])


def _to_program(row) -> Program:
    """support_program ORM 행 → common.evaluator.Program."""
    return Program(
        program_id=row.program_id,
        name=row.name,
        amount_max=row.amount_max,
        eligibility=row.eligibility,
        is_exclusive=row.is_exclusive,
        district_code=row.district_code,
        verified_by=row.verified_by,
        apply_end=row.apply_end,
    )


def _load_programs(session: Session) -> tuple[list[Program], bool]:
    """검수 공고를 읽는다. 조회가 실패하면 지원금 없이 계속한다 → (빈 목록, degraded=True)(7.4)."""
    try:
        with session.begin_nested():  # 실패해도 이 SAVEPOINT만 되돌리고 recommendation 저장은 계속한다
            rows = program_repo.list_matchable_programs(session)
    except SQLAlchemyError:
        logger.exception("지원사업 조회 실패 — 지원금 0으로 계산(degraded)")
        return [], True
    return [_to_program(row) for row in rows], False


def calculate(
    session: Session, req: BudgetCalculationRequest, mapper: Mapper | None
) -> tuple[BudgetCalculationResponse, list[Notice]]:
    industry = program_repo.get_industry(session, req.industry_code)
    if industry is None:  # 본문 안의 코드가 마스터에 없으면 UNKNOWN_CODE(8.1.1)
        raise AppError(
            "VALIDATION_ERROR",
            422,
            MESSAGES["VALIDATION_ERROR"],
            [field_error("industry_code", "UNKNOWN_CODE", req.industry_code)],
        )

    warnings: list[Notice] = []
    certificates = normalize(req.certificates, mapper)
    if certificates.unrecognized:
        names = ", ".join(certificates.unrecognized)
        warnings.append(
            Notice(
                code="CERTIFICATE_NOT_RECOGNIZED",
                message=f"표준 자격증 목록에서 찾지 못해 입력한 이름 그대로 계산했어요: {names}",
            )
        )

    programs, degraded = _load_programs(session)
    if degraded:
        warnings.append(_notice("SUPPORT_PROGRAM_UNAVAILABLE"))

    applicant = Applicant(
        age=req.age,
        career_years=req.career_years,
        capital=req.capital,
        certificates=certificates.certificates,
        industry_code=req.industry_code,
        industry_category=industry.category,
    )

    districts = program_repo.list_districts_with_rent(session)
    results: list[DistrictBudgetResult] = []
    partial_error = False
    for d in districts:  # 25개 구마다 따로 매칭한다(구 전용 사업 때문에 구마다 다르다, 7.4)
        reason = None
        try:
            match = match_district(programs, applicant, d.district_code)
        except Exception:
            # TODO(가정): 한 구만 계산에 실패하면 그 구만 지원금 0, 임대료 판정은 그대로 한다
            logger.exception("자치구 지원사업 계산 실패: %s", d.district_code)
            match, reason, partial_error = select_programs([]), "SUPPORT_DATA_ERROR", True

        available = available_budget(req.capital, match.support_fund_max)
        multiplier = d.deposit_multiplier or DEPOSIT_MULTIPLIER_DEFAULT
        cost = rent_cost(d.rent_per_sqm, req.target_area_sqm, multiplier)
        margin, passed = judge(available, cost)  # 임대료가 없으면 (None, "확인불가") — 탈락시키지 않는다
        if cost is None and reason is None:
            reason = "RENT_DATA_MISSING"

        results.append(
            DistrictBudgetResult(
                district_code=d.district_code,
                district_name=d.name,
                support_fund_max=match.support_fund_max,
                available_budget=available,
                estimated_rent_cost=None if cost is None else cost.estimated_rent_cost,
                budget_margin=margin,
                passed=passed,
                rent_confidence=d.confidence or "없음",
                # TODO(가정): 표시 순서는 program_id 오름차순
                matched_programs=[
                    MatchedProgram(
                        program_id=p.program_id,
                        name=p.name,
                        amount=p.amount_max,
                        is_exclusive=p.is_exclusive,
                        district_code=p.district_code,
                        apply_end=p.apply_end,
                    )
                    for p in sorted(match.chosen, key=lambda p: p.program_id)
                ],
                excluded_programs=[
                    ExcludedProgram(
                        program_id=e.program.program_id,
                        name=e.program.name,
                        amount=e.program.amount_max,
                        exclude_reason=e.reason,
                    )
                    for e in sorted(match.excluded, key=lambda e: e.program.program_id)
                ],
                monthly_rent=None if cost is None else cost.monthly_rent,
                rent_per_sqm=None if d.rent_per_sqm is None else float(d.rent_per_sqm),
                geo_code=d.geo_code,
                unavailable_reason=reason,
            )
        )
    if partial_error:
        warnings.append(_notice("PARTIAL_DISTRICT_ERROR"))

    results.sort(key=lambda r: (-r.available_budget, r.district_code))  # 2.3.1 랭킹 순서(8.2)
    passed_values = [r.passed for r in results]
    summary = BudgetSummary(
        passed_district_count=passed_values.count(PASSED_OK),
        eligible_district_count=passed_values.count(PASSED_OK) + passed_values.count(PASSED_UNKNOWN),
        excluded_district_count=passed_values.count(PASSED_EXCLUDED),
    )
    # TODO(가정): 기준 분기는 district_rent의 가장 최근 base_quarter, 없으면 null
    base_quarter = max((d.base_quarter for d in districts if d.base_quarter), default=None)

    snapshot = {
        "base_quarter": base_quarter,
        "own_capital": req.capital,
        "normalized_certificates": list(certificates.certificates),
        "degraded": degraded,
        "summary": summary.model_dump(mode="json"),
        "district_budgets": [r.model_dump(mode="json") for r in results],
    }
    input_condition = {
        **req.model_dump(mode="json"),
        "normalized_certificates": list(certificates.certificates),
        "industry_category": industry.category,
    }
    # Step 3(④)는 이 스냅샷을 재계산 없이 읽는다(3장 4번)
    rec_id = rec_repo.create_recommendation(session, input_condition, snapshot)
    session.commit()

    return BudgetCalculationResponse(rec_id=rec_id, **snapshot), warnings
