"""api/models가 db/schema.sql로 만든 실제 DB와 1:1인지 검사한다 (전체 명세 5.5).

카탈로그 비교: 테이블 · 칼럼 · 타입 · NULL 허용 · 기본값 유무 · PK · FK · UNIQUE · CHECK · 인덱스 · 뷰.
왕복 검사: 모델마다 넣고 다시 읽어 값이 그대로인지 본다.
"""

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, delete, select, text
from sqlalchemy.dialects import postgresql

from api.models import (
    VIEW_METADATA,
    Base,
    BusinessLicense,
    CommercialMarket,
    District,
    DistrictRent,
    Dong,
    Industry,
    PopulationQuarterly,
    Prediction,
    ProgramVerification,
    RecItem,
    Recommendation,
    SalesQuarterly,
    StoreQuarterly,
    SummaryCache,
    SupportProgram,
    v_extraction_accuracy,
)

DIALECT = postgresql.dialect()
TABLES = Base.metadata.tables


def _orm_type(column) -> str:
    """ORM 타입을 PostgreSQL format_type() 표기로 바꾼다. 예: VARCHAR(20) → character varying(20)."""
    t = column.type.compile(dialect=DIALECT).lower().replace(", ", ",")
    t = re.sub(r"^varchar\(", "character varying(", t)
    return re.sub(r"^char\(", "character(", t)


def _rows(conn, sql: str, **params):
    return conn.execute(text(sql), params).all()


@pytest.fixture(scope="module")
def catalog(db_engine):
    """public 스키마의 카탈로그를 한 번만 읽어 둔다."""
    with db_engine.connect() as conn:
        rels = dict(
            _rows(
                conn,
                "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')",
            )
        )
        columns = _rows(
            conn,
            "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, "
            "       (a.atthasdef OR a.attidentity <> '') AS has_default "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') AND a.attnum > 0 AND NOT a.attisdropped",
        )
        constraints = _rows(
            conn,
            "SELECT c.relname, k.conname, k.contype, "
            "       ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i) "
            "             JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n ORDER BY u.i), "
            "       f.relname, "
            "       ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY u(n, i) "
            "             JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n ORDER BY u.i), "
            "       k.confdeltype "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_class f ON f.oid = k.confrelid "
            "WHERE n.nspname = 'public'",
        )
        indexes = _rows(
            conn,
            "SELECT t.relname, i.relname, "
            "       ARRAY(SELECT pg_get_indexdef(x.indexrelid, k, true) "
            "             FROM generate_series(1, x.indnatts) k ORDER BY k), "
            "       am.amname "
            "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
            "JOIN pg_am am ON am.oid = i.relam JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'public' "
            "  AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = x.indexrelid)",
        )
        index_desc = {
            name
            for (name,) in _rows(
                conn,
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexdef LIKE '% DESC%'",
            )
        }
    return {
        "rels": rels,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "index_desc": index_desc,
    }


# ── 카탈로그 1:1 ─────────────────────────────────────────────


def test_table_and_view_sets_match(catalog):
    db_tables = {name for name, kind in catalog["rels"].items() if kind == "r"}
    db_views = {name for name, kind in catalog["rels"].items() if kind == "v"}
    assert set(TABLES) == db_tables
    assert len(db_tables) == 15
    assert set(VIEW_METADATA.tables) == db_views == {"v_extraction_accuracy"}


@pytest.mark.parametrize("table_name", sorted(TABLES))
def test_columns_match(catalog, table_name):
    db = {
        col: (typ, not notnull, has_default)
        for rel, col, typ, notnull, has_default in catalog["columns"]
        if rel == table_name
    }
    orm = {
        c.name: (_orm_type(c), c.nullable, c.server_default is not None or c.identity is not None)
        for c in TABLES[table_name].columns
    }
    assert orm == db


def test_view_columns_match(catalog):
    db = {col: typ for rel, col, typ, _, _ in catalog["columns"] if rel == "v_extraction_accuracy"}
    orm = {c.name: _orm_type(c) for c in v_extraction_accuracy.columns}
    assert orm == db


@pytest.mark.parametrize("table_name", sorted(TABLES))
def test_primary_keys_match(catalog, table_name):
    db = [cols for rel, _, kind, cols, *_ in catalog["constraints"] if rel == table_name and kind == "p"]
    assert db == [[c.name for c in TABLES[table_name].primary_key.columns]]


@pytest.mark.parametrize("table_name", sorted(TABLES))
def test_foreign_keys_match(catalog, table_name):
    deltype = {"a": None, "c": "CASCADE"}
    db = {
        (cols[0], ref_table, ref_cols[0], deltype[del_type])
        for rel, _, kind, cols, ref_table, ref_cols, del_type in catalog["constraints"]
        if rel == table_name and kind == "f"
    }
    orm = {
        (fk.parent.name, fk.column.table.name, fk.column.name, fk.ondelete)
        for fk in TABLES[table_name].foreign_keys
    }
    assert orm == db


@pytest.mark.parametrize("table_name", sorted(TABLES))
def test_unique_and_check_constraints_match(catalog, table_name):
    table = TABLES[table_name]
    db_unique = {
        tuple(cols) for rel, _, kind, cols, *_ in catalog["constraints"] if rel == table_name and kind == "u"
    }
    orm_unique = {(c.name,) for c in table.columns if c.unique} | {
        tuple(c.name for c in con.columns) for con in table.constraints if isinstance(con, UniqueConstraint)
    }
    assert orm_unique == db_unique

    db_checks = {name for rel, name, kind, *_ in catalog["constraints"] if rel == table_name and kind == "c"}
    orm_checks = {con.name for con in table.constraints if isinstance(con, CheckConstraint)}
    assert orm_checks == db_checks


@pytest.mark.parametrize("table_name", sorted(TABLES))
def test_indexes_match(catalog, table_name):
    db = {name: (cols, method) for rel, name, cols, method in catalog["indexes"] if rel == table_name}
    orm = {}
    for index in TABLES[table_name].indexes:
        cols = [getattr(e, "name", None) or e.element.name for e in index.expressions]
        orm[index.name] = (cols, index.dialect_options["postgresql"]["using"] or "btree")
    assert orm == db
    for index in TABLES[table_name].indexes:
        has_desc = any(not hasattr(e, "name") for e in index.expressions)
        assert has_desc == (index.name in catalog["index_desc"]), index.name


# ── 왕복 ─────────────────────────────────────────────────────

# 실제 코드와 겹치지 않는 시험용 코드. 트랜잭션은 테스트가 끝나면 롤백된다
GU, GU_NO_RENT, DONG, IND = "99001", "99002", "99001001", "CS999001"
VERIFY_FIELD = "test_only_field"  # 개발 DB에 이미 있는 검증 기록(age 등)과 섞이지 않게
ELIGIBILITY = {
    "and": [
        {"field": "age", "op": "<=", "value": 39},
        {
            "or": [
                {"field": "career_years", "op": ">=", "value": 2},
                {"field": "certificates", "op": "contains_any", "value": ["조리기능사"]},
            ]
        },
    ]
}


def _seed_masters(s):
    s.add_all(
        [
            District(district_code=GU, name="시험구", geo_code="99250"),
            District(district_code=GU_NO_RENT, name="시험구2", geo_code=None),
            Industry(industry_code=IND, name="시험업종", category="외식업"),
        ]
    )
    s.flush()
    s.add(Dong(dong_code=DONG, name="시험동", district_code=GU, geo_code="9925001"))
    s.flush()


def test_round_trip_masters_and_indicators(db_session):
    s = db_session
    _seed_masters(s)
    s.add_all(
        [
            SalesQuarterly(
                dong_code=DONG, industry_code=IND, year_quarter="20251", amount=90_000_000, txn_count=3_000
            ),
            StoreQuarterly(dong_code=DONG, industry_code=IND, year_quarter="20251", store_count=4),
            PopulationQuarterly(dong_code=DONG, year_quarter="20251", total=12_345, time_17_21=4_000),
            BusinessLicense(
                license_id="L-1", dong_code=DONG, industry_code=IND, open_date=date(2024, 3, 1)
            ),
        ]
    )
    s.commit()
    s.expunge_all()

    assert s.get(Dong, DONG).district_code == GU
    assert s.get(District, GU_NO_RENT).geo_code is None
    sales = s.get(SalesQuarterly, (DONG, IND, "20251"))
    assert (sales.amount, sales.weekday_amount) == (90_000_000, None)
    assert s.get(StoreQuarterly, (DONG, IND, "20251")).store_count == 4
    assert s.get(PopulationQuarterly, (DONG, "20251")).time_17_21 == 4_000
    assert s.get(BusinessLicense, "L-1").close_date is None


def test_round_trip_rent_keeps_null(db_session):
    s = db_session
    _seed_masters(s)
    s.add_all(
        [
            CommercialMarket(
                market_name="시험역", base_quarter="2026Q2", district_code=GU, rent_per_sqm=Decimal("28.50")
            ),
            DistrictRent(
                district_code=GU, base_quarter="2026Q2", rent_per_sqm=Decimal("28.50"), confidence="낮음"
            ),
            DistrictRent(
                district_code=GU_NO_RENT, base_quarter="2026Q2", rent_per_sqm=None, confidence="없음"
            ),
        ]
    )
    s.commit()
    s.expunge_all()

    rent = s.get(DistrictRent, GU)
    assert rent.rent_per_sqm == Decimal("28.50")
    assert (rent.deposit_multiplier, rent.market_count) == (Decimal("15.00"), 0)  # DB 기본값
    assert s.get(DistrictRent, GU_NO_RENT).rent_per_sqm is None  # 결측은 0이 아니라 NULL
    assert s.get(CommercialMarket, ("시험역", "2026Q2")).district_code == GU


def test_round_trip_programs_and_view(db_session):
    s = db_session
    _seed_masters(s)
    program = SupportProgram(
        name="시험 사업", amount_max=20_000_000, district_code=GU, eligibility=ELIGIBILITY
    )
    s.add(program)
    s.flush()
    s.add_all(
        [
            ProgramVerification(program_id=program.program_id, field_name=VERIFY_FIELD, is_match=True),
            ProgramVerification(program_id=program.program_id, field_name=VERIFY_FIELD, is_match=False),
        ]
    )
    s.commit()
    program_id = program.program_id
    s.expunge_all()

    got = s.get(SupportProgram, program_id)
    assert got.eligibility == ELIGIBILITY
    assert (got.is_exclusive, got.verified_by) == (False, None)
    assert got.district_code == GU
    row = s.execute(
        select(v_extraction_accuracy).where(v_extraction_accuracy.c.field_name == VERIFY_FIELD)
    ).one()
    assert (row.checked_n, row.accuracy_pct) == (2, Decimal("50.0"))


def test_round_trip_prediction_and_summary(db_session):
    s = db_session
    _seed_masters(s)
    s.add_all(
        [
            Prediction(
                dong_code=DONG,
                industry_code=IND,
                model_version="xgb_v1",
                survival_p10=Decimal("30.0"),
                survival_p50=Decimal("42.5"),
                survival_p90=Decimal("60.0"),
                sales_p10=60_000_000,
                sales_p50=90_000_000,
                sales_p90=120_000_000,
                growth_rate=None,
                sales_percentile=Decimal("0.8800"),
                survival_percentile=Decimal("0.7500"),
                total_score=Decimal("81.5"),
                score_rank=1,
            ),
            SummaryCache(dong_code=DONG, industry_code=IND, summary_text="시험 요약"),
        ]
    )
    s.commit()
    s.expunge_all()

    pred = s.get(Prediction, (DONG, IND, "xgb_v1"))
    assert pred.sales_p50 == 90_000_000
    assert (pred.survival_p50, pred.total_score) == (Decimal("42.5"), Decimal("81.5"))
    assert pred.growth_rate is None and pred.growth_percentile is None
    assert pred.computed_at is not None
    assert s.get(SummaryCache, (DONG, IND)).generated_at is not None


def test_round_trip_recommendation_uuid_and_cascade(db_session):
    s = db_session
    _seed_masters(s)
    rec = Recommendation(
        input_condition={"age": 27, "industry_code": IND},
        calculated_budgets={"district_budgets": [{"district_code": GU, "passed": "통과"}]},
    )
    s.add(rec)
    s.flush()
    assert isinstance(rec.rec_id, uuid.UUID) and rec.rec_id.version == 4  # DB의 gen_random_uuid()
    s.add(RecItem(rec_id=rec.rec_id, rank_no=1, dong_code=DONG, budget_margin=None))
    s.commit()
    rec_id = rec.rec_id
    s.expunge_all()

    got = s.get(Recommendation, rec_id)
    assert got.calculated_budgets["district_budgets"][0]["passed"] == "통과"
    assert got.created_at is not None
    assert s.get(RecItem, (rec_id, 1)).budget_margin is None

    s.execute(delete(Recommendation).where(Recommendation.rec_id == rec_id))
    s.commit()
    assert s.scalar(select(RecItem).where(RecItem.rec_id == rec_id)) is None  # ON DELETE CASCADE
