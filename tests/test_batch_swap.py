"""batch.pipeline.swap — 스테이징 → 한 트랜잭션 교체(명세 4.6 표 교체 규칙 · 6.5)."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from batch.pipeline.scores import fill_scores
from batch.pipeline.swap import replace_predictions, replace_rows

INPUT_COLUMNS = (
    "dong_code, industry_code, model_version, survival_p10, survival_p50, survival_p90, "
    "sales_p10, sales_p50, sales_p90, growth_rate, growth_confidence"
)
SCORE_COLUMNS = ("sales_percentile", "survival_percentile", "growth_percentile", "total_score", "score_rank")
SILLIM, NAKSEONGDAE, CHEONGUN = "11620901", "11620902", "11110901"


@pytest.fixture
def conn(dummy_db):
    savepoint = dummy_db.begin_nested()
    yield dummy_db
    if savepoint.is_active:
        savepoint.rollback()


def input_rows(conn, model_version="xgb_v1") -> list[dict]:
    """모델이 만든 것처럼 점수 칼럼 없이 예측값만 가진 행 목록."""
    rows = conn.execute(
        text(
            f"SELECT {INPUT_COLUMNS} FROM prediction WHERE model_version = :m "
            "ORDER BY dong_code, industry_code"
        ),
        {"m": model_version},
    ).mappings()
    return [dict(r) for r in rows]


def snapshot(conn) -> tuple:
    prediction = conn.execute(text("SELECT * FROM prediction ORDER BY 1, 2, 3")).all()
    summaries = conn.execute(text("SELECT dong_code, industry_code FROM summary_cache ORDER BY 1, 2")).all()
    return prediction, summaries


def count(conn, sql: str, **params) -> int:
    return conn.execute(text(sql), params).scalar_one()


def test_replace_predictions_success(conn):
    rows = input_rows(conn)
    target = next(r for r in rows if r["dong_code"] == SILLIM and r["industry_code"] == "CS100001")
    target["sales_p50"], target["sales_p90"] = 900_000_000, 1_000_000_000  # 업종 안 최고 매출로
    conn.execute(text("UPDATE prediction SET computed_at = '2020-01-01' WHERE model_version = 'xgb_v1'"))

    result = replace_predictions(conn, rows, "xgb_v1")

    assert result.upserted == len(rows) and result.deleted == 0
    expected = {(r["dong_code"], r["industry_code"]): r for r in fill_scores(rows)}
    stored = conn.execute(text("SELECT * FROM prediction WHERE model_version = 'xgb_v1'")).mappings().all()
    assert len(stored) == len(rows)
    for row in stored:
        want = expected[(row["dong_code"], row["industry_code"])]
        assert tuple(row[c] for c in SCORE_COLUMNS) == tuple(want[c] for c in SCORE_COLUMNS)
        assert row["computed_at"].year > 2020  # 갱신 시각
    sillim = next(r for r in stored if r["dong_code"] == SILLIM and r["industry_code"] == "CS100001")
    assert (sillim["sales_p50"], sillim["sales_percentile"]) == (900_000_000, Decimal("1.0000"))


def test_removed_combinations_deleted(conn):
    before_mlp = count(conn, "SELECT count(*) FROM prediction WHERE model_version = 'mlp_v1'")
    rows = [r for r in input_rows(conn) if r["dong_code"] not in (SILLIM, NAKSEONGDAE)]
    result = replace_predictions(conn, rows, "xgb_v1")
    assert result.deleted == 14  # 두 동 × 7업종
    assert {key[0] for key in result.deleted_keys} == {SILLIM, NAKSEONGDAE}
    assert count(conn, "SELECT count(*) FROM prediction WHERE model_version = 'xgb_v1'") == len(rows)
    assert (
        count(
            conn,
            "SELECT count(*) FROM prediction WHERE dong_code = :d AND model_version = 'xgb_v1'",
            d=SILLIM,
        )
        == 0
    )
    assert (
        count(conn, "SELECT count(*) FROM prediction WHERE model_version = 'mlp_v1'") == before_mlp
    )  # 다른 모델


def test_summary_cache_cleared_for_changed_rows(conn):
    # 예측이 없는 조합의 요약(교체 대상 밖)은 남아야 한다
    conn.execute(
        text(
            "INSERT INTO summary_cache (dong_code, industry_code, summary_text) "
            "VALUES (:d, 'CS100001', '남는 요약')"
        ),
        {"d": CHEONGUN},
    )
    before = count(conn, "SELECT count(*) FROM summary_cache")
    rows = [r for r in input_rows(conn) if r["dong_code"] != SILLIM]
    result = replace_predictions(conn, rows, "xgb_v1")
    remaining = conn.execute(text("SELECT dong_code, industry_code FROM summary_cache")).all()
    assert [tuple(r) for r in remaining] == [(CHEONGUN, "CS100001")]
    assert result.summaries_deleted == before - 1


def test_non_serving_model_keeps_summaries(conn):
    before = snapshot(conn)[1]
    result = replace_predictions(conn, input_rows(conn, "mlp_v1"), "mlp_v1")
    assert result.summaries_deleted == 0
    assert snapshot(conn)[1] == before


def test_failure_rolls_back(conn):
    before = snapshot(conn)
    rows = input_rows(conn)
    rows[-1]["sales_p10"] = rows[-1]["sales_p50"] + 1  # p10 > p50 → CHECK 위반
    with pytest.raises(IntegrityError):
        replace_predictions(conn, rows, "xgb_v1")
    assert snapshot(conn) == before  # prediction · summary_cache 모두 호출 전과 같다


def test_mixed_model_versions_rejected(conn):
    before = snapshot(conn)
    rows = input_rows(conn)
    rows[0]["model_version"] = "mlp_v1"
    with pytest.raises(ValueError, match="model_version"):
        replace_predictions(conn, rows, "xgb_v1")
    with pytest.raises(ValueError, match="없다"):
        replace_predictions(conn, [], "xgb_v1")
    assert snapshot(conn) == before


def test_replace_rows_generic(conn):
    rows = [
        {
            "market_name": "[더미] 관악구 상권 1",
            "base_quarter": "2026Q2",
            "district_code": "11620",
            "rent_per_sqm": 31,
        },
        {
            "market_name": "[더미] 새 상권",
            "base_quarter": "2026Q2",
            "district_code": "11620",
            "rent_per_sqm": 40,
        },
    ]
    result = replace_rows(
        conn, "commercial_market", rows, ("market_name", "base_quarter"), {"base_quarter": "2026Q2"}
    )
    assert result.upserted == 2 and result.deleted == 46 - 1
    stored = dict(conn.execute(text("SELECT market_name, rent_per_sqm FROM commercial_market")).all())
    assert stored == {"[더미] 관악구 상권 1": Decimal("31.00"), "[더미] 새 상권": Decimal("40.00")}


def test_replace_rows_rejects_unsafe_identifier(conn):
    with pytest.raises(ValueError, match="식별자"):
        replace_rows(conn, "prediction; drop table x", [{"a": 1}], ("a",), {"a": 1})
