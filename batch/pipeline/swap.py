"""스테이징 → 한 트랜잭션 교체 (전체 명세 4.6 '표 교체 규칙' · 6.5).

스테이징 표에 먼저 적재한 뒤 한 트랜잭션 안에서 본 표를 갱신한다. 어느 단계든 실패하면 전부 되돌아가
직전 표가 그대로 남는다. prediction은 ON CONFLICT로 갱신하고 같은 트랜잭션에서 사라진 조합을 지우며,
갱신된 조합의 summary_cache도 같은 트랜잭션에서 지운다.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, column, table, text

from batch.pipeline.db import transaction
from batch.pipeline.scores import fill_scores
from common.constants import SERVING_MODEL_VERSION

PREDICTION_KEY = ("dong_code", "industry_code", "model_version")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class SwapResult:
    upserted: int  # 새로 넣거나 갱신한 행 수
    deleted_keys: list[tuple] = field(default_factory=list)  # scope 안에서 새 목록에 없어 지운 행의 키

    @property
    def deleted(self) -> int:
        return len(self.deleted_keys)


@dataclass(frozen=True)
class PredictionSwapResult(SwapResult):
    summaries_deleted: int = 0


def _check(*names: str) -> None:
    """SQL에 끼워 넣는 표 · 칼럼 이름은 코드에 적힌 식별자만 허용한다."""
    for name in names:
        if not _IDENTIFIER.match(name):
            raise ValueError(f"허용하지 않는 식별자: {name!r}")


def replace_rows(
    conn: Connection,
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
    key_columns: Sequence[str],
    scope: Mapping[str, Any],
    extra_set: Mapping[str, str] | None = None,
) -> SwapResult:
    """rows로 table_name의 scope 범위를 통째로 교체한다.

    1) 임시 스테이징 표(LIKE 본 표)에 rows 적재 2) INSERT … ON CONFLICT (key) DO UPDATE
    3) scope 안에서 스테이징에 없는 키 삭제 4) 스테이징 삭제 — 모두 한 트랜잭션(또는 SAVEPOINT).
    extra_set은 갱신 때 함께 바꿀 칼럼 → SQL 식(예: {"computed_at": "now()"}). 코드 상수만 넣는다.
    """
    if not rows:
        # TODO(가정): 빈 목록으로 scope 전체를 지우는 실수를 막는다. 비우려면 따로 DELETE한다
        raise ValueError("교체할 행이 없다")
    columns = list(rows[0])
    if any(list(r) != columns for r in rows):
        raise ValueError("모든 행의 칼럼이 같아야 한다")
    extra_set = dict(extra_set or {})
    _check(table_name, *columns, *key_columns, *scope, *extra_set)
    staging = f"{table_name}_staging"

    cols = ", ".join(columns)
    keys = ", ".join(key_columns)
    updates = [f"{c} = EXCLUDED.{c}" for c in columns if c not in key_columns]
    updates += [f"{c} = {expr}" for c, expr in extra_set.items()]
    scope_sql = " AND ".join(f"t.{k} = :scope_{k}" for k in scope)
    same_key = " AND ".join(f"s.{k} = t.{k}" for k in key_columns)

    with transaction(conn):
        conn.execute(text(f"DROP TABLE IF EXISTS {staging}"))
        conn.execute(text(f"CREATE TEMP TABLE {staging} (LIKE {table_name} INCLUDING DEFAULTS)"))
        conn.execute(table(staging, *(column(c) for c in columns)).insert(), [dict(r) for r in rows])
        upserted = conn.execute(
            text(
                f"INSERT INTO {table_name} ({cols}) SELECT {cols} FROM {staging} "
                f"ON CONFLICT ({keys}) DO UPDATE SET {', '.join(updates)}"
            )
        ).rowcount
        deleted = conn.execute(
            text(
                f"DELETE FROM {table_name} t WHERE {scope_sql} "
                f"AND NOT EXISTS (SELECT 1 FROM {staging} s WHERE {same_key}) RETURNING {keys}"
            ),
            {f"scope_{k}": v for k, v in scope.items()},
        ).all()
        conn.execute(text(f"DROP TABLE {staging}"))
    return SwapResult(upserted=upserted, deleted_keys=[tuple(r) for r in deleted])


def replace_predictions(
    conn: Connection, rows: Sequence[Mapping[str, Any]], model_version: str
) -> PredictionSwapResult:
    """한 model_version의 prediction을 통째로 교체한다(6.5).

    - 적재 직전에 fill_scores(common.scoring)로 백분위 · total_score · score_rank를 채운다(판정 30)
    - ON CONFLICT 갱신 + 새 목록에 없는 조합 삭제, 갱신 행의 computed_at = now()
    - 같은 트랜잭션에서 갱신 · 삭제된 (dong_code, industry_code)의 summary_cache를 지운다(4.6)

    TODO(가정): 교체 단위는 model_version 전체다(업종 일부만 다시 넣는 부분 교체는 없음).
    summary_cache는 SERVING_MODEL_VERSION을 갈아끼울 때만 지운다(비교 모델 적재는 서빙 요약과 무관).
    """
    if not rows:
        raise ValueError("교체할 예측 행이 없다")
    if any(r["model_version"] != model_version for r in rows):
        raise ValueError(f"모든 행의 model_version이 {model_version!r}이어야 한다")

    scored = fill_scores(list(rows))
    with transaction(conn):
        result = replace_rows(
            conn,
            "prediction",
            scored,
            PREDICTION_KEY,
            {"model_version": model_version},
            {"computed_at": "now()"},
        )
        summaries_deleted = 0
        if model_version == SERVING_MODEL_VERSION:
            pairs = {(r["dong_code"], r["industry_code"]) for r in scored}
            pairs |= {(dong, industry) for dong, industry, _ in result.deleted_keys}
            dongs, industries = zip(*sorted(pairs), strict=True)
            summaries_deleted = conn.execute(
                text(
                    "DELETE FROM summary_cache WHERE (dong_code, industry_code) IN "
                    "(SELECT d, i FROM unnest(CAST(:dongs AS text[]), CAST(:industries AS text[])) "
                    "AS p(d, i))"
                ),
                {"dongs": list(dongs), "industries": list(industries)},
            ).rowcount
    return PredictionSwapResult(
        upserted=result.upserted, deleted_keys=result.deleted_keys, summaries_deleted=summaries_deleted
    )
